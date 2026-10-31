"""WeChat desktop integration without any wxauto dependency.

The only supported transport is the optional ``wechatauto-replica`` backend:
read-only local database listening plus UI Automation sending after its Qt
accessibility gate is activated.  OCR and screen capture are explicitly
disabled; only the message database, UIA controls, and the clipboard are used.
"""

from __future__ import annotations

import ctypes
import logging
import re
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import re

from .paths import DATA

log = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    chat: str
    sender: str
    content: str
    msg_type: str = "text"
    external_id: str = ""
    attr: str = "friend"
    chat_type: str = "friend"
    direction: str = "in"          # "in", "out", or "system"
    origin: str = "friend"          # friend / self / bot_echo / system
    image_path: str = ""
    raw: object | None = None
    received_at: float = field(default_factory=time.time)

    @property
    def is_self(self) -> bool:
        return self.direction == "out" or self.attr in ("self", "system")

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"


@dataclass
class HistoryMessage:
    chat: str
    sender: str
    content: str
    role: str
    msg_type: str = "text"
    external_id: str = ""
    attr: str = "friend"
    direction: str = "in"
    origin: str = "friend"
    received_at: float = field(default_factory=time.time)


@dataclass
class SendResult:
    ok: bool
    message: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


def detect_wechat_backends() -> dict[str, bool]:
    import importlib.util
    return {"wechatauto": importlib.util.find_spec("wechatauto") is not None}


