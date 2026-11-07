"""Lazy-page and warm-navigation integration regression."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.dashboard import PAGES, DashboardApp
from ui.dashboard_components import ScrollbarBridge


class LazyDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        directory = Path(self.temp.name)
        (directory / ".env").write_text("SCHEMA_VERSION=2\n", encoding="utf-8")
        self.app = DashboardApp(
            settings_path=directory / ".env",
            profiles_directory=directory,
            start_integrations=False,
        )
        self.app.update()
        self.addCleanup(self.app.destroy)

    def test_only_overview_constructed_and_100_transitions_reuse_widgets(self):
        self.assertEqual(tuple(self.app.pages), ("overview",))
        self.app.variable("MEMORY_DEPTH").set("4")
        for page in PAGES:
            self.app.navigate(page)
            self.app.update()
        identities = {key: id(frame) for key, frame in self.app.pages.items()}
        self.app.navigate("providers")
        self.app.pages["providers"]._parent_canvas.yview_moveto(0.5)
        scroll = self.app.pages["providers"]._parent_canvas.yview()
        self.app.navigate("overview")
        for index in range(100):
            self.app.navigate(tuple(PAGES)[index % 7])
            self.app.update_idletasks()
        self.app.navigate("providers")
        self.app.update()
        self.assertEqual(
            identities, {key: id(frame) for key, frame in self.app.pages.items()}
        )
        self.assertEqual(self.app.variable("MEMORY_DEPTH").get(), "4")
        self.assertEqual(self.app.draft.changes["MEMORY_DEPTH"], "4")
        self.assertAlmostEqual(
            self.app.pages["providers"]._parent_canvas.yview()[0], scroll[0], delta=0.02
        )
        self.assertEqual(len(self.app.timings["navigation_ms"]), 108)

    def test_failed_page_keeps_previous_page_and_can_retry(self):
        with patch.object(
            PAGES["providers"][1], "build", side_effect=RuntimeError("private")
        ):
            self.app.navigate("providers")
        self.assertEqual(self.app.current_page, "overview")
        self.assertNotIn("providers", self.app.pages)
        self.assertNotIn("private", self.app.last_error)
        self.app.navigate("providers")
        self.assertEqual(self.app.current_page, "providers")

    def test_scroll_bridge_cancels_timer_and_rejects_updates_after_close(self):
        from unittest.mock import Mock

        owner, scrollbar = Mock(), Mock()
        owner.winfo_toplevel.return_value.destroying = False
        owner.after.return_value = "owned-timer"
        bridge = ScrollbarBridge(owner, scrollbar)
        bridge.set(0, 0.5)
        bridge.set(0.1, 0.6)
        owner.after.assert_called_once()
        bridge.close()
        owner.after_cancel.assert_called_once_with("owned-timer")
        bridge.set(0, 1)
        owner.after.assert_called_once()
        self.assertIsNone(bridge.pending)

    def test_exit_during_async_registration_is_retried(self):
        from unittest.mock import Mock

        self.app.save_jobs = Mock(busy=True)
        self.app.exit_app()
        self.assertTrue(self.app.exit_requested.is_set())
        self.assertFalse(self.app.closing)
