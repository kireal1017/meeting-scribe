"""Move a folder to the Windows Recycle Bin (recoverable), never a permanent delete."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040  # -> Recycle Bin
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000  # ask before deleting for good when the drive has no Recycle Bin


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def move_to_trash(path: Path) -> None:
    """Raises OSError when the folder could not be moved (in use, cancelled, ...)."""
    op = _SHFILEOPSTRUCTW(
        wFunc=FO_DELETE,
        pFrom=str(path.resolve()) + "\0",  # list of paths ending in a double NUL
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        | FOF_WANTNUKEWARNING,
    )
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted or path.exists():
        raise OSError(f"휴지통으로 옮기지 못했습니다 (코드 {rc:#x}): {path}")