class BaseWeChatAdapter:
    name = "base"

    def __init__(self, config: dict, on_message: Callable[[IncomingMessage], None],
                 on_status: Callable[[str, str], None] | None = None) -> None:
        self.config = config
        self.on_message = on_message
        self.on_status = on_status or (lambda kind, text: None)
        self._targets: dict[str, dict] = {}
        self._running = False
        self._last_message_at = 0.0
        self.connected = False
        self.self_name = ""
        self.backend_label = self.name
        self._recent_sent: dict[str, list[tuple[float, str]]] = {}
        self._sent_lock = threading.Lock()

    @staticmethod
    def _normalise_sent(text: str) -> str:
        return " ".join(str(text or "").split()).strip()

    def _remember_sent(self, chat: str, text: str, *, kind: str = "text") -> None:
        value = self._normalise_sent(text)
        if not value:
            return
        now = time.time()
        with self._sent_lock:
            queue = self._recent_sent.setdefault(chat, [])
            queue.append((now, value))
            self._recent_sent[chat] = [(ts, value) for ts, value in queue
                                       if now - ts < 180][-20:]

    def _is_recent_sent(self, chat: str, text: str, window: float = 90.0) -> bool:
        value = self._normalise_sent(text)
        if not value:
            return False
        now = time.time()
        with self._sent_lock:
            queue = self._recent_sent.get(chat, [])
            return any(now - ts <= window and value == sent for ts, sent in queue)

    def start(self, targets: list[dict]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        self._running = False
        self.connected = False

    def send_text(self, chat: str, text: str) -> SendResult:
        raise NotImplementedError

    def send_quoted_text(self, chat: str, text: str, quote_text: str = "",
                         quote_kind: str = "text") -> SendResult:
        """Adapters without native quote support fall back to a normal text."""
        return self.send_text(chat, text)

    def ensure_chat_open(self, chat: str) -> bool:
        return False

    def _right_click_quote_message(self, chat: str, quote_text: str,
                                   quote_kind: str = "text") -> bool:
        """Right-click the specific incoming bubble that ARONA is answering."""
        if not quote_text.strip() and quote_kind not in ("image", "voice"):
            return False
        uia = self._uia
        if uia is None:
            return False
        if uia.current_chat() != chat and not uia.open_chat(chat):
            return False
        cleaned = re.sub(r"^\s*@\S+\s*", "", quote_text)
        cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", cleaned)
        candidates = []
        for value in (quote_text, cleaned):
            needle = re.sub(r"\s+", "", value)[:40]
            if needle and needle not in candidates:
                candidates.append(needle)
        if not candidates and quote_kind not in ("image", "voice"):
            return False

        def predicate(class_name: str, name: str) -> bool:
            normalized = re.sub(r"\s+", "", name or "")
            if quote_kind in ("image", "voice"):
                marker = "ChatImageItemView" if quote_kind == "image" else "ChatVoiceItemView"
                return marker in class_name and not normalized.startswith("引用")
            return (any(needle in normalized for needle in candidates) and
                    not normalized.startswith("引用") and
                    "ChatTextItemView" in class_name)

        found = uia.find_in_message_list(predicate, max_scrolls=20)
        if not found:
            return False
        _class_name, _name, rect = found
        # The row rectangle spans the whole chat width; its geometric center
        # is empty space for a left-aligned incoming bubble.  Find the
        # non-background content column as the upstream latest-row helper does.
        # This method is only used for the incoming message being answered,
        # which is left-aligned in WeChat.  Clicking the left content area is
        # more reliable than the row centre (which is empty background).
        width = rect.right - rect.left
        cx = rect.left + min(120, max(36, width // 5))
        cx = max(rect.left + 8, min(rect.right - 8, cx))
        cy = max(rect.top + 8, min(rect.bottom - 8,
                                   (rect.top + rect.bottom) // 2))
        uia._set_cursor(cx, cy)
        time.sleep(0.2)
        uia._right_click()
        time.sleep(0.8)
        return True

    def send_quoted_text(self, chat: str, text: str, quote_text: str = "",
                         quote_kind: str = "text") -> SendResult:
        if not text.strip():
            return SendResult(False, "消息内容为空")
        username = self._usernames.get(chat, "")
        before = self._last_seq(username) if username else 0
        with self._ui_lock:
            if not self._open_chat(chat):
                return SendResult(False, "无法打开微信会话", chat)
            try:
                menu = None
                for _attempt in range(3):
                    if not self._right_click_quote_message(chat, quote_text, quote_kind):
                        return SendResult(False, "引用失败：没有定位到被回复的消息")
                    # The popup can disappear while the UIA tree is traversed;
                    # re-open it if necessary and scan immediately.
                    deadline = time.time() + 2.0
                    while time.time() < deadline:
                        menu = self._uia._uia_find_menu_item("引用")
                        if menu is not None:
                            break
                        time.sleep(0.12)
                    if menu is not None:
                        break
                    try:
                        ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                        ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
                    except Exception:
                        pass
                if menu is None:
                    return SendResult(False, "引用失败：没有找到引用菜单")
                if not self._uia._uia_click_menu_item(menu):
                    return SendResult(False, "引用失败：无法点击引用菜单")
                time.sleep(0.6)
                ok = bool(self._uia.send_text(text))
            except Exception as exc:
                log.exception("wechatauto 引用发送失败")
                return SendResult(False, "引用发送失败", str(exc))
        if not ok:
            return SendResult(False, "引用后 UIA 输入/发送校验失败")
        self._remember_sent(chat, text)
        if username and not self._wait_outgoing(username, before, "text", timeout=3.0):
            log.warning("wechatauto 已执行引用发送，但数据库尚未确认：%s", chat)
        return SendResult(True, "已引用发送")

    def send_sticker(self, chat: str, path: Path) -> SendResult:
        raise NotImplementedError

    def fetch_recent(self, chat: str, limit: int) -> list[HistoryMessage]:
        raise NotImplementedError

    def unread_sessions(self, limit: int = 200) -> list[dict]:
        return []

    def probe(self) -> tuple[bool, str]:
        return False, "适配器尚未连接"

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "backend": self.backend_label,
            "self_name": self.self_name,
            "last_message_at": self._last_message_at,
        }


class WeChatAutoAdapter(BaseWeChatAdapter):
    """Free fallback for WeChat 4.1.15 via wechatauto-replica.

    Reading uses the library's decrypted local message database (no screenshots)
    and its watermark-based Listener.  Sending uses the library's UIA driver
    after its Qt accessibility gate hot-activation; OCR and screenshot fallback
    are explicitly disabled.  No Codex or OpenAI SDK is involved.

    This backend is optional and experimental: it reads local WeChat databases
    and temporarily flips a Qt accessibility byte in the running Weixin process.
    """

    name = "wechatauto"

    def __init__(self, config: dict, on_message: Callable[[IncomingMessage], None],
                 on_status: Callable[[str, str], None] | None = None) -> None:
        super().__init__(config, on_message, on_status)
        self._db = None
        self._listener = None
        self._uia = None
        self._media = None
        self._ui_lock = threading.RLock()
        self._targets_by_user: dict[str, dict] = {}
        self._usernames: dict[str, str] = {}
        self.self_wxid = ""

    def _import_deps(self):
        log.info("wechatauto import: root")
        # wechatauto.logger clears the root logger handlers at import time.
        # Preserve ARONA's file handler so received messages, model calls,
        # token usage, and replies remain visible in logs/arona.log.
        root_logger = logging.getLogger()
        saved_handlers = list(root_logger.handlers)
        saved_level = root_logger.level
        try:
            from wechatauto import WxParam, WeChatDB
            log.info("wechatauto import: db")
            from wechatauto.db import Listener
            log.info("wechatauto import: uia")
            from wechatauto.uia_driver import WeChatUIA
        finally:
            root_logger.handlers[:] = saved_handlers
            root_logger.setLevel(saved_level)
        log.info("wechatauto import: done")
        # This integration deliberately never calls OCR.  Sending is UIA-only.
        WxParam.ENABLE_OCR = False

        # The upstream package calls tasklist through a captured subprocess.
        # In a restricted desktop session that pipe can block indefinitely,
        # which left the Start button at "连接中" and prevented any replies.
        # Use a native process-list lookup instead; it is also faster.
        try:
            import psutil

            def _weixin_pids(_self=None) -> list[int]:
                pids = []
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        if str(proc.info.get("name") or "").lower() == "weixin.exe":
                            pids.append(int(proc.info["pid"]))
                    except (psutil.Error, ValueError, KeyError):
                        continue
                return pids

            WeChatDB._find_weixin_pids = _weixin_pids

            @staticmethod
            def _weixin_running() -> bool:
                return bool(_weixin_pids())

            WeChatUIA.is_running = _weixin_running
        except Exception as exc:
            log.debug("安装 psutil 进程探测失败：%s", exc)

        return WxParam, WeChatDB, Listener, WeChatUIA

    def _resolve_username(self, name: str) -> str | None:
        name = (name or "").strip()
        if not name or self._db is None:
            return None
        if name in ("文件传输助手", "File Transfer", "filehelper"):
            return "filehelper"
        if name.startswith("wxid_") or name.endswith("@chatroom"):
            return name
        for getter in (self._db.username_by_nickname,):
            try:
                username = getter(name)
                if username:
                    return username
            except Exception:
                pass
        try:
            for hit in self._db.search_contact(name):
                if name in (hit.get("remark"), hit.get("nick_name")):
                    return str(hit.get("username") or "")
        except Exception:
            pass
        try:
            for session in self._db.get_sessions(limit=300):
                username = str(session.get("username") or "")
                if not username:
                    continue
                try:
                    if self._db.get_nickname(username) == name:
                        return username
                except Exception:
                    continue
        except Exception as exc:
            log.debug("扫描会话名失败：%s", exc)
        return None

    def start(self, targets: list[dict]) -> None:
        log.info("wechatauto 适配器：开始导入依赖")
        WxParam, WeChatDB, Listener, WeChatUIA = self._import_deps()
        log.info("wechatauto 适配器：依赖导入完成")
        self.on_status("warning",
                       "wechatauto 后端：读取本地消息库 + UIA 发送（OCR/截图已禁用）。"
                       "首次启动需解密数据库并激活 Qt 无障碍树。")
        try:
            log.info("wechatauto 适配器：初始化微信数据库")
            self._db = WeChatDB()
            log.info("wechatauto 适配器：数据库初始化完成")
        except Exception as exc:
            raise RuntimeError(
                "wechatauto 无法读取微信数据库。请确保微信已登录、主窗口已打开，"
                "并以普通用户权限运行（不要以管理员身份运行）。\n"
                f"原始错误：{exc}"
            ) from exc
        try:
            info = self._db.get_self_info() or {}
            self.self_wxid = str(self._db.wxid or info.get("username") or "")
            self.self_name = str(info.get("nick_name") or info.get("remark") or "")
        except Exception as exc:
            log.debug("读取 wechatauto 自身信息失败：%s", exc)
        configured_name = str(self.config.get("self_name", "")).strip()
        if configured_name:
            self.self_name = configured_name
        try:
            log.info("wechatauto 适配器：激活 UIA 窗口")
            self._uia = WeChatUIA()
            if not self._uia.ensure_window():
                raise RuntimeError("微信主窗口不可见")
            if not self._uia.ensure_materialized(timeout=15):
                raise RuntimeError(
                    "Qt 控件树激活失败。请让 Python 与微信保持同一权限级别，"
                    "并将 Python 加入安全软件白名单后重试"
                )
        except Exception as exc:
            raise RuntimeError(
                "wechatauto 的 UI Automation 控件树不可用。\n"
                "请确认微信主窗口已打开、桌面未锁屏，且 Python 与微信不是不同权限级别。\n"
                f"原始错误：{exc}"
            ) from exc

        self._targets = {t["name"]: t for t in targets}
        self._targets_by_user = {}
        self._usernames = {}
        self._chat_aliases: dict[str, list[str]] = {}
        failures = []
        for target in targets:
            name = target["name"]
            username = self._resolve_username(name)
            if not username:
                failures.append(name)
                continue
            self._usernames[name] = username
            self._targets_by_user[username] = target
            self._chat_aliases[name] = self._chat_names(name, username)
            log.info("wechatauto 已解析会话：%s -> %s", name, username)
        if failures:
            log.warning("wechatauto 未找到会话：%s", "、".join(failures))

        watermark = str(DATA / "wechatauto_watermark.json")
        try:
            interval = max(1.0, float(self.config.get("poll_interval", 2.0)))
            self._listener = Listener(
                self._db, interval=interval, watermark_file=watermark,
                persist_interval=1.0,
            )
            for name, username in self._usernames.items():
                self._listener.add_listener(username, self._on_db_message)
            self._listener.start()
        except Exception as exc:
            raise RuntimeError(f"wechatauto 消息监听启动失败：{exc}") from exc
        self._running = True
        self.connected = True
        self.backend_label = "DB-UIA · 4.1.15（wechatauto-replica）"
        self.on_status(
            "connected",
            f"wechatauto 已连接 · {len(self._usernames)}/{len(targets)} 个监听对象 · "
            "OCR/截图关闭",
        )

    def _download_incoming_image(self, msg: dict) -> str:
        """Decrypt an image already present in the local WeChat cache."""
        if not self._db:
            return ""
        try:
            from wechatauto.media import MediaDownloader
            from .paths import DATA

            folder = DATA / "images" / "incoming"
            folder.mkdir(parents=True, exist_ok=True)
            if self._media is None:
                self._media = MediaDownloader(self._db, save_dir=str(folder))
            username = str(msg.get("username") or "")
            local_id = int(msg.get("local_id") or 0)
            if not username or not local_id:
                return ""
            path = self._media.download_image(
                username, local_id, save_dir=str(folder), tier="best",
                shard=msg.get("shard"),
            )
            if path:
                # Animated WeChat images can be returned as .wxgf, which
                # PIL/vision models cannot open.  Use the decoded thumbnail
                # (PNG/JPG) instead.  This is a local file, not a screenshot.
                if str(path).lower().endswith(".wxgf"):
                    try:
                        thumb = self._media.download_image(
                            username, local_id, save_dir=str(folder), tier="thumb",
                            shard=msg.get("shard"),
                        )
                        if thumb:
                            path = thumb
                    except Exception as exc:
                        log.debug("wechatauto 动画图缩略图失败：%s", exc)
                log.info("wechatauto 图片已解密：%s", path)
            return str(path or "")
        except Exception as exc:
            log.warning("wechatauto 图片解密失败：%s", exc)
            return ""

    def _message_type_name(self, value: object) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "文本": "text", "text": "text", "图片": "image", "image": "image",
            "表情": "emotion", "emotion": "emotion", "语音": "voice", "voice": "voice",
            "视频": "video", "video": "video", "文件": "file", "file": "file",
            "链接": "link", "link": "link", "引用": "quote", "quote": "quote",
            "系统": "system", "system": "system",
        }
        return mapping.get(text, text or "text")

    def _display_sender(self, username: str, target: dict, outgoing: bool) -> str:
        if outgoing:
            return self.self_name or "老师（本人手动）"
        if username:
            try:
                return str(self._db.get_nickname(username) or username)
            except Exception:
                return username
        return "群友" if target.get("type") == "group" else target.get("name", "")

    def _map_message(self, msg: dict, target: dict, chat_name: str) -> IncomingMessage:
        username = str(msg.get("username") or "")
        sender_username = str(msg.get("sender_username") or "")
        content = str(msg.get("content") or "").strip()
        msg_type = self._message_type_name(msg.get("type"))
        image_path = self._download_incoming_image(msg) if msg_type == "image" else ""
        outgoing = bool(self.self_wxid and sender_username == self.self_wxid)
        if not sender_username and username == "filehelper":
            outgoing = True
        sender = self._display_sender(sender_username, target, outgoing)
        ext = f"wxdb:{username}:{msg.get('shard', '')}:{msg.get('local_id', '')}"
        return IncomingMessage(
            chat=chat_name,
            sender=sender,
            content=content,
            msg_type=msg_type,
            external_id=ext,
            attr="self" if outgoing else "friend",
            chat_type=str(target.get("type", "friend")),
            direction="out" if outgoing else "in",
            origin="self" if outgoing else "friend",
            image_path=image_path,
            raw=msg,
            received_at=float(msg.get("create_time") or time.time()),
        )

    def _on_db_message(self, msg: dict, listener=None) -> None:
        try:
            username = str(msg.get("username") or "")
            target = self._targets_by_user.get(username)
            if not target:
                return
            chat_name = str(target.get("name") or username)
            incoming = self._map_message(msg, target, chat_name)
            if incoming.direction == "out" and self._is_recent_sent(chat_name, incoming.content):
                return
            self._last_message_at = time.time()
            self.on_message(incoming)
        except Exception:
            log.exception("wechatauto 消息回调失败")

    def _last_seq(self, username: str) -> int:
        try:
            rows = self._db.get_messages(username, limit=1)
            return int(rows[0].get("sort_seq", 0)) if rows else 0
        except Exception:
            return 0

    @staticmethod
    def _set_file_clipboard(paths: list[str]) -> bool:
        """Put files on the clipboard as CF_HDROP (no screen capture)."""
        paths = [os.path.abspath(p) for p in paths]
        if not paths:
            return False

        class DROPFILES(ctypes.Structure):
            _fields_ = [
                ("pFiles", ctypes.c_uint),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
                ("fNC", ctypes.c_int),
                ("fWide", ctypes.c_int),
            ]

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        try:
            if not u32.OpenClipboard(None):
                return False
            u32.EmptyClipboard()
            df = DROPFILES()
            df.pFiles = ctypes.sizeof(DROPFILES)
            df.fWide = 1
            raw = (ctypes.string_at(ctypes.byref(df), ctypes.sizeof(DROPFILES))
                   + ("\0".join(paths) + "\0").encode("utf-16-le") + b"\0\0")
            handle = k32.GlobalAlloc(0x0042, len(raw))
            if not handle:
                u32.CloseClipboard()
                return False
            dst = k32.GlobalLock(handle)
            ctypes.memmove(dst, raw, len(raw))
            k32.GlobalUnlock(handle)
            u32.SetClipboardData(15, handle)
            u32.CloseClipboard()
            return True
        except Exception:
            return False

    def _chat_names(self, chat: str, username: str) -> list[str]:
        names = [chat]
        # Long group names with a year range (e.g. "... 2018~2026") are often
        # not returned by WeChat's search box as one token.  Also try the
        # stable part before the range; opening that result still validates
        # the full current title against all aliases.
        base = re.sub(r"\s+\d{4}\s*[~～\-–—]\s*\d{4}.*$", "", chat).strip()
        if base and base != chat:
            names.append(base)
            if len(base) > 24:
                names.append(base[:24].rstrip())
        if self._db and username:
            try:
                remark = str(self._db.get_nickname(username) or "").strip()
                if remark:
                    names.append(remark)
            except Exception:
                pass
            try:
                for hit in self._db.search_contact(chat):
                    if str(hit.get("username") or "") != username:
                        continue
                    for key in ("remark", "nick_name"):
                        value = str(hit.get(key) or "").strip()
                        if value:
                            names.append(value)
            except Exception:
                pass
        return list(dict.fromkeys(names))

    def ensure_chat_open(self, chat: str) -> bool:
        with self._ui_lock:
            return self._open_chat(chat)

    @staticmethod
    def _find_chat_tab(uia):
        win = getattr(uia, "_win", None)
        if win is None:
            return None
        found = [None]

        def walk(ctrl, depth=0):
            if found[0] is not None or depth > 20:
                return
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for child in children:
                try:
                    if (child.ClassName or "") == "mmui::XTabBarItem" and \
                            (child.Name or "").strip() == "微信":
                        found[0] = child
                        return
                except Exception:
                    pass
                walk(child, depth + 1)

        walk(win)
        return found[0]

    def _prepare_chat_window(self, uia) -> None:
        """Restore a wide chat page before searching for a contact.

        WeChat keeps a previously opened conversation in a narrow window even
        after clicking the chat tab; the search box then has no UIA node.  A
        normal/maximized window materialises the chat page again.
        """
        try:
            win = getattr(uia, "_win", None)
            hwnd = int(getattr(win, "NativeWindowHandle", 0) or 0) if win else 0
            if hwnd:
                rect = win.BoundingRectangle
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width < 900 or height < 600:
                    ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                    time.sleep(0.8)
            if not uia.ensure_window():
                return
            uia.ensure_materialized(timeout=4)
            uia.back_to_chat_tab(settle=0.8, max_clicks=3)
            # back_to_chat_tab's readiness probe can report False while the
            # tree is actually usable.  Click the visible WeChat tab
            # explicitly as a fallback, then re-check the chat input.
            try:
                if uia.current_chat() is None:
                    tab = self._find_chat_tab(uia)
                    if tab is not None:
                        uia._click_ctrl(tab)
                        time.sleep(0.8)
                    if uia.current_chat() is None:
                        tab = self._find_chat_tab(uia)
                        if tab is not None:
                            uia._click_ctrl(tab)
                            time.sleep(0.8)
            except Exception:
                pass
        except Exception as exc:
            log.debug("整理微信聊天窗口失败：%s", exc)

    def _open_chat(self, chat: str) -> bool:
        uia = self._uia
        if not uia:
            return False
        aliases = self._chat_aliases.get(chat) or [chat]
        self._prepare_chat_window(uia)
        try:
            for attempt in range(3):
                if not uia.ensure_window():
                    time.sleep(0.5)
                    continue
                current = uia.current_chat()
                if current and current in aliases:
                    return True
                # Try the configured name first, then the local remark/nickname.
                for alias in aliases:
                    try:
                        ok = bool(uia.open_chat(alias, retries=3))
                    except Exception as exc:
                        log.warning("wechatauto 搜索会话 %s 失败：%s", alias, exc)
                        ok = False
                    current = uia.current_chat()
                    if ok or (current and current in aliases):
                        log.info("wechatauto 已打开会话：%s -> %r", chat, current or alias)
                        return True
                # A narrow window can keep the previous conversation open;
                # materialize and retry once before reporting an error.
                try:
                    uia.ensure_materialized(timeout=5)
                    self._prepare_chat_window(uia)
                except Exception:
                    pass
                time.sleep(0.4)
            try:
                current = uia.current_chat()
            except Exception:
                current = None
            log.warning("wechatauto 打开会话失败：目标=%s 当前=%r 候选=%s",
                        chat, current, aliases)
            return False
        except Exception as exc:
            log.warning("wechatauto 打开会话失败：%s", exc)
            return False

    def _wait_outgoing(self, username: str, before_seq: int, expected_type: str,
                       timeout: float = 6.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                rows = self._db.get_messages(username, limit=5)
                for row in rows:
                    if int(row.get("sort_seq", 0)) > before_seq:
                        sender = str(row.get("sender_username") or "")
                        if sender == self.self_wxid:
                            return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def send_text(self, chat: str, text: str) -> SendResult:
        if not text.strip():
            return SendResult(False, "消息内容为空")
        username = self._usernames.get(chat, "")
        before = self._last_seq(username) if username else 0
        with self._ui_lock:
            if not self._open_chat(chat):
                return SendResult(False, "无法打开微信会话", chat)
            try:
                ok = bool(self._uia.send_text(text))
            except Exception as exc:
                log.exception("wechatauto 发送失败")
                return SendResult(False, "发送失败", str(exc))
        if not ok:
            return SendResult(False, "UIA 输入/发送校验失败")
        self._remember_sent(chat, text)
        if username and not self._wait_outgoing(username, before, "text", timeout=3.0):
            log.warning("wechatauto 已执行发送，但数据库尚未确认：%s", chat)
        return SendResult(True, "已发送")

    def send_sticker(self, chat: str, path: Path) -> SendResult:
        if not Path(path).is_file():
            return SendResult(False, "表情包文件不存在", str(path))
        username = self._usernames.get(chat, "")
        before = self._last_seq(username) if username else 0
        with self._ui_lock:
            if not self._open_chat(chat):
                return SendResult(False, "无法打开微信会话", chat)
            try:
                ctrl = self._uia._chat_input()
                if ctrl is None or not self._uia._click_ctrl(ctrl):
                    return SendResult(False, "找不到聊天输入框")
                if not self._set_file_clipboard([str(path)]):
                    return SendResult(False, "无法写入图片剪贴板")
                ctrl.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
                time.sleep(0.15)
                ctrl.SendKeys("{Ctrl}v", waitTime=0.05)
                time.sleep(2.0)
                ctrl.SendKeys("{Enter}", waitTime=0.05)
                time.sleep(1.2)
            except Exception as exc:
                log.exception("wechatauto 发送表情包失败")
                return SendResult(False, "表情包发送失败", str(exc))
        self._remember_sent(chat, "__sticker__")
        if username and not self._wait_outgoing(username, before, "image", timeout=6.0):
            return SendResult(False, "数据库未确认图片已发出")
        return SendResult(True, "表情包已发送")

    def fetch_recent(self, chat: str, limit: int) -> list[HistoryMessage]:
        target = self._targets.get(chat, {})
        username = self._usernames.get(chat, "")
        if not username:
            return []
        rows = self._db.get_messages(username, limit=max(1, limit))
        result: list[HistoryMessage] = []
        for row in reversed(rows):
            outgoing = str(row.get("sender_username") or "") == self.self_wxid
            sender = self._display_sender(str(row.get("sender_username") or ""),
                                          target, outgoing)
            result.append(HistoryMessage(
                chat=chat,
                sender=sender,
                content=str(row.get("content") or ""),
                role="assistant" if outgoing else "user",
                msg_type=self._message_type_name(row.get("type")),
                external_id=f"wxdb:{username}:{row.get('shard', '')}:{row.get('local_id', '')}",
                attr="self" if outgoing else "friend",
                direction="out" if outgoing else "in",
                origin="self" if outgoing else "friend",
            ))
        return result

    def target_usernames(self) -> set[str]:
        return set(self._usernames.values())

    def unread_sessions(self, limit: int = 200) -> list[dict]:
        if not self._db:
            return []
        result = []
        try:
            for session in self._db.get_sessions(limit=limit):
                try:
                    unread = int(session.get("unread") or 0)
                except (TypeError, ValueError):
                    unread = 0
                if unread <= 0:
                    continue
                username = str(session.get("username") or "")
                try:
                    name = str(self._db.get_nickname(username) or username)
                except Exception:
                    name = username
                result.append({"username": username, "name": name,
                               "unread": unread,
                               "last_time": session.get("last_time", 0)})
        except Exception as exc:
            log.debug("读取未读会话失败：%s", exc)
        result.sort(key=lambda item: int(item.get("unread", 0)), reverse=True)
        return result

    def probe(self) -> tuple[bool, str]:
        try:
            if not self._uia or not self._uia.is_running():
                return False, "微信进程未运行"
            return True, "wechatauto 数据库/UIA 通道在线"
        except Exception as exc:
            return False, f"wechatauto 状态检查失败：{exc}"

    def stop(self) -> None:
        self._running = False
        with self._ui_lock:
            self._uia = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception as exc:
                log.debug("停止 wechatauto listener 失败：%s", exc)
            self._listener = None
        self._db = None
        self._media = None
        self.connected = False


def build_adapter(backend: str, config: dict,
                  on_message: Callable[[IncomingMessage], None],
                  on_status: Callable[[str, str], None] | None = None) -> BaseWeChatAdapter:
    """Build the non-wxauto transport (wechatauto-replica only)."""
    if not detect_wechat_backends().get("wechatauto"):
        raise RuntimeError(
            "未安装 4.1.15 兼容后端。请运行「安装 4.1.15 后端.bat」，"
            "或参考 README 的 wechatauto 安装说明"
        )
    if backend not in ("auto", "", None, "wechatauto"):
        raise RuntimeError(
            f"不再支持 wxauto 适配器：{backend}。请选择自动或 wechatauto"
        )
    return WeChatAutoAdapter(config, on_message, on_status)


def adapter_capabilities() -> dict:
    backends = detect_wechat_backends()
    return {**backends, "preferred": "wechatauto" if backends.get("wechatauto") else ""}

