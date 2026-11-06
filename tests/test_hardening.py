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
                patch("core.profile_store.os.replace", side_effect=OSError("denied")),
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
    def test_failed_switch_and_selector_save_restores_previous_values(self):
        app = SimpleNamespace(
            settings=SimpleNamespace(vad_enabled=False, audio_device="Headset"),
            vad_switch=Mock(),
            device_combo=Mock(),
            _save=Mock(return_value=False),
            _restore_switch=DashboardApp._restore_switch,
        )
        app.vad_switch.get.return_value = 1
        DashboardApp._on_vad_changed(app)
        app.vad_switch.deselect.assert_called_once()
        DashboardApp._on_device_changed(app, "Другой")
        app.device_combo.set.assert_called_once_with("Headset")

    def test_unexpected_device_scan_failure_returns_result(self):
        from queue import Queue
        from threading import Event

        completed = Event()
        app = SimpleNamespace(
            device_refreshing=False,
            refresh_devices_button=Mock(),
            device_updates=Queue(maxsize=1),
        )

        def fail():
            completed.set()
            raise RuntimeError("sensitive details")

        with patch("ui.dashboard.list_loopback_devices", side_effect=fail):
            DashboardApp._refresh_devices(app)
            self.assertTrue(completed.wait(2))
            devices, error = app.device_updates.get(timeout=2)
        self.assertEqual(devices, [])
        self.assertNotIn("sensitive", error)


if __name__ == "__main__":
    unittest.main()
