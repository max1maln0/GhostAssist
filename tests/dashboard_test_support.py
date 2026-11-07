"""Headless helpers for Dashboard's real asynchronous settings actions."""

import time
from unittest.mock import Mock

from config.settings import load_settings
from ui.dashboard_actions import SettingsActions
from ui.dashboard_jobs import JobRunner
from ui.dashboard_settings import SettingsDraft


def finish(runner):
    deadline = time.monotonic() + 3
    while runner.busy and time.monotonic() < deadline:
        runner.poll()
        time.sleep(0.005)
    if runner.busy:
        raise AssertionError("Background job did not complete")


class TransactionHarness(SettingsActions):
    def __init__(self, path):
        self.settings_path = path
        self.settings = load_settings(path)
        self.draft = SettingsDraft(self.settings)
        self.save_jobs = JobRunner()
        self.prepare_hotkeys = Mock(return_value=None)
        self.set_error = Mock()
        self.set_saving = Mock()
        self.sync_form = Mock()
        self.profile_dirty = False
        self._replace_dashboard_hotkey = Mock()
        self._stop_process = Mock()
        self.restart_pending = False
        self.probe_revision = 0
        self.probe_states = {}
