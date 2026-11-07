import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.profile_store import write_profile
from ui.dashboard import DashboardApp


class ProfilePersistenceTests(unittest.TestCase):
    def test_failed_replace_preserves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.md"
            path.write_text("original", encoding="utf-8")
            with (
                patch("core.profile_store.profile_path", return_value=path),
                patch("core.profile_store.replace_file", side_effect=OSError("denied")),
                self.assertRaises(OSError),
            ):
                write_profile("profile.md", "new content")
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_successful_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.md"
            path.write_text("original", encoding="utf-8")
            with patch("core.profile_store.profile_path", return_value=path):
                write_profile("profile.md", "новый текст")
            self.assertEqual(path.read_text(encoding="utf-8"), "новый текст")


class DashboardRecoveryTests(unittest.TestCase):
    def test_environment_override_is_not_silently_saved(self):
        from config.settings import Settings
        from ui.dashboard_actions import safe_save

        with (
            patch.dict(os.environ, {"VAD_ENABLED": "true"}),
            patch("ui.dashboard_settings.update_env") as update,
        ):
            result = safe_save(
                Settings(_env_file=None),
                {"VAD_ENABLED": "false"},
                Path("unused.env"),
                None,
            )
        update.assert_not_called()
        self.assertIn("VAD_ENABLED", result.error)

    def test_profile_selection_does_not_change_editor_when_save_fails(self):
        app = SimpleNamespace(
            loaded_profile="old.md",
            profile_selector=Mock(),
            legend_textbox=Mock(),
            guard_profile=Mock(return_value=False),
        )
        DashboardApp.load_selected_profile(app, "new.md")
        app.profile_selector.set.assert_called_once_with("old.md")
        app.legend_textbox.delete.assert_not_called()

    def test_failed_save_preserves_saved_values_and_draft(self):
        from dashboard_test_support import TransactionHarness, finish

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            app = TransactionHarness(Path(directory) / ".env")
            app.draft.changes = {"VAD_ENABLED": "true", "AUDIO_DEVICE": "Headset"}
            with patch("ui.dashboard_settings.update_env", side_effect=PermissionError):
                app.request_save()
                finish(app.save_jobs)
            self.assertFalse(app.settings.vad_enabled)
            self.assertEqual(app.draft.changes["AUDIO_DEVICE"], "Headset")
            app.sync_form.assert_not_called()
            app.save_jobs.close()

    def test_unexpected_device_scan_failure_returns_result(self):
        from dashboard_test_support import finish

        from ui.dashboard_jobs import JobRunner

        variable = Mock()
        variable.get.return_value = ""
        app = SimpleNamespace(
            device_jobs=JobRunner(),
            refresh_devices_button=Mock(),
            audio_capture=Mock(),
            variable=Mock(return_value=variable),
            device_combo=Mock(),
            device_status=Mock(),
        )
        app.audio_capture.list_devices.side_effect = RuntimeError("sensitive details")
        DashboardApp._refresh_devices(app)
        finish(app.device_jobs)
        self.assertNotIn(
            "sensitive", app.device_status.configure.call_args.kwargs["text"]
        )
        app.refresh_devices_button.configure.assert_called_with(state="normal")
        app.device_jobs.close()


if __name__ == "__main__":
    unittest.main()
