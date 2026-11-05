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

    def test_audio_toggle_blocks_screen_and_updates_hud(self):
        entered = threading.Event()

        def record(stop, cancel, seconds, max_bytes):
            entered.set()
            if not stop.wait(2):
                raise TimeoutError
            return b"wav"

        with patch("main.record_wav", side_effect=record) as capture:
            with patch.object(
                self.app.llm, "generate", return_value="answer"
            ) as generate:
                self.app.audio()
                self.assertTrue(entered.wait(2))
                self.app.capture()
                self.app.snip()
                self.assertIs(self.app.tasks.state, State.RECORDING)
                self.app.audio()
                self.app.audio()
                self.app.future.result(timeout=2)
                self.app.poll()
                self.assertIs(self.app.tasks.state, State.IDLE)
                self.assertEqual(generate.call_args.args[:2], (b"wav", "audio/wav"))
                capture.assert_called_once()
                self.app.hud.update_text.assert_called_with("answer", "[TEST] ГОТОВО")

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
