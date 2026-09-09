import ctypes
import json
import os
import time
from contextlib import contextmanager


@contextmanager
def open_snapshot(path):
    if os.name != "nt":
        with path.open("r", encoding="utf-8") as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        yield stream


def read_snapshot(path):
    with open_snapshot(path) as stream:
        return json.load(stream)


# Windows errors raised while another process (indexer, antivirus, a reader without
# FILE_SHARE_DELETE) briefly holds the snapshot: sharing violation, lock violation and
# "unable to remove the file to be replaced".
TRANSIENT_REPLACE_ERRORS = frozenset({32, 33, 1175})
REPLACE_ATTEMPTS = 20
REPLACE_RETRY_SECONDS = 0.05


def replace_file(path, temporary):
    if os.name == "nt" and path.exists():
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.ReplaceFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ]
        kernel.ReplaceFileW.restype = wintypes.BOOL
        if not kernel.ReplaceFileW(str(path), str(temporary), None, 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        temporary.replace(path)


def save_snapshot(path, values):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2), encoding="utf-8")
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            replace_file(path, temporary)
            return
        except OSError as error:
            if (
                attempt == REPLACE_ATTEMPTS
                or getattr(error, "winerror", None) not in TRANSIENT_REPLACE_ERRORS
            ):
                raise
            time.sleep(REPLACE_RETRY_SECONDS)