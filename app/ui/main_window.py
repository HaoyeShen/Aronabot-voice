"""Main application window."""

from __future__ import annotations

import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Any

from .. import __version__
from ..paths import ASSETS
from ..service import BotService
from .dialogs import DiagnosticsDialog, ReplyDetailDialog
from .pages.dashboard import DashboardPage
from .pages.debug import DebugPage
from .pages.listen_reply import ListenReplyPage
from .pages.logs import LogsPage
from .pages.models import ModelsPage
from .pages.monitor import MonitorPage
from .pages.personas import PersonasPage
from .pages.settings import SettingsPage
from .pages.stickers import StickersPage
from .pages.targets import TargetsPage
from .pages.training import TrainingPage
from .shell import NAV_ITEMS, Sidebar, TopBar
from .theme import C, init_scale, px
from .widgets import ConfirmDialog, ImageBank, ToastManager, confirm, info, setup_ttk

PAGE_CLASSES = {
    "dashboard": DashboardPage,
    "debug": DebugPage,
    "monitor": MonitorPage,
    "stickers": StickersPage,
    "targets": TargetsPage,
    "listen_reply": ListenReplyPage,
    "personas": PersonasPage,
    "training": TrainingPage,
    "models": ModelsPage,
    "logs": LogsPage,
    "settings": SettingsPage,
}

PAGE_CODES = {key: code for key, _label, _icon, code in NAV_ITEMS}
log = logging.getLogger("arona.ui")


