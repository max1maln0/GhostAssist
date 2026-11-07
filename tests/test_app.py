import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mock_provider import MockProvider

from config.settings import Settings
from core.audio_queue import AudioError
from core.controller import ApplicationController, ViewKind
from core.llm_worker import LLMWorker
from core.orchestrator import Lifecycle, TaskOrchestrator


class AppTests(unittest.TestCase):
    def test_audio_failure_and_empty_transcription_recover(self):
        with patch.object(
            self.deps.audio, "record", side_effect=AudioError("Устройство отключилось.")
        ):
            self.app.audio()
            views = self.finish()
        self.assertTrue(any(v.status == "ОШИБКА" for v in views))
        with patch.object(self.deps.llm, "transcribe", return_value=""):
            self.app.audio()
            views = self.finish()
        self.assertFalse(any(v.kind is ViewKind.REVIEW for v in views))
        self.app.audio()
        self.finish()
        self.assertIs(self.app.orchestrator.state, Lifecycle.WAITING)

    def test_selection_cancel_and_mode_conflicts(self):
        self.app.snip()
        task_id = self.app.active.request.task_id
        mode = self.app.context.mode
        self.app.switch_mode()
        self.assertEqual(self.app.context.mode, mode)
        self.app.selected(task_id, None)
        self.app.poll()
        self.assertIsNone(self.app.active)
        self.app.switch_mode()
        self.assertNotEqual(self.app.context.mode, mode)

    def test_pending_cancellation_prevents_success_delivery(self):
        self.app.capture()
        self.app.orchestrator.future.result(2)
        cancel = threading.Event()
        cancel.set()
        views = self.app.poll(cancel)
        self.assertEqual(self.app.sessions.snapshot("TEST")[1], ())
        self.assertFalse(any(v.text and "Ответ:" in v.text for v in views))

    def test_vad_overflow_cancel_and_restart(self):
        self.settings.vad_enabled = True
        emitted = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            for number in range(1, 10):
                kwargs["on_segment"](number, b"wav")
            emitted.set()
            stop.wait(2)
            return b""

        self.deps.audio.record.side_effect = record
        self.app.audio()
        self.assertTrue(emitted.wait(1))
        self.app.poll()
        self.assertEqual(self.app.dropped, 5)
        future = self.app.source_future
        self.app.cancel()
        future.result(2)
        if self.app.orchestrator.future:
            self.app.orchestrator.future.result(2)
        self.app.poll()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.segments)

    def test_20_vad_start_stop_cycles_reuse_source_and_release_resources(self):
        self.settings.vad_enabled = True
        source_threads = set()
        entered = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            source_threads.add(threading.get_ident())
            entered.set()
            stop.wait(2)
            return b""

        self.deps.audio.record.side_effect = record
        for _ in range(20):
            entered.clear()
            self.app.audio()
            self.assertTrue(entered.wait(1))
            future = self.app.source_future
            self.app.cancel()
            future.result(2)
            self.app.poll()
            self.assertFalse(self.app.busy)
        self.assertEqual(len(source_threads), 1)

    def test_ui_review_callback_uses_controller(self):
        from main import GhostAssistApp

        self.cleanup_for_replacement()
        deps = SimpleNamespace(**vars(self.deps))
        deps.orchestrator = TaskOrchestrator()
        deps.hotkeys = Mock()
        with patch("main.StealthHUD"):
            ui = GhostAssistApp(self.settings, dependencies=deps)
        # This adapter owns the shared dependencies for the remainder of this test.
        self.app = ui.controller
        with patch("main.TranscriptReview") as review:
            ui.audio()
            ui.future.result(2)
            ui.poll()
            callback = review.call_args.args[2]
            callback("изменено")
            ui.future.result(2)
            ui.poll()
        self.assertEqual(ui.sessions.snapshot("INTERVIEW")[1][0].question, "изменено")

    def cleanup_for_replacement(self):
        self.app.close()
        self.app.orchestrator.close(timeout=2)
        self.app._source.shutdown(wait=True)

    def test_ui_window_failures_do_not_leave_controller_busy(self):
        from main import GhostAssistApp

        self.cleanup_for_replacement()
        deps = SimpleNamespace(**vars(self.deps))
        deps.orchestrator = TaskOrchestrator()
        deps.hotkeys = Mock()
        with patch("main.StealthHUD"):
            ui = GhostAssistApp(self.settings, dependencies=deps)
        self.app = ui.controller
        with patch("main.SnippingOverlay", side_effect=OSError("private")):
            ui.snip()
            ui.poll()
            ui.hud.update_text.assert_called_with(
                "Не удалось открыть окно выделения.", "ОШИБКА"
            )
            ui.poll()
            self.assertFalse(self.app.busy)

        with patch("main.TranscriptReview", side_effect=OSError("private")):
            ui.audio()
            ui.future.result(2)
            ui.poll()
            ui.hud.update_text.assert_called_with(
                "Не удалось открыть проверку текста.", "ОШИБКА"
            )
            ui.poll()
            self.assertFalse(self.app.busy)

    def test_auto_send_adapter_and_stale_confirmation(self):
        from main import GhostAssistApp

        self.cleanup_for_replacement()
        self.settings.auto_send_transcript = True
        deps = SimpleNamespace(**vars(self.deps))
        deps.orchestrator = TaskOrchestrator()
        deps.hotkeys = Mock()
        with patch("main.StealthHUD"):
            ui = GhostAssistApp(self.settings, dependencies=deps)
        self.app = ui.controller
        with patch("main.TranscriptReview") as review:
            ui.audio()
            ui.future.result(2)
            old_id = self.app.active.request.task_id
            ui.poll()
            review.return_value.submit.side_effect = lambda: review.call_args.args[2](
                "auto"
            )
            review.return_value.after.call_args.args[1]()
            ui.future.result(2)
            ui.poll()
            self.assertEqual(ui.sessions.snapshot("INTERVIEW")[1][0].question, "auto")
            ui.capture()
            self.assertFalse(self.app.confirm(old_id, "stale"))
            ui.future.result(2)
            ui.poll()

    def setUp(self):
        self.settings = Settings(_env_file=None)
        self.deps = SimpleNamespace(
            orchestrator=TaskOrchestrator(),
            screen=SimpleNamespace(capture=Mock(return_value=b"jpg")),
            audio=SimpleNamespace(record=Mock(return_value=b"wav")),
            llm=LLMWorker(
                self.settings,
                provider=MockProvider(chunks=("Вопрос: Вопрос?\nОтвет: Ответ",)),
            ),
        )
        self.app = ApplicationController(self.settings, self.deps)

    def tearDown(self):
        self.app.close()
        self.app.orchestrator.close(timeout=2)
        self.app._source.shutdown(wait=True)

    def finish(self):
        self.app.orchestrator.future.result(2)
        return self.app.poll()

    def test_100_screen_tasks_and_selection(self):
        for _ in range(100):
            self.app.capture()
            self.finish()
            self.assertIsNone(self.app.active)
        self.assertEqual(
            len(self.app.sessions.snapshot("TEST")[1]), self.settings.memory_depth
        )
        self.app.snip()
        task_id = self.app.active.request.task_id
        self.app.poll()
        bbox = {"left": -20, "top": 0, "width": 80, "height": 80}
        self.app.selected(task_id, bbox)
        self.finish()
        self.deps.screen.capture.assert_called_with(bbox)

    def test_manual_audio_stop_and_confirm_same_uuid(self):
        entered = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            entered.set()
            stop.wait(2)
            return b"wav"

        self.deps.audio.record.side_effect = record
        self.app.audio()
        self.assertTrue(entered.wait(1))
        task_id = self.app.active.request.task_id
        self.app.capture()
        self.app.snip()
        self.deps.screen.capture.assert_not_called()
        self.app.audio()
        views = self.finish()
        self.assertIs(self.app.orchestrator.state, Lifecycle.WAITING)
        self.assertTrue(any(v.kind is ViewKind.REVIEW for v in views))
        self.assertTrue(self.app.confirm(task_id, "исправлено"))
        self.assertEqual(self.app.active.request.task_id, task_id)
        self.finish()
        self.assertEqual(
            self.app.sessions.snapshot("INTERVIEW")[1][0].question, "исправлено"
        )

    def test_cancel_review(self):
        self.app.audio()
        self.finish()
        self.app.cancel_current()
        self.app.cancel_current()
        self.app.poll()
        self.assertIsNone(self.app.active)
        self.assertEqual(self.app.sessions.snapshot("INTERVIEW")[1], ())

    def test_cancel_and_late_result(self):
        entered, release = threading.Event(), threading.Event()

        def generate(*args, **kwargs):
            entered.set()
            args[3]("partial")
            release.wait(2)
            args[3]("late")
            return "Вопрос: Late?\nОтвет: late"

        with patch.object(self.deps.llm, "generate", side_effect=generate):
            self.app.capture()
            self.assertTrue(entered.wait(1))
            self.app.cancel()
            self.app.capture()
            self.app.audio()
            self.assertTrue(self.app.orchestrator.busy)
            release.set()
            views = self.finish()
        self.assertFalse(any(v.text and "late" in v.text for v in views))
        self.assertEqual(self.app.sessions.snapshot("TEST")[1], ())
        self.app.capture()
        self.finish()

    def test_timeout_and_error_recovery(self):
        self.settings.task_timeout = 0.05

        def generate(*args, **kwargs):
            args[4].wait(1)
            return "late"

        with patch.object(self.deps.llm, "generate", side_effect=generate):
            self.app.capture()
            views = self.finish()
        self.assertTrue(any(v.status == "ОШИБКА" for v in views))
        self.assertEqual(self.app.sessions.snapshot("TEST")[1], ())
        with patch.object(self.deps.screen, "capture", side_effect=OSError("private")):
            self.app.capture()
            views = self.finish()
            self.assertNotIn("private", str(views))
        self.app.capture()
        self.finish()

    def test_vad_fifo_and_continues_during_review(self):
        self.settings.vad_enabled = True
        emitted = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            kwargs["on_segment"](1, b"one")
            kwargs["on_segment"](2, b"two")
            emitted.set()
            stop.wait(2)
            return b""

        self.deps.audio.record.side_effect = record
        with patch.object(
            self.deps.llm, "transcribe", side_effect=["one", "two"]
        ) as transcribe:
            self.app.audio()
            self.assertTrue(emitted.wait(1))
            self.app.capture()
            self.app.snip()
            self.app.poll()
            for question in ("one", "two"):
                self.finish()
                self.assertTrue(self.app.monitoring)
                self.app.confirm(self.app.active.request.task_id, question)
                self.finish()
            self.assertEqual(
                [c.args[0] for c in transcribe.call_args_list], [b"one", b"two"]
            )
            self.app.cancel()
            self.app.source_future.result(2)
            self.app.poll()
            self.assertIsNone(self.app.source_future)

    def test_close_and_repeat(self):
        entered = threading.Event()

        def generate(*args, **kwargs):
            entered.set()
            args[4].wait(2)
            return "late"

        with patch.object(self.deps.llm, "generate", side_effect=generate):
            self.app.capture()
            self.assertTrue(entered.wait(1))
            self.app.close()
            self.app.close()
            self.app.orchestrator.future.result(2)
            self.app.poll()
        self.assertTrue(self.app.stopped)
