"""Small Windows DPAPI wrapper for at-rest API keys.

Keys are never written to the configuration in plain text.  On Windows the
ciphertext is bound to the current Windows user through CryptProtectData.  On
other platforms (used only for development) the value is obfuscated, not
encrypted, and the UI says so.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes

PREFIX_DPAPI = "dpapi:"
PREFIX_OBF = "obf:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    in_blob, keep_in = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), "ARONA API key", None, None, None,
        0x01, ctypes.byref(out_blob),  # CRYPTPROTECT_UI_FORBIDDEN
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del keep_in


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    in_blob, keep_in = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        0x01, ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del keep_in


def protect(secret: str) -> str:
    if not secret:
        return ""
    raw = secret.encode("utf-8")
    if sys.platform == "win32":
        try:
            return PREFIX_DPAPI + base64.b64encode(_dpapi_protect(raw)).decode("ascii")
        except Exception:
            pass
    key = (os.environ.get("ARONA_LOCAL_SALT", "arona-schale-local") * 3).encode("utf-8")
    mixed = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return PREFIX_OBF + base64.b64encode(mixed).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith(PREFIX_DPAPI):
            return _dpapi_unprotect(base64.b64decode(value[len(PREFIX_DPAPI):])).decode("utf-8")
        if value.startswith(PREFIX_OBF):
            raw = base64.b64decode(value[len(PREFIX_OBF):])
            key = (os.environ.get("ARONA_LOCAL_SALT", "arona-schale-local") * 3).encode("utf-8")
            return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return ""
    # Backwards compatibility: treat an unprefixed non-empty string as a key.
    return value if ":" not in value else ""


def masked(secret: str, visible: int = 4) -> str:
    if not secret:
        return "未设置"
    if len(secret) <= visible * 2:
        return "•" * len(secret)
    return secret[:visible] + "••••••••" + secret[-visible:]
