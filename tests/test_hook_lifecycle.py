import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pynput._util.win32 import SystemHook

from config.settings import Settings
from platform_adapters.windows.hotkeys import (
    VerifiedGlobalHotKeys,
    WindowsHotkeyBackend,
)
from ui.dashboard import DashboardApp


class HookLifecycleTests(unittest.TestCase):
    def test_hook_exception_is_reported_without_sticking_listener(self):
        class RaisingHook:
            def __init__(self, *_):
                pass

            def __enter__(self):
                raise OSError("private detail")

            def __exit__(self, *_):
                pass

        listener = VerifiedGlobalHotKeys({"<ctrl>+<shift>+<f9>": lambda: None})
        with patch("platform_adapters.windows.hotkeys.SystemHook", RaisingHook):
            with self.assertRaises(OSError) as error:
                listener.start_verified()
        self.assertNotIn("private detail", str(error.exception))
        self.assertFalse(listener.hook_installed)
        self.assertFalse(listener.is_alive())

    def test_null_windows_hook_is_reported_and_thread_stops(self):
        class FailedHook:
            def __init__(self, *_):
                pass

            def __enter__(self):
                return SimpleNamespace(_hook=0)

            def __exit__(self, *_):
                pass

        listener = VerifiedGlobalHotKeys({"<ctrl>+<shift>+<f9>": lambda: None})
        with patch("platform_adapters.windows.hotkeys.SystemHook", FailedHook):
            with self.assertRaisesRegex(OSError, "hook"):
                listener.start_verified()
        self.assertFalse(listener.is_alive())

    def test_failed_hook_keeps_previous_dashboard_listener_and_settings(self):
        class FailedHook:
            def __init__(self, *_):
                pass

            def __enter__(self):
                return SimpleNamespace(_hook=0)

            def __exit__(self, *_):
                pass

        with patch.dict(os.environ, {}, clear=True):
            initial = Settings(_env_file=None)
            candidate = Settings(_env_file=None, hotkey_dashboard="<alt>+<home>")
            old = Mock()
            app = SimpleNamespace(
                settings=initial, dashboard_hotkey=old, hotkeys=WindowsHotkeyBackend()
            )
            app._create_dashboard_hotkey = lambda settings: (
                DashboardApp._create_dashboard_hotkey(app, settings)
            )
            with (
                patch("platform_adapters.windows.hotkeys.SystemHook", FailedHook),
                patch("ui.dashboard.Settings", return_value=candidate),
                patch("ui.dashboard.update_env") as update,
                patch("ui.dashboard.messagebox.showerror"),
            ):
                self.assertFalse(
                    DashboardApp._save(app, HOTKEY_DASHBOARD="<alt>+<home>")
                )
            update.assert_not_called()
            old.stop.assert_not_called()
            self.assertIs(app.dashboard_hotkey, old)
            self.assertIs(app.settings, initial)

    def test_real_hook_starts_and_stops(self):
        listener = VerifiedGlobalHotKeys({"<ctrl>+<shift>+<f9>": lambda: None})
        listener.start_verified()
        self.assertTrue(listener.hook_installed)
        listener.stop()
        listener.join(timeout=2)
        self.assertFalse(listener.is_alive())
        self.assertNotIn(listener.ident, SystemHook._HOOKS)


if __name__ == "__main__":
    unittest.main()
