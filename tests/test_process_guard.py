"""Owned Windows job boundary, tested using real isolated Python processes."""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows process ownership")
class ProcessGuardTests(unittest.TestCase):
    def run_code(self, body):
        code = (
            "from platform_adapters.windows.process_guard import guarded_entry\n"
            + body
            + "\nraise SystemExit(guarded_entry(entry))\n"
        )
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            out, error = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(3)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(error, b"")
        return process.returncode, out

    def test_normal_exit_does_not_wait_for_grace(self):
        started = time.monotonic()
        code, out = self.run_code(
            'def entry(arm):\n print("DONE",flush=True)\n return 0'
        )
        self.assertEqual(code, 0)
        self.assertIn(b"DONE", out)
        self.assertLess(time.monotonic() - started, 4)

    def test_uncooperative_executor_cannot_hold_interpreter(self):
        code, _ = self.run_code(
            "from concurrent.futures import ThreadPoolExecutor\nfrom threading import Event\ndef entry(arm):\n pool=ThreadPoolExecutor(1)\n pool.submit(Event().wait)\n pool.shutdown(wait=False,cancel_futures=True)\n return 0"
        )
        self.assertEqual(code, 124)

    def test_keyboard_interrupt_arms_boundary_before_interpreter_join(self):
        code, _ = self.run_code(
            "import signal\nfrom concurrent.futures import ThreadPoolExecutor\nfrom threading import Event\ndef entry(arm):\n pool=ThreadPoolExecutor(1)\n pool.submit(Event().wait)\n signal.raise_signal(signal.SIGINT)"
        )
        self.assertEqual(code, 124)

    def test_owner_exit_reaps_its_child_without_killing_another_process(self):
        from ctypes import wintypes

        from platform_adapters.windows.process_guard import kernel_api

        code = """from platform_adapters.windows.process_guard import guarded_entry
import subprocess,sys

def entry(arm):
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 print(child.pid,flush=True)
 sys.stdin.buffer.read(1)
 return 0
raise SystemExit(guarded_entry(entry))
"""
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        kernel = kernel_api()
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = None
        try:
            child = int(process.stdout.readline())
            handle = kernel.OpenProcess(0x100000, False, child)
            self.assertTrue(handle)
            process.communicate(b"x", timeout=8)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
            self.assertIsNone(unrelated.poll())
        finally:
            if handle:
                kernel.CloseHandle(handle)
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
            unrelated.kill()
            unrelated.wait(3)
