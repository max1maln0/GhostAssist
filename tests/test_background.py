import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pynput import keyboard

from config.settings import Settings, validate_hotkeys
from main import GhostAssistApp
from ui.dashboard import DashboardApp


class BackgroundTests(unittest.TestCase):
    def test_hide_restore_keeps_service(self):
        ready = threading.Event()
        ready.set()
        tray = SimpleNamespace(ready=ready, visible=True, icon=Mock())
        app = SimpleNamespace(
            tray=tray,
            _end_hotkey_capture=Mock(),
            dashboard_visible=True,
            withdraw=Mock(),
            deiconify=Mock(),
            lift=Mock(),
            focus_force=Mock(),
            assistant_process=Mock(),
        )
        process = app.assistant_process
        DashboardApp.hide_dashboard(app)
        app.withdraw.assert_called_once()
        self.assertFalse(app.dashboard_visible)
        self.assertIs(app.assistant_process, process)
        DashboardApp.show_dashboard(app)
        app.deiconify.assert_called_once()
        self.assertTrue(app.dashboard_visible)
        self.assertIs(app.assistant_process, process)

    def test_close_preference(self):
        app = SimpleNamespace(
            _window_exists=True,
            settings=SimpleNamespace(close_to_tray=True),
            hide_dashboard=Mock(),
            exit_app=Mock(),
        )
        DashboardApp.on_closing(app)
        app.hide_dashboard.assert_called_once()
        app.settings.close_to_tray = False
        DashboardApp.on_closing(app)
        app.exit_app.assert_called_once()

    def test_close_during_startup_is_deferred(self):
        app = SimpleNamespace(
            _window_exists=False,
            after=Mock(),
            on_closing=Mock(),
            hide_dashboard=Mock(),
            exit_app=Mock(),
        )
        DashboardApp.on_closing(app)
        app.after.assert_called_once_with(30, app.on_closing)
        app.hide_dashboard.assert_not_called()
        app.exit_app.assert_not_called()

    def test_cannot_hide_before_tray_is_ready(self):
        app = SimpleNamespace(
            tray=SimpleNamespace(ready=threading.Event()),
            set_error=Mock(),
            withdraw=Mock(),
        )
        DashboardApp.hide_dashboard(app)
        app.set_error.assert_called_once()
        app.withdraw.assert_not_called()

    def test_exit_request_is_not_lost_when_queue_full(self):
        from queue import Queue

        app = SimpleNamespace(
            exit_requested=threading.Event(),
            capturing_hotkey=False,
            tray_commands=Queue(maxsize=1),
        )
        app.tray_commands.put_nowait("toggle")
        DashboardApp._enqueue_tray(app, "exit")
        self.assertTrue(app.exit_requested.is_set())

    def test_dashboard_preferences_do_not_restart_service(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from dashboard_test_support import TransactionHarness, finish

        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            app = TransactionHarness(Path(directory) / ".env")
            listener = Mock()
            app.prepare_hotkeys.return_value = listener
            self.assertTrue(
                app.request_save(
                    {"CLOSE_TO_TRAY": "false", "HOTKEY_DASHBOARD": "<alt>+<home>"}
                )
            )
            finish(app.save_jobs)
            self.assertFalse(app.settings.close_to_tray)
            app._replace_dashboard_hotkey.assert_called_once_with(listener)
            app._stop_process.assert_not_called()
            app.save_jobs.close()

    def test_service_changes_require_explicit_apply(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from dashboard_test_support import TransactionHarness, finish

        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            app = TransactionHarness(Path(directory) / ".env")
            app.request_save({"HOTKEY_SCREEN": "<alt>+z", "ACTIVE_PROFILE": "other.md"})
            finish(app.save_jobs)
            self.assertEqual(app.settings.hotkey_screen, "<alt>+z")
            self.assertEqual(app.settings.active_profile, "other.md")
            self.assertFalse(app.restart_pending)
            app._stop_process.assert_not_called()
            app.save_jobs.close()

    def test_hotkey_registration_failure_does_not_save(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from dashboard_test_support import TransactionHarness, finish

        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            app = TransactionHarness(Path(directory) / ".env")
            initial = app.settings
            app.prepare_hotkeys.side_effect = OSError("private detail")
            with patch("ui.dashboard_settings.update_env") as write:
                app.request_save({"HOTKEY_DASHBOARD": "<alt>+<home>"})
                finish(app.save_jobs)
            write.assert_not_called()
            self.assertIs(app.settings, initial)
            self.assertNotIn("private", app.set_error.call_args.args[0])
            app.save_jobs.close()

    def test_failed_write_stops_prepared_hotkey(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from dashboard_test_support import TransactionHarness, finish

        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            app = TransactionHarness(Path(directory) / ".env")
            initial = app.settings
            listener = Mock()
            app.prepare_hotkeys.return_value = listener
            with patch("ui.dashboard_settings.update_env", side_effect=OSError):
                app.request_save({"HOTKEY_DASHBOARD": "<alt>+<home>"})
                finish(app.save_jobs)
            listener.stop.assert_called_once()
            self.assertIs(app.settings, initial)
            app.save_jobs.close()

    def test_hotkey_event_is_queued(self):
        from queue import Queue

        app = SimpleNamespace(
            exit_requested=threading.Event(),
            capturing_hotkey=False,
            tray_commands=Queue(maxsize=4),
        )
        listener = keyboard.GlobalHotKeys(
            {"<alt>+<home>": lambda: DashboardApp._enqueue_tray(app, "toggle")}
        )
        listener._on_press(keyboard.Key.alt, False)
        listener._on_press(keyboard.Key.home, False)
        listener._on_release(keyboard.Key.home, False)
        listener._on_release(keyboard.Key.alt, False)
        self.assertEqual(app.tray_commands.get_nowait(), "toggle")

    def test_parent_cancel_command_then_eof(self):
        reader, writer = os.pipe()
        app = SimpleNamespace(enqueue=Mock(), cancel=object(), close=object())
        thread = threading.Thread(
            target=GhostAssistApp.watch_parent, args=(app, reader)
        )
        try:
            thread.start()
            os.write(writer, b"C")
            os.close(writer)
            writer = None
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(app.enqueue.call_args_list[0].args, (app.cancel,))
            self.assertEqual(app.enqueue.call_args_list[1].args, (app.close,))
        finally:
            if writer is not None:
                os.close(writer)
            os.close(reader)

    def test_dashboard_hotkey_is_checked_for_conflicts(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.hotkey_dashboard, "<ctrl>+<shift>+<f9>")
        validate_hotkeys(settings)
        with self.assertRaises(ValueError):
            validate_hotkeys(settings, {"hotkey_dashboard": settings.hotkey_audio})
