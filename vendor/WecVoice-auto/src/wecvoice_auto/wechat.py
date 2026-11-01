"""Minimal WeChat 4.x window handling.

We deliberately do **not** patch WeChat's memory or read its database.  The
library only needs a foreground main window and the currently open
conversation; everything else is done by WeChat itself when we hold Alt.

UI Automation is used opportunistically to verify the open chat and to focus
the input box.  On a cold WeChat 4.x start the Qt accessibility tree may not
be materialised yet, so every UIA lookup degrades gracefully to plain Win32
window handling.
"""

from __future__ import annotations

import ctypes
import logging
import re
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

from .errors import ChatMismatchError, WeChatNotRunningError

log = logging.getLogger("wecvoice.wechat")

MAIN_CLASS = "mmui::MainWindow"
CHAT_INPUT_AID = "chat_input_field"
_TITLE_HINTS = ("微信", "Weixin")
_CHAT_NAME_TAIL = re.compile(r"(?:(?:长按|按住)鼠标\s*)?语音输入(?:文字)?$")
_FILEHELPER_ALIASES = {"文件传输助手", "filehelper", "file transfer", "filetransfer"}

_user32 = ctypes.windll.user32
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.SetFocus.argtypes = [wintypes.HWND]


def _window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def clean_chat_name(name: Optional[str]) -> str:
    if not name:
        return ""
    stripped = str(name).strip()
    head = _CHAT_NAME_TAIL.sub("", stripped).strip()
    return head or stripped


