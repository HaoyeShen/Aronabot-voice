"""Keyboard and mouse primitives for WeChat's hold-to-talk gesture."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Optional

from .errors import VoiceSendError

log = logging.getLogger("wecvoice.input")

VK_RMENU = 0xA5
VK_MENU = 0x12
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_KEYBOARD = 1

_user32 = ctypes.windll.user32
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def _key_event(vk: int, scan: int, flags: int) -> bool:
    """Inject one keyboard event with SendInput and report whether it landed."""
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    sent = _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    return int(sent) == 1


def alt_down() -> bool:
    """Hold the RIGHT Alt (AltGr) key.

    WeChat 4.2 only listens to the right Alt for hold-to-talk; the left Alt
    does nothing.  The extended-key flag is required so the keystroke is
    delivered as VK_RMENU instead of VK_LMENU.
    """
    ok = _key_event(VK_RMENU, 0x38, KEYEVENTF_EXTENDEDKEY)
    if not ok:
        log.warning("SendInput right-Alt down was rejected (Windows error %s)", ctypes.windll.kernel32.GetLastError())
    return ok


def alt_up() -> bool:
    return _key_event(VK_RMENU, 0x38, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)


def left_down() -> None:
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def left_up() -> None:
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def click_at(x: int, y: int) -> None:
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    left_down()
    time.sleep(0.04)
    left_up()


class HoldToTalk:
    """Hold right Alt (or the voice button) while audio is played.

    Used as a context manager::

        with HoldToTalk("alt"):
            play(clip)
    """

    def __init__(self, trigger: str = "alt", button=None, *, press_delay: float = 0.0) -> None:
        if trigger not in ("alt", "button"):
            raise ValueError("trigger must be 'alt' or 'button'")
        self.trigger = trigger
        self.button = button
        self.press_delay = max(0.0, float(press_delay))
        self._pressed = False
        self._rect: Optional[tuple] = None

    def _button_rect(self) -> Optional[tuple]:
        if self.button is None:
            return None
        try:
            box = self.button.BoundingRectangle
            if box is None or box.width() <= 0 or box.height() <= 0:
                return None
            return (box.left, box.top, box.right, box.bottom)
        except Exception:  # noqa: BLE001
            return None

    def __enter__(self) -> "HoldToTalk":
        if self.trigger == "alt":
            if not alt_down():
                raise VoiceSendError(
                    "Windows rejected the simulated right-Alt key. "
                    "Start the terminal from the normal desktop (not an elevated or restricted shell) and retry."
                )
            self._pressed = True
        else:
            rect = self._button_rect()
            if rect is None:
                raise VoiceSendError(
                    "trigger='button' but the WeChat voice button was not found; use trigger='alt' instead."
                )
            self._rect = rect
            x = (rect[0] + rect[2]) // 2
            y = (rect[1] + rect[3]) // 2
            _user32.SetCursorPos(x, y)
            time.sleep(0.08)
            left_down()
            self._pressed = True
        if self.press_delay:
            time.sleep(self.press_delay)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.trigger == "alt":
                alt_up()
            else:
                left_up()
        finally:
            self._pressed = False
            time.sleep(0.05)


def release_all() -> None:
    """Best-effort cleanup after a crash while a key/button was held."""
    try:
        alt_up()
    except Exception:  # noqa: BLE001
        pass
    try:
        left_up()
    except Exception:  # noqa: BLE001
        pass
