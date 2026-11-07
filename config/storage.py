"""Bounded, reentrant cross-process configuration transactions."""

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def configuration_lock(path: Path, timeout: float = 10):
    key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("Конфигурация занята другим процессом.")
    try:
        held = getattr(_local, "held", None)
        if held is None:
            held = _local.held = set()
        if key in held:
            yield
            return
        # Never remove this sidecar: unlinking an actively locked inode permits
        # another process to acquire a different lock for the same target.
        descriptor = os.open(
            str(path.resolve()) + ".lock", os.O_RDWR | os.O_CREAT, 0o600
        )
        with os.fdopen(descriptor, "r+b") as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _file_lock(stream, True)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Конфигурация занята другим процессом."
                        ) from None
                    time.sleep(0.01)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                _file_lock(stream, False)
    finally:
        lock.release()


def _file_lock(stream, acquire):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(
            stream.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1
        )
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


def protect_staging(source: Path, staging: Path):
    """Apply the target access policy before any secrets enter the staging file."""
    if not source.exists():
        return
    if os.name != "nt":
        os.chmod(staging, source.stat().st_mode & 0o777)
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get.restype = wintypes.BOOL
    set_security = api.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    size = wintypes.DWORD()
    get(str(source), 4, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not get(str(source), 4, descriptor, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION:
    # inherited directory permissions must not broaden the staging policy.
    if not set_security(str(staging), 4 | 0x80000000, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


def replace_file(staging: Path, target: Path):
    if os.name != "nt" or not target.exists():
        os.replace(staging, target)
        return
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace.restype = wintypes.BOOL
    # Do not use IGNORE_ACL_ERRORS: inability to preserve protection is a failure.
    if not replace(str(target.resolve()), str(staging.resolve()), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())