def _enum_main_windows() -> List[int]:
    found: List[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            title = _window_text(hwnd)
            class_name = _class_name(hwnd)
            if not title:
                return True
            title_match = any(hint.lower() in title.lower() for hint in _TITLE_HINTS)
            class_match = MAIN_CLASS == class_name or (
                class_name.startswith("Qt") and title_match
            )
            if title_match and class_match:
                found.append(int(hwnd))
        except Exception:  # noqa: BLE001
            pass
        return True

    _user32.EnumWindows(callback, 0)
    return found


class WeChatWindow:
    """A thin wrapper around the WeChat main window."""

    def __init__(self, *, title_hints: Tuple[str, ...] = _TITLE_HINTS) -> None:
        self.title_hints = title_hints
        self.hwnd: Optional[int] = None
        self.title: str = ""
        self._uia = None
        self._uia_failed = False

    # ------------------------------------------------------------------ window
    def find(self) -> bool:
        handles = _enum_main_windows()
        if not handles:
            self.hwnd = None
            self.title = ""
            return False
        # Prefer a window whose class is exactly mmui::MainWindow.
        handles.sort(key=lambda hwnd: (0 if _class_name(hwnd) == MAIN_CLASS else 1, -hwnd))
        self.hwnd = handles[0]
        self.title = _window_text(self.hwnd)
        return True

    def ensure(self) -> "WeChatWindow":
        if not self.find():
            raise WeChatNotRunningError(
                "WeChat main window not found. Sign in to WeChat 4.1.9+ and keep the main window open."
            )
        return self

    def is_foreground(self) -> bool:
        if self.hwnd is None:
            return False
        try:
            return int(_user32.GetForegroundWindow()) == int(self.hwnd)
        except Exception:  # noqa: BLE001
            return False

    def activate(self) -> bool:
        if self.hwnd is None and not self.find():
            return False
        try:
            if _user32.IsIconic(self.hwnd):
                _user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            _user32.BringWindowToTop(self.hwnd)
            if _user32.SetForegroundWindow(self.hwnd):
                time.sleep(0.2)
                if self.is_foreground():
                    return True
            # Windows may refuse foreground changes from a background process.
            foreground = _user32.GetForegroundWindow()
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            target_tid = _user32.GetWindowThreadProcessId(self.hwnd, None)
            fg_tid = _user32.GetWindowThreadProcessId(foreground, None)
            attached = []
            for tid in {target_tid, fg_tid}:
                if tid and tid != current_tid:
                    if _user32.AttachThreadInput(current_tid, tid, True):
                        attached.append(tid)
            try:
                _user32.SetForegroundWindow(self.hwnd)
                _user32.SetFocus(self.hwnd)
            finally:
                for tid in attached:
                    _user32.AttachThreadInput(current_tid, tid, False)
            time.sleep(0.25)
            return self.is_foreground()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to activate the WeChat window: %s", exc)
            return False

    def rect(self) -> Optional[Tuple[int, int, int, int]]:
        if self.hwnd is None and not self.find():
            return None
        try:
            box = wintypes.RECT()
            _user32.GetWindowRect(self.hwnd, ctypes.byref(box))
            return (box.left, box.top, box.right, box.bottom)
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------------------- UIA
    def uia(self):
        if self._uia is not None:
            return self._uia
        if self._uia_failed:
            return None
        try:
            import uiautomation as auto
        except Exception as exc:  # noqa: BLE001
            log.debug("uiautomation is unavailable: %s", exc)
            self._uia_failed = True
            return None
        try:
            control = auto.WindowControl(searchDepth=1, ClassName=MAIN_CLASS)
            if not control.Exists(0.8, 0.2):
                control = auto.WindowControl(searchDepth=1, Name=self.title or "微信")
            if control.Exists(0.8, 0.2):
                self._uia = control
                return control
        except Exception as exc:  # noqa: BLE001
            log.debug("UIA main window lookup failed: %s", exc)
        self._uia_failed = True
        return None

    def chat_input(self):
        root = self.uia()
        if root is None:
            return None
        try:
            edit = root.EditControl(AutomationId=CHAT_INPUT_AID)
            if edit.Exists(0.5, 0.1):
                return edit
        except Exception:  # noqa: BLE001
            pass
        try:
            import uiautomation as auto
        except Exception:  # noqa: BLE001
            return None
        try:
            for control, _depth in auto.WalkControl(root, maxDepth=30):
                try:
                    if control.ControlTypeName != "EditControl":
                        continue
                    if "搜索" in (control.Name or ""):
                        continue
                    if control.AutomationId == CHAT_INPUT_AID:
                        return control
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return None

    def current_chat(self) -> str:
        edit = self.chat_input()
        if edit is None:
            return ""
        try:
            return clean_chat_name(edit.Name)
        except Exception:  # noqa: BLE001
            return ""

    def ensure_chat(self, chat: Optional[str]) -> str:
        """Verify (when possible) that ``chat`` is the open conversation."""
        if not chat:
            return self.current_chat()
        current = self.current_chat()
        if not current:
            log.warning("Cannot read the current chat name via UIA; skipping chat verification (target: %s)", chat)
            return current
        if _same_chat(current, chat):
            return current
        raise ChatMismatchError(
            f"Current chat is {current!r}, not {chat!r}. Open the target chat in WeChat first; "
            "this library only sends and does not search or switch chats."
        )

    def focus_input(self, *, click_fallback: bool = True) -> bool:
        edit = self.chat_input()
        if edit is not None:
            try:
                edit.SetFocus()
                edit.Click(simulateMove=False, waitTime=0.05)
                time.sleep(0.15)
                return True
            except Exception as exc:  # noqa: BLE001
                log.debug("UIA focus on the input box failed: %s", exc)
        if not click_fallback:
            return False
        box = self.rect()
        if not box:
            return False
        left, top, right, bottom = box
        width = max(1, right - left)
        height = max(1, bottom - top)
        x = int(left + width * 0.55)
        y = int(bottom - min(95, max(60, height * 0.12)))
        try:
            _user32.SetCursorPos(x, y)
            time.sleep(0.08)
            _user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.05)
            _user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.2)
            log.debug("Clicked the input area by coordinates (%d, %d)", x, y)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Coordinate fallback focus failed: %s", exc)
            return False

    def voice_button(self):
        """Find the native voice button when ``trigger='button'`` is used."""
        root = self.uia()
        if root is None:
            return None
        try:
            import uiautomation as auto
        except Exception:  # noqa: BLE001
            return None
        candidates = []
        try:
            for control, _depth in auto.WalkControl(root, maxDepth=40):
                try:
                    if "Button" not in control.ControlTypeName:
                        continue
                    name = (control.Name or "").strip()
                    aid = (control.AutomationId or "").lower()
                    if name in ("语音", "按住说话", "发送语音", "语音消息"):
                        candidates.append(control)
                    elif any(token in aid for token in ("voice", "audio", "record")):
                        candidates.append(control)
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed to find the voice button: %s", exc)
        return candidates[0] if candidates else None


def _same_chat(current: str, expected: str) -> bool:
    left = current.strip().lower()
    right = expected.strip().lower()
    if left == right:
        return True
    if right in _FILEHELPER_ALIASES and left in _FILEHELPER_ALIASES:
        return True
    return False
