import threading
import unittest
from unittest.mock import patch

import main
from config.settings import Settings
from core.task_state import State


class AppTests(unittest.TestCase):
    def setUp(self):
        with patch("main.StealthHUD"):
            self.app = main.GhostAssistApp(Settings(_env_file=None))
        self.addCleanup(self.app.worker.shutdown)
        self.addCleanup(self.app.capture_worker.shutdown)

    def test_audio_toggle_blocks_screen_and_updates_hud(self):
        entered = threading.Event()

        def record(stop, cancel, seconds, max_bytes, device_name, vad_options=None):
            entered.set()
            if not stop.wait(2):
                raise TimeoutError
            return b"wav"

        with patch("main.record_wav", side_effect=record) as capture:
            with patch.object(
                self.app.llm, "transcribe", return_value="question"
            ) as transcribe:
                with patch.object(self.app, "show_transcript") as review:
                    self.app.audio()
                    self.assertTrue(entered.wait(2))
                    self.app.capture()
                    self.app.snip()
                    self.assertIs(self.app.tasks.state, State.RECORDING)
                    self.app.audio()
                    self.app.audio()
                    self.app.future.result(timeout=2)
                    self.app.poll()
                    self.assertIs(self.app.tasks.state, State.REVIEW)
                    review.assert_called_once()
                    self.assertEqual(transcribe.call_args.args[0], b"wav")
                    capture.assert_called_once()

    def test_one_hundred_sequential_mock_requests_keep_state_bounded(self):
        with (
            patch.object(self.app.llm, "capture_screen", return_value=b"jpg"),
            patch.object(
                self.app.llm,
                "generate",
                return_value="Вопрос: Вопрос?\nОтвет: Ответ",
            ) as generate,
        ):
            for _ in range(100):
                self.app.capture()
                self.app.future.result(timeout=2)
                self.app.poll()
                self.assertIs(self.app.tasks.state, State.IDLE)
            self.assertEqual(generate.call_count, 100)
            self.assertLessEqual(
                len(self.app.sessions.snapshot("TEST")[1]),
                self.app.settings.memory_depth,
            )

    def test_cancel_drops_result_even_if_worker_returns_success(self):
        entered = threading.Event()

        def generate(data, mime_type, prompt, publish, cancel, **kwargs):
            publish("partial")
            entered.set()
            if not cancel.wait(2):
                raise TimeoutError
            publish("late")
            return "stale success"

        with patch.object(self.app.llm, "capture_screen", return_value=b"jpg"):
            with patch.object(self.app.llm, "generate", side_effect=generate):
                self.app.capture()
                self.assertTrue(entered.wait(2))
                self.app.poll()
                self.assertIs(self.app.tasks.state, State.STREAMING)
                self.app.enqueue(self.app.cancel)
                self.app.poll()
                future = self.app.future
                if future:
                    future.result(timeout=2)
                    self.app.poll()
                self.assertIs(self.app.tasks.state, State.IDLE)
                displayed = str(self.app.hud.update_text.call_args_list)
                self.assertNotIn("late", displayed)
                self.assertNotIn("stale success", displayed)

    def test_overlay_failure_returns_to_idle(self):
        with patch("main.SnippingOverlay", side_effect=OSError):
            self.app.snip()
        self.assertIs(self.app.tasks.state, State.IDLE)
        self.app.hud.update_text.assert_called_with(
            "Не удалось открыть окно выделения.", "ОШИБКА"
        )

    def test_shutdown_waits_for_worker_cleanup(self):
        entered = threading.Event()
        release = threading.Event()

        def generate(data, mime_type, prompt, publish, cancel, **kwargs):
            entered.set()
            if not cancel.wait(2) or not release.wait(2):
                raise TimeoutError
            return None

        with patch.object(self.app.llm, "capture_screen", return_value=b"jpg"):
            with patch.object(self.app.llm, "generate", side_effect=generate):
                self.app.capture()
                self.assertTrue(entered.wait(2))
                future = self.app.future
                self.app.enqueue(self.app.close)
                self.app.poll()
                self.assertIs(self.app.tasks.state, State.CANCELLING)
                self.app.hud.root.destroy.assert_not_called()
                release.set()
                future.result(timeout=2)
                self.app.poll()
                self.app.hud.root.destroy.assert_called_once()

    def test_completed_response_enters_history_and_is_reused(self):
        with patch.object(self.app.llm, "capture_screen", return_value=b"jpg"):
            with patch.object(
                self.app.llm,
                "generate",
                return_value="Вопрос: Первый?\nОтвет: Первый ответ",
            ) as generate:
                self.app.capture()
                self.app.future.result(timeout=2)
                self.app.poll()
                self.app.capture()
                self.app.future.result(timeout=2)
                self.app.poll()
                history = generate.call_args.kwargs["history"]
                self.assertEqual(history[0].question, "Первый?")
                self.assertEqual(history[0].answer, "Первый ответ")

    def test_cancelled_response_never_enters_history(self):
        entered = threading.Event()

        def generate(data, mime_type, prompt, publish, cancel, **kwargs):
            entered.set()
            cancel.wait(2)
            return "Вопрос: Поздний?\nОтвет: Поздний ответ"

        with patch.object(self.app.llm, "capture_screen", return_value=b"jpg"):
            with patch.object(self.app.llm, "generate", side_effect=generate):
                self.app.capture()
                self.assertTrue(entered.wait(2))
                future = self.app.future
                self.app.cancel()
                future.result(timeout=2)
                self.app.poll()
                self.assertEqual(self.app.sessions.snapshot("TEST")[1], ())

    def test_vad_recording_reaches_transcription_and_can_be_cancelled(self):
        self.app.settings.vad_enabled = True
        entered = threading.Event()

        def record(
            stop, cancel, seconds, max_bytes, device, vad_options=None, on_segment=None
        ):
            self.assertEqual(vad_options["sensitivity"], 2)
            entered.set()
            cancel.wait(2)
            return b""

        with patch("main.record_wav", side_effect=record):
            with patch.object(self.app.llm, "transcribe") as transcribe:
                self.app.audio()
                self.assertTrue(entered.wait(2))
                future = self.app.capture_future
                self.app.cancel()
                future.result(timeout=2)
                self.app.poll()
                transcribe.assert_not_called()
                self.assertFalse(self.app.monitoring)
                self.assertIs(self.app.tasks.state, State.IDLE)

    def test_vad_segment_reaches_transcription_review(self):
        self.app.settings.vad_enabled = True
        emitted = threading.Event()

        def record(
            stop, cancel, seconds, max_bytes, device, vad_options=None, on_segment=None
        ):
            on_segment(1, b"vad wav")
            emitted.set()
            stop.wait(2)
            return b""

        with patch("main.record_wav", side_effect=record):
            with patch.object(
                self.app.llm, "transcribe", return_value="речь"
            ) as transcribe:
                with patch.object(self.app, "show_transcript") as review:
                    self.app.audio()
                    self.assertTrue(emitted.wait(2))
                    self.app.poll()
                    self.app.future.result(timeout=2)
                    self.app.poll()
                    self.assertIs(self.app.tasks.state, State.REVIEW)
                    self.assertEqual(transcribe.call_args.args[0], b"vad wav")
                    review.assert_called_once()
                    self.app.cancel()
                    self.app.capture_future.result(timeout=2)

    def test_same_vad_transcript_is_shown_again(self):
        self.app.settings.vad_enabled = True
        emitted = threading.Event()

        def record(
            stop, cancel, seconds, max_bytes, device, vad_options=None, on_segment=None
        ):
            on_segment(1, b"one")
            on_segment(2, b"two")
            emitted.set()
            stop.wait(2)
            return b""

        with patch("main.record_wav", side_effect=record):
            with patch.object(self.app.llm, "transcribe", return_value="Повтор"):
                with patch.object(self.app, "show_transcript") as review:
                    self.app.audio()
                    self.assertTrue(emitted.wait(2))
                    self.app.poll()
                    self.app.future.result(timeout=2)
                    self.app.poll()
                    self.assertIs(self.app.tasks.state, State.REVIEW)
                    self.app.tasks.finish(self.app.tasks.active)
                    self.app.poll()
                    self.app.future.result(timeout=2)
                    self.app.poll()
                    self.assertEqual(review.call_count, 2)
                    self.app.cancel()
                    self.app.capture_future.result(timeout=2)