class MainWindow:
    def __init__(self, root: tk.Tk, service: BotService, icon=None) -> None:
        self.root = root
        self.service = service
        self.config = service.config
        self.icon = icon
        self.diagnostics_busy = False
        self.last_diagnostics: dict | None = None
        self._closing = False
        self._bot_action_busy = False
        self._stop_watchdog_id = None
        self._start_watchdog_id = None
        self._current_page = ""
        self.pages: dict[str, Any] = {}
        init_scale(root)
        setup_ttk(root)
        self.bank = ImageBank(root)
        self._configure_root()
        self._build()
        self.toast = ToastManager(self.main)
        self._poll_events()
        self._startup()
        log.info("界面就绪 · 机器人待机 · 监听对象 %d 个 · 后端 %s",
                 len(self.config.enabled_targets()), self.config.wechat.get("backend", "auto"))
        log.info("点击右上角「启动机器人」后才会开始监听微信。")
        if (self.config.bot.get("auto_start", True)
                and self.config.enabled_targets()
                and not getattr(self.root, "_arona_no_autostart", False)):
            log.info("已启用启动时自动连接，2 秒后自动启动机器人。")
            self.root.after(2000, self._auto_start)

    # ------------------------------------------------------------------ build
    def _apply_native_icon(self) -> None:
        """Set both the small and big Win32 icons explicitly.

        Tk's iconphoto/iconbitmap often leaves the small icon (the one used by
        the title bar) unset on Windows, which renders as a blank page.  The
        taskbar also prefers the class small icon, so set both class slots.
        """
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            from ctypes import wintypes

            ico = ASSETS / "arona_icon.ico"
            if not ico.exists():
                return
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            large = wintypes.HICON()
            small = wintypes.HICON()
            shell32.ExtractIconExW.argtypes = [
                wintypes.LPCWSTR, ctypes.c_int,
                ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON),
                ctypes.c_uint,
            ]
            shell32.ExtractIconExW.restype = ctypes.c_uint
            count = shell32.ExtractIconExW(str(ico), 0,
                                           ctypes.byref(large),
                                           ctypes.byref(small), 1)
            if not count:
                return
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                hwnd = self.root.winfo_id()
            WM_SETICON = 0x0080
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, 0, small)
                user32.SetClassLongPtrW(hwnd, -34, small)  # GCLP_HICONSM
            if large:
                user32.SendMessageW(hwnd, WM_SETICON, 1, large)
                user32.SetClassLongPtrW(hwnd, -14, large)  # GCLP_HICON
        except Exception as exc:
            log.debug("设置 Win32 图标失败：%s", exc)

    def _configure_root(self) -> None:
        root = self.root
        root.title("ARONA · 什亭之匣微信助手")
        root.configure(bg=C["bg"])
        # iconbitmap first, then iconphoto: on Windows Tk's iconbitmap sets
        # only the large/class icon, while the later iconphoto also fills the
        # small title-bar icon.  The reverse order leaves a blank small icon.
        try:
            ico = ASSETS / "arona_icon.ico"
            if ico.exists():
                root.iconbitmap(default=str(ico))
        except Exception:
            pass
        if self.icon:
            try:
                root.iconphoto(True, self.icon)
            except Exception:
                pass
        root.after(250, self._apply_native_icon)
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        width = min(px(1390), max(1080, screen_w - px(80)))
        height = min(px(890), max(700, screen_h - px(100)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - px(8))
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(min(px(1120), width), min(px(700), height))
        root.protocol("WM_DELETE_WINDOW", self.close)
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass

    def _build(self) -> None:
        shell = tk.Frame(self.root, bg=C["bg"])
        shell.pack(fill="both", expand=True)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(shell, self, self.show_page)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.main = tk.Frame(shell, bg=C["bg"])
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(1, weight=1)
        self.main.grid_columnconfigure(0, weight=1)
        self.topbar = TopBar(self.main, self)
        self.topbar.grid(row=0, column=0, sticky="ew", padx=px(24))
        self.host = tk.Frame(self.main, bg=C["bg"])
        self.host.grid(row=1, column=0, sticky="nsew", padx=px(24), pady=(0, px(18)))
        self.host.grid_rowconfigure(0, weight=1)
        self.host.grid_columnconfigure(0, weight=1)
        self.sidebar.update_state(self.service.state())
        self.topbar.update_state(self.service.state())
        self.show_page(self.config.ui.get("start_page", "dashboard"))

    def _startup(self) -> None:
        self.refresh_header()
        try:
            self.root.deiconify()
            self.root.attributes("-alpha", 0.0)
            steps = 8

            def fade(step: int = 0) -> None:
                if self._closing:
                    return
                try:
                    self.root.attributes("-alpha", min(1.0, step / steps))
                except Exception:
                    return
                if step < steps:
                    self.root.after(18, lambda: fade(step + 1))

            fade()
        except Exception:
            pass
        if not self.config.targets():
            self.root.after(700, lambda: self.toast.show(
                "阿洛娜已就位。先在「测试对象」添加白名单，再启动机器人。", "info", 6500))
        else:
            self.root.after(700, lambda: self.toast.show("阿洛娜已就位，随时可以开始。", "success"))

    # ------------------------------------------------------------------ pages
    def show_page(self, key: str) -> None:
        if key not in PAGE_CLASSES:
            key = "dashboard"
        if self._current_page and self._current_page in self.pages:
            self.pages[self._current_page].pack_forget()
        page = self.pages.get(key)
        if page is None:
            page = PAGE_CLASSES[key](self.host, self)
            self.pages[key] = page
        page.pack(fill="both", expand=True)
        page.on_show()
        self._current_page = key
        self.sidebar.set_active(key)
        self.topbar.set_page(page.title, page.subtitle, PAGE_CODES.get(key, key.upper()))
        self.refresh_header()

    def refresh_header(self) -> None:
        try:
            state = self.service.state()
            self.topbar.update_state(state)
            self.sidebar.update_state(state)
            if not self._bot_action_busy and not state.get("starting"):
                self.topbar.action.set_busy(False)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ bot
    def toggle_bot(self) -> None:
        if self._bot_action_busy:
            return
        state = self.service.state()
        if state.get("running"):
            if not confirm(self.root, "停止机器人",
                           "停止后不再监听和回复微信消息，已保存的上下文不会删除。\n确定停止吗？",
                           ok_text="停止", danger=True, icon=self.icon):
                return
            self._bot_action_busy = True
            self.topbar.action.set_busy(True, "停止中")
            log.info("用户请求停止机器人")
            self.toast.show("正在停止机器人…", "info")
            try:
                self._stop_watchdog_id = self.root.after(20000, self._stop_watchdog)
            except Exception:
                self._stop_watchdog_id = None

            def worker():
                try:
                    self.service.stop()
                finally:
                    self.root.after(0, self._bot_stopped)

            threading.Thread(target=worker, daemon=True).start()
            return
        if self.service.state().get("starting"):
            return
        if not self.config.enabled_targets():
            if not confirm(self.root, "尚未设置测试对象",
                           "当前白名单为空。启动后阿洛娜不会监听或回复任何人。\n"
                           "可以先添加对象，或仍然启动以检查微信连接。",
                           ok_text="仍然启动", icon=self.icon):
                self.show_page("targets")
                return
        self._bot_action_busy = True
        self.topbar.action.set_busy(True, "连接中")
        log.info("用户请求启动机器人 · 后端=%s · 监听对象=%d 个",
                 self.config.wechat.get("backend", "auto"),
                 len(self.config.enabled_targets()))
        self.toast.show("正在连接微信 · 使用本地数据库 + UIA，不截屏…", "info")
        self.service._update(starting=True)
        try:
            self._start_watchdog_id = self.root.after(60000, self._start_watchdog)
        except Exception:
            self._start_watchdog_id = None

        def worker():
            try:
                ok = self.service.start()
            except Exception as exc:
                log.exception("启动线程异常：%s", exc)
                ok = False
            self.root.after(0, lambda: self._bot_started(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _start_watchdog(self) -> None:
        self._start_watchdog_id = None
        if not self._bot_action_busy:
            return
        log.warning("启动机器人超过 60 秒，界面保持可操作；请查看日志中的启动流程阶段")
        self._bot_action_busy = False
        try:
            self.topbar.action.set_busy(False)
        except Exception:
            pass
        self.refresh_header()
        self.toast.show("启动仍在后台进行或已卡住，请查看日志；可再次点击启动。", "warn")

    def _auto_start(self) -> None:
        if self._closing or self._bot_action_busy:
            return
        if self.service.state().get("running"):
            return
        self.toggle_bot()

    def _bot_started(self, ok: bool) -> None:
        self._bot_action_busy = False
        if self._start_watchdog_id:
            try:
                self.root.after_cancel(self._start_watchdog_id)
            except Exception:
                pass
            self._start_watchdog_id = None
        if self._stop_watchdog_id:
            try:
                self.root.after_cancel(self._stop_watchdog_id)
            except Exception:
                pass
            self._stop_watchdog_id = None
        try:
            self.topbar.action.set_busy(False)
        except Exception:
            pass
        if self._closing:
            return
        self.refresh_header()
        if ok:
            count = len(self.config.enabled_targets())
            self.toast.show(f"机器人已启动，正在监听 {count} 个对象。", "success")
        else:
            error = self.service.state().get("last_error") or "请查看运行日志"
            self.topbar.action.set_busy(False, "启动机器人")
            self.toast.show("启动失败：" + str(error)[:80], "error")
            backend = self.config.wechat.get("backend", "auto")
            if "wechatauto" in str(error) or "数据库" in str(error) or "控件树" in str(error):
                detail = (
                    "wechatauto 4.1.15 兼容后端没有连上微信。\n\n"
                    f"{error}\n\n"
                    "请依次确认：\n"
                    "1. 微信已登录，且主窗口是打开的（可按 Ctrl+Alt+W 唤出）；\n"
                    "2. 桌面未锁屏；\n"
                    "3. 不要用管理员权限运行 ARONA，让 Python 与微信权限级别一致；\n"
                    "4. 把 Python/本项目加入安全软件白名单；\n"
                    "5. 首次连接需要 10–30 秒，请稍等后重试。\n\n"
                    "可在「自动调试」查看完整报告；如果报告显示 "
                    "「Python 运行完整性：Low」，请关闭 ARONA，"
                    "从桌面重新双击「启动 ARONA.bat」后再试。\n\n"
                    "如果仍然失败，请把日志发给我，以便继续调整。"
                )
            else:
                detail = (
                    "阿洛娜没能连接微信。\n\n"
                    f"{error}\n\n"
                    "请确认：\n"
                    "1. Windows 微信已启动并登录，主窗口处于打开状态；\n"
                    "2. 已安装微信自动化组件（4.1.15 请安装 wechatauto 兼容后端）；\n"
                    "3. 微信窗口没有被最小化到无法访问的状态；\n"
                    "4. 在「设置」中选择正确的适配器。\n\n"
                    "当前自动调试会显示微信版本与可用后端。"
                )
            info(self.root, "启动失败", detail, icon=self.icon)

    def _bot_stopped(self) -> None:
        self._bot_action_busy = False
        if self._stop_watchdog_id:
            try:
                self.root.after_cancel(self._stop_watchdog_id)
            except Exception:
                pass
            self._stop_watchdog_id = None
        try:
            self.topbar.action.set_busy(False)
        except Exception:
            pass
        self.refresh_header()
        self.toast.show("机器人已停止。", "info")

    def _stop_watchdog(self) -> None:
        self._stop_watchdog_id = None
        if not self._bot_action_busy:
            return
        log.warning("停止机器人超过 20 秒，释放界面按钮；后台停止可能仍在收尾")
        self._bot_action_busy = False
        try:
            self.topbar.action.set_busy(False)
        except Exception:
            pass
        self.refresh_header()
        self.toast.show("停止操作超时，界面已恢复；请稍后确认机器人状态。", "warn")

    def restart_bot(self) -> None:
        if self._bot_action_busy:
            return
        self._bot_action_busy = True
        self.topbar.action.set_busy(True, "重启中")

        def worker():
            try:
                if self.service.running:
                    self.service.stop()
                self.service._update(starting=True)
                ok = self.service.start()
            except Exception:
                ok = False
            self.root.after(0, lambda: self._bot_started(ok))

        threading.Thread(target=worker, daemon=True).start()

    def reload_listening(self) -> None:
        self.refresh_header()
        if not self.service.running:
            return
        self.toast.show("正在热更新测试对象…", "info")

        def worker():
            ok = self.service.restart_listening()
            self.root.after(0, lambda: self.toast.show(
                "测试对象已热更新。" if ok else "测试更新失败，请查看日志。",
                "success" if ok else "error"))

        threading.Thread(target=worker, daemon=True).start()

    def run_health_check(self) -> None:
        self.toast.show("正在检查模型与微信连接…", "info")

        def worker():
            try:
                result = self.service.test_model()
                self.root.after(0, lambda: self.toast.show(
                    ("模型连接正常：" if result["ok"] else "模型连接失败：") + result["message"],
                    "success" if result["ok"] else "error"))
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: self.toast.show("健康检查失败：" + message, "error"))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ events
    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(80):
                try:
                    kind, data = self.service.events.get_nowait()
                except Exception:
                    break
                self._dispatch(kind, data)
        finally:
            try:
                self.root.after(120, self._poll_events)
            except tk.TclError:
                pass

    def _dispatch(self, kind: str, data: dict) -> None:
        if kind in ("state", "started", "stopped", "start_failed", "model_health",
                    "adapter_status", "targets_reloaded"):
            self.refresh_header()
        if kind == "toast":
            self.toast.show(data.get("message", ""), data.get("level", "info"))
        elif kind == "error":
            message = data.get("message", "发生错误")
            self.toast.show(str(message)[:120], "error")
            self.refresh_header()
        elif kind == "started":
            self.refresh_header()
        elif kind == "start_failed":
            self.refresh_header()
        elif kind == "model_health":
            if data.get("ok"):
                self.toast.show("模型连接正常。", "success")
            else:
                self.toast.show("模型连接失败：" + str(data.get("message", ""))[:90], "error")
        elif kind == "adapter_status" and data.get("kind") == "warning":
            self.toast.show(str(data.get("message", "")), "warn")
        elif kind == "unread":
            outside = data.get("outside") or []
            if outside:
                summary = "、".join(f"{item.get('name')}({item.get('unread')})"
                                    for item in outside[:3])
                self.toast.show("未读消息中有未监听会话：" + summary,
                                "warn", 8000)
        page = self.pages.get(self._current_page)
        if page is not None:
            try:
                page.on_event(kind, data)
            except Exception:
                pass

    # ------------------------------------------------------------------ dialogs
    def show_diagnostics(self, result: dict) -> None:
        if not result:
            return
        DiagnosticsDialog(self.root, result, icon=self.icon, bank=self.bank).show()

    def show_reply_detail(self, reply: dict) -> None:
        ReplyDetailDialog(self.root, reply, bank=self.bank, icon=self.icon).show()

    # ------------------------------------------------------------------ close
    def close(self) -> None:
        if self._closing:
            return
        if self.config.ui.get("confirm_close", True) and self.service.running:
            if not confirm(self.root, "退出 ARONA",
                           "机器人正在运行。退出会停止监听，但不会删除上下文和设置。\n确定退出吗？",
                           ok_text="退出", danger=True, icon=self.icon):
                return
        self._closing = True
        try:
            if self.service.running:
                self.service.stop(join_timeout=2.0)
        except Exception:
            pass
        try:
            self.service.memory.close()
        except Exception:
            pass
        try:
            self.service.training.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
