import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config.settings import HotkeyValidationError, Settings, validate_hotkeys
from main import GhostAssistApp
from ui.dashboard import DashboardApp
from ui.hotkey_capture import HotkeyCapture


class CaptureTests(unittest.TestCase):
    def test_live_modifiers_and_commit_after_all_keys_released(self):
        capture = HotkeyCapture()
        capture.press("Control_L")
        self.assertEqual(capture.display(), "Ctrl + ...")
        capture.press("Shift_R")
        self.assertEqual(capture.display(), "Ctrl + Shift + ...")
        capture.press("F9")
        self.assertEqual(capture.display(), "Ctrl + Shift + F9")
        self.assertEqual(capture.release("F9"), (None, None))
        self.assertEqual(capture.release("Control_L"), (None, None))
        self.assertEqual(
            capture.release("Shift_R"), ("complete", "<ctrl>+<shift>+<f9>")
        )

    def test_modifier_changes_repeat_and_left_right(self):
        capture = HotkeyCapture()
        capture.press("Control_L")
        capture.press("Control_R")
        capture.press("Control_L")
        self.assertEqual(capture.display(), "Ctrl + ...")
        capture.release("Control_L")
        self.assertEqual(capture.display(), "Ctrl + ...")
        capture.press("Shift_L")
        capture.press("F9")
        capture.release("Control_R")
        self.assertEqual(capture.display(), "Shift + F9")
        capture.release("F9")
        self.assertEqual(capture.release("Shift_L"), ("complete", "<shift>+<f9>"))

    def test_escape_and_modifier_release_without_main_key(self):
        capture = HotkeyCapture()
        capture.press("Alt_R")
        self.assertEqual(capture.display(), "Alt + ...")
        capture.release("Alt_R")
        self.assertEqual(capture.display(), "Нажмите комбинацию...")
        self.assertEqual(capture.press("Escape"), ("cancel", None))

    def test_detailed_conflicts_and_unsupported_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            with self.assertRaisesRegex(
                HotkeyValidationError, "зарезервирована Windows"
            ):
                validate_hotkeys(settings, {"hotkey_screen": "<alt>+<f4>"})
            with self.assertRaisesRegex(
                HotkeyValidationError, "Показать / скрыть Dashboard"
            ):
                validate_hotkeys(settings, {"hotkey_screen": "<ctrl>+<shift>+<f9>"})
            with self.assertRaisesRegex(HotkeyValidationError, "модификатор"):
                validate_hotkeys(settings, {"hotkey_screen": "a"})
            with self.assertRaisesRegex(HotkeyValidationError, "распознать"):
                validate_hotkeys(settings, {"hotkey_screen": "<not_a_key>"})

    def test_reset_conflict_does_not_save(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                hotkey_dashboard="<alt>+<home>",
                hotkey_screen="<ctrl>+<shift>+<f9>",
            )
            app = SimpleNamespace(
                settings=settings,
                active_hotkey=None,
                request_save=Mock(),
                _end_hotkey_capture=Mock(),
                hotkey_buttons={"HOTKEY_DASHBOARD": Mock()},
            )
            with patch("ui.dashboard_hotkeys.messagebox.showwarning") as warning:
                DashboardApp._reset_hotkey(app, "HOTKEY_DASHBOARD")
            app.request_save.assert_not_called()
            self.assertIn("Фуллскрин скрин", warning.call_args.args[1])

    def test_reset_saves_configured_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None, hotkey_dashboard="<alt>+<home>")
            app = SimpleNamespace(
                settings=settings,
                active_hotkey=None,
                request_save=Mock(side_effect=lambda values, done: done(True)),
                _end_hotkey_capture=Mock(),
                hotkey_buttons={"HOTKEY_DASHBOARD": Mock()},
            )
            DashboardApp._reset_hotkey(app, "HOTKEY_DASHBOARD")
            self.assertEqual(
                app.request_save.call_args.args[0],
                {"HOTKEY_DASHBOARD": "<ctrl>+<shift>+<f9>"},
            )
            app.hotkey_buttons["HOTKEY_DASHBOARD"].configure.assert_called_once_with(
                text="Ctrl + Shift + F9"
            )

    def test_service_capture_suspension_protocol(self):
        reader, writer = os.pipe()
        app = SimpleNamespace(
            enqueue=Mock(),
            cancel=object(),
            close=object(),
            hotkeys_suspended=threading.Event(),
        )
        thread = threading.Thread(
            target=GhostAssistApp.watch_parent, args=(app, reader)
        )
        try:
            thread.start()
            os.write(writer, b"S")
            self.assertTrue(app.hotkeys_suspended.wait(2))
            os.write(writer, b"R")
            deadline = time.monotonic() + 2
            while app.hotkeys_suspended.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(app.hotkeys_suspended.is_set())
            os.close(writer)
            writer = None
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            app.enqueue.assert_called_once_with(app.close)
        finally:
            if writer is not None:
                os.close(writer)
            os.close(reader)
