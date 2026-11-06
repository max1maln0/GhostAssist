import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pynput._util.win32 import SystemHook

from core.hotkeys import VerifiedGlobalHotKeys


class HookLifecycleTests(unittest.TestCase):
    def test_null_windows_hook_is_reported_and_thread_stops(self):
        class FailedHook:
            def __init__(self, *_):
                pass

            def __enter__(self):
                return SimpleNamespace(_hook=0)

            def __exit__(self, *_):
                pass

        listener = VerifiedGlobalHotKeys({"<ctrl>+<shift>+<f9>": lambda: None})
        with patch("core.hotkeys.SystemHook", FailedHook):
            with self.assertRaisesRegex(OSError, "hook"):
                listener.start_verified()
        self.assertFalse(listener.is_alive())

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
