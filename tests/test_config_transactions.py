import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from config.env_file import update_env
from config.settings import load_settings
from config.storage import configuration_lock, protect_staging


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"
        self.path.write_text(
            "# keep\nGEMINI_API_KEY=private-test-key\n", encoding="utf-8"
        )

    def test_parallel_process_migrations_and_patches_preserve_every_update(self):
        code = """
import sys
from pathlib import Path
from config.settings import load_settings
from config.env_file import update_env
path = Path(sys.argv[1])
for n in range(8):
    load_settings(path)
    update_env({f'WRITER_{sys.argv[2]}_{n}': str(n)}, path)
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(self.path), str(i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for i in range(6)
        ]
        for process in processes:
            _, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error.decode(errors="replace"))
        values = dotenv_values(self.path)
        self.assertEqual(values["GEMINI_API_KEY"], "private-test-key")
        self.assertEqual(values["SCHEMA_VERSION"], "1")
        self.assertEqual(len([key for key in values if key.startswith("WRITER_")]), 48)
        self.assertEqual(
            self.path.read_text(encoding="utf-8").count("SCHEMA_VERSION"), 1
        )

    def test_thread_transactions_and_nested_load_do_not_deadlock(self):
        def write(index):
            with configuration_lock(self.path):
                load_settings(self.path)
                update_env({f"THREAD_{index}": str(index)}, self.path)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(24)))
        self.assertEqual(len(dotenv_values(self.path)), 26)

    def test_process_lock_has_bounded_wait_and_recovers_after_process_exit(self):
        code = """
import sys, time
from pathlib import Path
from config.storage import configuration_lock
with configuration_lock(Path(sys.argv[1])):
    print('ready', flush=True)
    time.sleep(30)
"""
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", code, str(self.path)], stdout=subprocess.PIPE
        )
        try:
            self.assertEqual(
                process.stdout.readline(),
                b"ready\r\n" if os.name == "nt" else b"ready\n",
            )
            with self.assertRaises(TimeoutError):
                with configuration_lock(self.path, timeout=0.03):
                    self.fail("another process holds the transaction")
        finally:
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()
        update_env({"MEMORY_DEPTH": "4"}, self.path)
        self.assertEqual(load_settings(self.path).memory_depth, 4)

    @unittest.skipUnless(os.name == "nt", "requires Windows DACL APIs")
    def test_explicit_windows_dacl_preserved_before_staging_write_and_after_replace(
        self,
    ):
        def acl(path, restrict=False):
            script = """
$p=$env:GHOSTASSIST_TEST_PATH
$acl=[System.IO.File]::GetAccessControl($p)
if ($env:GHOSTASSIST_TEST_RESTRICT -eq '1') {
    $acl.SetAccessRuleProtection($true,$false)
    $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule=[System.Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow')
    $acl.SetAccessRule($rule)
    [System.IO.File]::SetAccessControl($p,$acl)
    $acl=[System.IO.File]::GetAccessControl($p)
}
$acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
"""
            return subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop';" + script,
                ],
                env={
                    **os.environ,
                    "GHOSTASSIST_TEST_PATH": str(path),
                    "GHOSTASSIST_TEST_RESTRICT": "1" if restrict else "0",
                },
                text=True,
            ).strip()

        expected = acl(self.path, True)
        staged = []

        def protect(source, temp):
            protect_staging(source, temp)
            self.assertEqual(temp.stat().st_size, 0)
            staged.append(acl(temp))

        with patch("config.env_file.protect_staging", side_effect=protect):
            update_env({"MEMORY_DEPTH": "4"}, self.path)
        self.assertEqual(
            [s.replace("D:PAI", "D:P") for s in staged],
            [expected.replace("D:PAI", "D:P")],
        )
        self.assertEqual(acl(self.path), expected)
        self.assertEqual(load_settings(self.path).memory_depth, 4)

    def test_staging_security_failure_leaves_original_untouched(self):
        before = self.path.read_bytes()
        with patch("config.env_file.protect_staging", side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                update_env({"MEMORY_DEPTH": "4"}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".ghostassist-env-*.tmp")), [])
