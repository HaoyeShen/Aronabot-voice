"""Create a console-less ARONA.exe whose embedded icon is assets/arona_icon.ico.

Python's own pythonw.exe carries the Python icon.  Windows uses the executable
icon for the taskbar, so simply calling ``root.iconbitmap`` is not enough.
This script copies pythonw.exe inside the venv (same DLL search layout) and
replaces its icon group with the ARONA icon using the Win32 resource API.
"""

from __future__ import annotations

import ctypes
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
# Use the base interpreter, not the venv redirector: the redirector spawns a
# second pythonw.exe child, whose Python icon would replace ours on the
# taskbar.  A base pythonw placed inside .venv/Scripts still detects
# .venv/pyvenv.cfg and uses the virtual environment.
SOURCE = Path(sys.base_prefix) / "pythonw.exe"
TARGET = VENV / "Scripts" / "ARONA.exe"
MARKER = VENV / "Scripts" / "ARONA.iconv2"
ICON = ROOT / "assets" / "arona_icon.ico"

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0


class _GRPICONDIRENTRY(ctypes.Structure):
    _fields_ = [
        ("bWidth", wintypes.BYTE),
        ("bHeight", wintypes.BYTE),
        ("bColorCount", wintypes.BYTE),
        ("bReserved", wintypes.BYTE),
        ("wPlanes", wintypes.WORD),
        ("wBitCount", wintypes.WORD),
        ("dwBytesInRes", wintypes.DWORD),
        ("nID", wintypes.WORD),
    ]


def _parse_ico(path: Path) -> tuple[list[tuple[int, int, int, int, int, bytes]], bytes]:
    data = path.read_bytes()
    if len(data) < 6:
        raise ValueError("icon file is too short")
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1 or count < 1:
        raise ValueError("not a Windows .ico file")
    offset = 6
    entries = []
    for _ in range(count):
        width, height, colors, _reserved, planes, bpp, size, image_offset = \
            struct.unpack_from("<BBBBHHII", data, offset)
        offset += 16
        image = data[image_offset:image_offset + size]
        if len(image) != size:
            raise ValueError("truncated icon image")
        entries.append((width, height, colors, planes, bpp, image))
    group = bytearray(struct.pack("<HHH", 0, 1, count))
    for index, (width, height, colors, planes, bpp, image) in enumerate(entries, 1):
        group += struct.pack(
            "<BBBBHHIH",
            width, height, colors, 0, planes, bpp, len(image), index,
        )
    return entries, bytes(group)


def make(target: Path = TARGET) -> Path:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"missing {SOURCE}")
    if not ICON.is_file():
        raise FileNotFoundError(f"missing {ICON}")
    if (target.exists() and MARKER.exists() and
            target.stat().st_mtime >= max(SOURCE.stat().st_mtime, ICON.stat().st_mtime)):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, target)

    entries, group = _parse_ico(ICON)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    begin.restype = wintypes.HANDLE
    update = kernel32.UpdateResourceW
    update.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID,
                       wintypes.WORD, wintypes.LPVOID, wintypes.DWORD]
    update.restype = wintypes.BOOL
    end = kernel32.EndUpdateResourceW
    end.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    end.restype = wintypes.BOOL

    handle = begin(str(target), False)
    if not handle:
        raise OSError(ctypes.get_last_error(), "BeginUpdateResourceW failed")
    try:
        for index, (_w, _h, _c, _p, _b, image) in enumerate(entries, 1):
            if not update(handle, ctypes.c_void_p(RT_ICON),
                          ctypes.c_void_p(index), LANG_NEUTRAL,
                          image, len(image)):
                raise OSError(ctypes.get_last_error(), "UpdateResourceW icon failed")
        if not update(handle, ctypes.c_void_p(RT_GROUP_ICON),
                      ctypes.c_void_p(1), LANG_NEUTRAL,
                      group, len(group)):
            raise OSError(ctypes.get_last_error(), "UpdateResourceW group failed")
    except Exception:
        end(handle, True)
        raise
    if not end(handle, False):
        raise OSError(ctypes.get_last_error(), "EndUpdateResourceW failed")
    try:
        MARKER.write_text("base-pythonw-v2", encoding="utf-8")
    except OSError:
        pass
    return target


if __name__ == "__main__":
    try:
        print(make())
    except Exception as exc:
        print(f"ARONA.exe 生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
