"""Real CustomTkinter integration tests with isolated .env/profiles and fake I/O."""

import faulthandler
import os
import tempfile
import time
import unittest
from pathlib import Path
from tkinter import TclError
from unittest.mock import Mock, patch

from config.settings import load_settings
from providers.access_check import AccessResult
from ui.dashboard import DashboardApp


class DashboardGuiTests(unittest.TestCase):
    def setUp(self):
        faulthandler.dump_traceback_later(90, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = Path(self.folder.name)
        self.path = self.directory / ".env"
        self.path.write_text("SCHEMA_VERSION=2\n", encoding="utf-8")
        try:
            self.app = DashboardApp(
                settings_path=self.path,
                profiles_directory=self.directory,
                start_integrations=False,
            )
        except TclError as exc:
            self.skipTest("Graphical Tk session unavailable: " + type(exc).__name__)
        self.errors = []
        self.app.report_callback_exception = lambda *error: self.errors.append(error)
        self.app.update()
        self.addCleanup(self.close)

    def close(self):
        for runner in (self.app.save_jobs, self.app.probe_jobs, self.app.device_jobs):
            runner.close()
        self.app.destroy()
        self.assertEqual(self.errors, [])

    def pump(self, runner):
        deadline = time.monotonic() + 3
        while runner.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.assertFalse(runner.busy)
        self.app.update()

    def test_navigation_and_responsive_footer(self):
        for page, frame in self.app.pages.items():
            self.app.navigate(page)
            self.app.update()
            self.assertEqual(self.app.current_page, page)
            self.assertTrue(frame.winfo_ismapped())
        self.app.geometry("780x560")
        self.app.update()
        self.app.update_idletasks()
        self.assertEqual(self.app.apply_button.grid_info()["row"], 2)
        for widget in (
            self.app.apply_button,
            self.app.save_button,
            self.app.cancel_button,
        ):
            self.assertLessEqual(
                widget.winfo_rootx() + widget.winfo_width(),
                self.app.winfo_rootx() + self.app.winfo_width(),
            )

    def test_form_save_reload_cancel_and_independent_models(self):
        self.app.variable("GENERATION_PROVIDER").set("openai")
        self.app.provider_changed("generation", "openai")
        self.assertEqual(self.app.variable("TRANSCRIPTION_PROVIDER").get(), "gemini")
        self.app.variable("MEMORY_DEPTH").set("4")
        self.app.request_save()
        self.pump(self.app.save_jobs)
        reloaded = load_settings(self.path)
        self.assertEqual(reloaded.resolve_binding("generation")[0], "openai")
        self.assertEqual(reloaded.memory_depth, 4)
        self.app.variable("MEMORY_DEPTH").set("9")
        self.app.discard_changes()
        self.assertEqual(self.app.variable("MEMORY_DEPTH").get(), "4")
        self.assertEqual(self.app.draft.changes, {})

    def test_secret_save_replace_delete_never_prefills_entry(self):
        entry = self.app.key_controls["openai"][0]
        for key in ("offline-key-one", "offline-key-two"):
            entry.insert(0, key)
            self.app.save_key("openai")
            self.pump(self.app.save_jobs)
            self.assertEqual(
                load_settings(self.path).openai_api_key.get_secret_value(), key
            )
            self.assertEqual(entry.get(), "")
        with patch("ui.dashboard_actions.messagebox.askyesno", return_value=True):
            self.app.delete_key("openai")
            self.pump(self.app.save_jobs)
        self.assertEqual(load_settings(self.path).openai_api_key.get_secret_value(), "")
        self.assertEqual(entry.get(), "")

    def test_write_failure_keeps_draft_and_widgets_enabled(self):
        before = self.path.read_bytes()
        self.app.variable("AUDIO_MAX_SECONDS").set("12")
        with patch(
            "ui.dashboard_settings.update_env",
            side_effect=PermissionError("private-data"),
        ):
            self.app.request_save()
            self.pump(self.app.save_jobs)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.app.variable("AUDIO_MAX_SECONDS").get(), "12")
        self.assertIn("AUDIO_MAX_SECONDS", self.app.draft.changes)
        self.assertEqual(self.app.save_button.cget("state"), "normal")
        self.assertNotIn("private-data", self.app.last_error)

    def test_profiles_dirty_warning_atomic_save_and_traversal(self):
        profile = self.directory / self.app.settings.active_profile
        self.app.legend_textbox.insert("end", "Example profile")
        self.app.update()
        self.assertTrue(self.app.profile_dirty)
        self.app.navigate("profiles")
        with patch(
            "ui.dashboard_profiles.messagebox.askyesnocancel", return_value=None
        ):
            self.app.navigate("overview")
        self.assertEqual(self.app.current_page, "profiles")
        self.assertTrue(self.app.save_current_legend())
        self.assertEqual(profile.read_text(encoding="utf-8"), "Example profile")
        self.assertFalse(self.app.profile_dirty)
        self.assertFalse(self.app.load_selected_profile("../escape.md"))
        self.assertFalse((self.directory.parent / "escape.md").exists())
        profile.write_text("external edit", encoding="utf-8")
        self.assertFalse(self.app.save_current_legend())
        self.assertEqual(profile.read_text(), "external edit")

    def test_hook_capture_switch_escape_and_async_save(self):
        self.app.navigate("hotkeys")
        self.app._start_hotkey_capture("HOTKEY_SCREEN")
        self.assertTrue(self.app.capturing_hotkey)
        self.app._start_hotkey_capture("HOTKEY_AUDIO")
        self.assertEqual(self.app.active_hotkey, "HOTKEY_AUDIO")
        self.assertEqual(
            self.app.hotkey_buttons["HOTKEY_SCREEN"].cget("border_color"), "#1e293b"
        )
        self.app.focus_force()
        self.app.update()
        self.app.event_generate("<KeyPress>", keysym="Escape")
        self.app.update()
        self.assertFalse(self.app.capturing_hotkey)
        self.assertIsNone(self.app.active_hotkey)
        with patch.object(self.app, "prepare_hotkeys", return_value=None):
            self.app._start_hotkey_capture("HOTKEY_SCREEN")
            self.app.update()
            for key in ("Control_L", "Shift_L", "F8"):
                self.app.event_generate("<KeyPress>", keysym=key)
            for key in ("F8", "Shift_L", "Control_L"):
                self.app.event_generate("<KeyRelease>", keysym=key)
            self.pump(self.app.save_jobs)
        self.assertFalse(self.app.capturing_hotkey)
        self.assertEqual(self.app.settings.hotkey_screen, "<ctrl>+<shift>+<f8>")

    def test_async_device_refresh_preserves_missing_selection(self):
        self.app.variable("AUDIO_DEVICE").set("Disconnected headset")
        self.app.audio_capture = Mock()
        self.app.audio_capture.list_devices.return_value = ["Realtek loopback"]
        self.app._refresh_devices()
        self.pump(self.app.device_jobs)
        self.assertIn("недоступно", self.app.device_status.cget("text"))
        self.assertEqual(self.app.device_combo.get(), "Disconnected headset")
        self.app.device_selected("По умолчанию")
        self.assertEqual(self.app.variable("AUDIO_DEVICE").get(), "")

    def test_metadata_probe_no_generation_and_stale_revision(self):
        with patch(
            "ui.dashboard_actions.check_access",
            return_value=AccessResult("auth", "Ошибка авторизации"),
        ) as probe:
            self.app.probe_access("gemini")
            self.pump(self.app.probe_jobs)
            probe.assert_called_once()
        self.assertIn(
            "Ошибка авторизации", self.app.key_controls["gemini"][1].cget("text")
        )
        with patch(
            "ui.dashboard_actions.check_access",
            return_value=AccessResult("verified", "Old result"),
        ):
            self.app.probe_access("gemini")
            self.app.probe_revision += 1
            self.app.probe_states.clear()
            self.pump(self.app.probe_jobs)
        self.assertNotIn("Old result", self.app.key_controls["gemini"][1].cget("text"))

    def test_discard_profile_does_not_resave_it_on_key_write(self):
        self.app.legend_textbox.insert("end", "Discard this edit")
        self.app.update()
        self.assertTrue(self.app.profile_dirty)
        with patch(
            "ui.dashboard_profiles.messagebox.askyesnocancel", return_value=False
        ):
            self.assertTrue(self.app.guard_profile())
        self.assertFalse(self.app.profile_dirty)
        self.assertEqual(self.app.legend_textbox.get("1.0", "end-1c"), "")
        entry = self.app.key_controls["openai"][0]
        entry.insert(0, "offline-key")
        with patch.object(self.app, "save_current_legend") as save:
            self.app.save_key("openai")
            self.pump(self.app.save_jobs)
        save.assert_not_called()

    def test_logical_scaling_125_and_150_keeps_actions_inside_window(self):
        import customtkinter as ctk

        try:
            for factor in (1.25, 1.5):
                ctk.set_widget_scaling(factor)
                ctk.set_window_scaling(factor)
                self.app.geometry("800x540")
                for page in self.app.pages:
                    self.app.navigate(page)
                    self.app.update()
                    for widget in (
                        self.app.apply_button,
                        self.app.cancel_button,
                        self.app.save_button,
                    ):
                        self.assertLessEqual(
                            widget.winfo_rootx() + widget.winfo_width(),
                            self.app.winfo_rootx() + self.app.winfo_width(),
                        )
        finally:
            ctk.set_widget_scaling(1)
            ctk.set_window_scaling(1)
