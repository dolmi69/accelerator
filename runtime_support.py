"""Small OS adapters shared by the local launchers and the laboratory."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import subprocess
import sys
import time


def is_windows():
    return sys.platform == 'win32'


@contextmanager
def file_lock(path, *, timeout=30):
    """Lock a stable byte on Windows, or the file on macOS/Linux."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        if is_windows():
            import msvcrt
            if not path.stat().st_size:
                lock.write(b'\0')
                lock.flush()

            def acquire():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another local process holds this lock.') from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            release()


def runtime_environment():
    """Keep OS essentials; do not inherit the accelerator's keys or DB URL."""
    env = {'PATH': os.defpath, 'PYTHONNOUSERSITE': '1', 'PYTHONUTF8': '1',
           'PYTHONIOENCODING': 'utf-8', 'LANG': 'en_US.UTF-8'}
    if is_windows():
        for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'):
            if os.environ.get(name):
                env[name] = os.environ[name]
    return env


def detached_process_options():
    if is_windows():
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}


def stop_windows_process(pid, *, timeout=4):
    """Terminate and wait on one process handle; never probe via os.kill(pid, 0)."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)  # terminate + synchronize
    if not handle:
        if ctypes.get_last_error() == 87:  # process has already exited
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 0) == 0:
            return
        if not kernel.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        if result == 258:
            raise TimeoutError('Local server did not stop in time.')
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
