"""Per-user, per-session Windows process guard and activation signal."""

import ctypes
import getpass
import hashlib
from ctypes import wintypes


class SingleInstance:
    def __init__(self, role):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        kernel.CreateEventW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel.SetEvent.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        user = hashlib.sha256(getpass.getuser().casefold().encode()).hexdigest()[:16]
        prefix = f"Local\\GhostAssist-{user}-{role}"
        self.event = kernel.CreateEventW(None, False, False, prefix + "-activate")
        if not self.event:
            raise ctypes.WinError(ctypes.get_last_error())
        self.mutex = kernel.CreateMutexW(None, False, prefix + "-instance")
        if not self.mutex:
            kernel.CloseHandle(self.event)
            raise ctypes.WinError(ctypes.get_last_error())
        self.primary = ctypes.get_last_error() != 183

    def activate(self):
        if not self.kernel.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def activated(self):
        return self.kernel.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        if self.mutex:
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
        if self.event:
            self.kernel.CloseHandle(self.event)
            self.event = None
