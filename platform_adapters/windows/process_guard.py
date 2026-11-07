"""Windows-owned process boundary for bounded interpreter shutdown.

The guardian starts BEFORE assigning the owner to its job. It holds the sole
job handle and an inherited process handle, never a user-supplied target PID.
"""

import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from threading import Event, Thread
from time import monotonic

GRACE_SECONDS = 6.0


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class BASIC_LIMITS(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BASIC_LIMITS),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def kernel_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "DuplicateHandle": (
            [
                wintypes.HANDLE,
                wintypes.HANDLE,
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ],
            wintypes.BOOL,
        ),
        "SetHandleInformation": (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def checked(success):
    if not success:
        raise OSError("Не удалось установить границу завершения GhostAssist.")


class ProcessGuard:
    def __init__(self):
        self.process = None
        self.armed = False

    def start(self):
        if os.name != "nt":
            raise OSError("ProcessGuard requires Windows")
        kernel = kernel_api()
        job = kernel.CreateJobObjectW(None, None)
        checked(job)
        owner = wintypes.HANDLE()
        try:
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            checked(
                kernel.SetInformationJobObject(
                    job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            current = kernel.GetCurrentProcess()
            checked(
                kernel.DuplicateHandle(
                    current, current, current, ctypes.byref(owner), 0x100001, True, 0
                )
            )
            checked(kernel.SetHandleInformation(job, 1, 1))
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [int(job), owner.value]}
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "platform_adapters.windows.process_guard",
                    str(int(job)),
                    str(owner.value),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startup,
                close_fds=True,
                cwd=Path(__file__).resolve().parents[2],
            )
            ready = Event()
            response = []

            def handshake():
                response.append(self.process.stdout.read(1))
                ready.set()

            Thread(target=handshake, daemon=True).start()
            if not ready.wait(3) or response != [b"R"]:
                raise OSError("Наблюдатель GhostAssist не подтвердил готовность.")
            self.process.stdout.close()
            # Only this interpreter and children subsequently created by it join.
            checked(kernel.AssignProcessToJobObject(job, current))
        except BaseException:
            if self.process:
                self.process.kill()
                self.process.wait(timeout=3)
            raise
        finally:
            kernel.CloseHandle(job)
            if owner.value:
                kernel.CloseHandle(owner)
        return self

    def arm(self):
        if self.armed or self.process is None:
            return
        self.armed = True
        try:
            self.process.stdin.write(b"C")
            self.process.stdin.flush()
            self.process.stdin.close()
        except (OSError, ValueError):
            pass


def guarded_entry(entry):
    guard = ProcessGuard().start()
    previous = signal.getsignal(signal.SIGINT)

    def interrupted(signum, frame):
        guard.arm()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupted)
    try:
        return entry(guard.arm)
    except KeyboardInterrupt:
        return 130
    finally:
        guard.arm()
        signal.signal(signal.SIGINT, previous)


def watch(job, owner):
    kernel = kernel_api()
    closing = Event()
    signal.signal(signal.SIGINT, lambda *_: closing.set())

    def control():
        try:
            sys.stdin.buffer.read(1)
        finally:
            closing.set()

    Thread(target=control, daemon=True).start()
    sys.stdout.buffer.write(b"R")
    sys.stdout.buffer.flush()
    deadline = None
    try:
        while kernel.WaitForSingleObject(owner, 50) == 258:
            if closing.is_set() and deadline is None:
                deadline = monotonic() + GRACE_SECONDS
            if deadline is not None and monotonic() >= deadline:
                checked(kernel.TerminateJobObject(job, 124))
                kernel.WaitForSingleObject(owner, 2000)
                return
    finally:
        # Normal owner exit also closes any abandoned descendants.
        kernel.CloseHandle(job)
        kernel.CloseHandle(owner)


if __name__ == "__main__":
    watch(int(sys.argv[1]), int(sys.argv[2]))
