"""Real process assertions: a hidden window is not proof of termination."""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "offline_shutdown_probe.py"


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class ProcessShutdownTests(unittest.TestCase):
    def probe(self, scenario, mode="controller", auto=False):
        command = [sys.executable, str(PROBE), mode, "--scenario", scenario]
        if auto:
            command += ["--auto-exit", "1"]
        started = time.monotonic()
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT
        )
        try:
            output, error = process.communicate(timeout=14)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(3)
        self.assertLess(time.monotonic() - started, 14)
        self.assertIn(b"GHOST_QA_OWNER", output)
        self.assertEqual(
            process.returncode,
            124 if scenario == "hang" and mode != "dashboard" else 0,
            error.decode(errors="replace"),
        )
        return process.returncode

    def test_controller_cancellation_record_vad_generation_and_review_exit(self):
        for scenario in ("generation", "record", "vad", "review"):
            with self.subTest(scenario=scenario):
                self.probe(scenario)

    def test_noncooperative_executor_has_bounded_process_exit(self):
        self.probe("hang")

    def test_service_real_ui_exit_during_generation(self):
        self.probe("generation", "service", True)

    def test_dashboard_and_managed_hung_service_release_process_tree(self):
        self.probe("hang", "dashboard", True)
