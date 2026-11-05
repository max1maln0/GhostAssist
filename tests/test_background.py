import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config.settings import Settings, validate_hotkeys
from ui.dashboard import DashboardApp


class BackgroundTests(unittest.TestCase):
    def test_hide_restore_keeps_service(self):
        ready = threading.Event()
        ready.set()
        tray = SimpleNamespace(ready=ready, visible=True, icon=Mock())
        app = SimpleNamespace(
            tray=tray,
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
            withdraw=Mock(),
        )
        with patch("ui.dashboard.messagebox.showwarning") as warning:
            DashboardApp.hide_dashboard(app)
        warning.assert_called_once()
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
        app = SimpleNamespace(
            assistant_process=Mock(),
            restart_pending=False,
            _stop_process=Mock(),
            _bind_dashboard_hotkey=Mock(),
        )
        app.assistant_process.poll.return_value = None
        with patch("ui.dashboard.Settings", return_value=Mock()):
            with patch("ui.dashboard.update_env"):
                self.assertTrue(DashboardApp._save(app, CLOSE_TO_TRAY="false"))
                self.assertTrue(
                    DashboardApp._save(app, HOTKEY_DASHBOARD="<alt>+<home>")
                )
        app._stop_process.assert_not_called()
        app._bind_dashboard_hotkey.assert_called_once()

    def test_dashboard_hotkey_is_checked_for_conflicts(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.hotkey_dashboard, "<alt>+<home>")
        validate_hotkeys(settings)
        with self.assertRaises(ValueError):
            validate_hotkeys(settings, {"hotkey_dashboard": settings.hotkey_audio})
