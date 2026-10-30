"""Sidebar, header, and window chrome."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import __version__
from ..paths import ASSETS
from ..ui.theme import C, display_font, font, mix, px, truncate
from ..ui.widgets import ImageBank, Pill, RoundedButton, _widget_bg


NAV_ITEMS = [
    ("dashboard", "控制台", "⌂", "HOME"),
    ("debug", "自动调试", "✦", "DEBUG"),
    ("monitor", "运行监控", "◉", "LIVE"),
    ("stickers", "表情包", "☺", "STICKERS"),
    ("targets", "测试对象", "◎", "TARGETS"),
    ("listen_reply", "监听与回复", "↺", "REPLY"),
    ("personas", "人格设定", "♡", "PERSONA"),
    ("training", "本地训练", "✎", "TRAIN"),
    ("models", "模型与 API", "◇", "MODELS"),
    ("logs", "运行日志", "≡", "LOGS"),
    ("settings", "设置", "⚙", "SETTINGS"),
]


class NavItem(tk.Frame):
    def __init__(self, parent, key: str, label: str, icon: str, code: str,
                 command: Callable[[str], None]) -> None:
        super().__init__(parent, bg=C["sidebar"], height=px(43), cursor="hand2")
        self.pack_propagate(False)
        self.key = key
        self.command = command
        self.active = False
        self._bg = C["sidebar"]
        self.bar = tk.Frame(self, bg=C["sidebar"], width=px(3))
        self.bar.pack(side="left", fill="y")
        self.icon_label = tk.Label(self, text=icon, bg=C["sidebar"], fg=C["nav_text_dim"],
                                   font=("Segoe UI Symbol", px(15)), width=2)
        self.icon_label.pack(side="left", padx=(px(10), px(1)))
        self.text_frame = tk.Frame(self, bg=C["sidebar"])
        self.text_frame.pack(side="left", fill="both", expand=True, pady=px(5))
        self.label = tk.Label(self.text_frame, text=label, bg=C["sidebar"], fg=C["nav_text"],
                              font=font(9, "bold"), anchor="w")
        self.label.pack(fill="x")
        self.code = tk.Label(self.text_frame, text=code, bg=C["sidebar"], fg=C["nav_text_dim"],
                             font=display_font(6, "normal"), anchor="w")
        self.code.pack(fill="x")
        self.dot = tk.Canvas(self, width=px(7), height=px(7), bg=C["sidebar"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="right", padx=px(12))
        self._draw_dot(False)
        for widget in (self, self.icon_label, self.text_frame, self.label, self.code):
            widget.bind("<Button-1>", lambda _e: self.command(self.key))
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _draw_dot(self, active: bool) -> None:
        self.dot.delete("all")
        if active:
            self.dot.create_oval(px(1), px(1), px(6), px(6), fill=C["pink"], outline="")

    def _paint(self, bg: str, fg: str, icon_fg: str, code_fg: str) -> None:
        self.configure(bg=bg)
        for widget in (self.bar, self.icon_label, self.text_frame, self.label, self.code, self.dot):
            widget.configure(bg=bg)
        self.label.configure(fg=fg)
        self.icon_label.configure(fg=icon_fg)
        self.code.configure(fg=code_fg)

    def _enter(self, _=None) -> None:
        if not self.active:
            self._paint(C["sidebar_hover"], "white", C["cyan"], C["nav_text_dim"])

    def _leave(self, _=None) -> None:
        if not self.active:
            self._paint(C["sidebar"], C["nav_text"], C["nav_text_dim"], C["nav_text_dim"])

    def set_active(self, active: bool) -> None:
        self.active = active
        if active:
            self._paint(C["sidebar_active"], "white", C["cyan"], C["cyan"])
            self.bar.configure(bg=C["pink"])
            self._draw_dot(True)
        else:
            self._paint(C["sidebar"], C["nav_text"], C["nav_text_dim"], C["nav_text_dim"])
            self.bar.configure(bg=C["sidebar"])
            self._draw_dot(False)


class Sidebar(tk.Frame):
    def __init__(self, parent, app, on_navigate: Callable[[str], None]) -> None:
        super().__init__(parent, bg=C["sidebar"], width=px(236))
        self.pack_propagate(False)
        self.app = app
        self.nav: dict[str, NavItem] = {}
        self._build(on_navigate)

    def _build(self, on_navigate) -> None:
        top = tk.Frame(self, bg=C["sidebar"], height=px(170))
        top.pack(fill="x")
        top.pack_propagate(False)
        brand = tk.Frame(top, bg=C["sidebar"])
        brand.pack(fill="x", padx=px(18), pady=(px(20), 0))
        avatar = self.app.bank.photo(ASSETS / "avatar_128.png", (px(58), px(58)), fit="contain")
        if avatar:
            label = tk.Label(brand, image=avatar, bg=C["sidebar"], bd=0)
            label.image = avatar
            label.pack(side="left")
        text = tk.Frame(brand, bg=C["sidebar"])
        text.pack(side="left", padx=(px(10), 0), fill="y")
        tk.Label(text, text="ARONA", bg=C["sidebar"], fg="white",
                 font=display_font(21, "bold")).pack(anchor="w", pady=(px(4), 0))
        tk.Label(text, text="S.C.H.A.L.E.", bg=C["sidebar"], fg=C["cyan"],
                 font=display_font(7, "normal")).pack(anchor="w")
        tk.Label(text, text="AUTO ASSISTANT / 01", bg=C["sidebar"], fg=C["nav_text_dim"],
                 font=display_font(5, "normal")).pack(anchor="w", pady=(px(2), 0))
        line = tk.Frame(top, bg="#1A3E60", height=1)
        line.pack(fill="x", padx=px(18), pady=(px(16), px(9)))
        tk.Label(top, text="S C H A L E   C O N T R O L", bg=C["sidebar"],
                 fg=C["nav_text_dim"], font=display_font(6, "normal")).pack(anchor="w",
                                                                           padx=px(20))
        nav = tk.Frame(self, bg=C["sidebar"])
        nav.pack(fill="x", padx=px(10), pady=(px(7), 0))
        for key, label, icon, code in NAV_ITEMS:
            item = NavItem(nav, key, label, icon, code, on_navigate)
            item.pack(fill="x", pady=px(1))
            self.nav[key] = item
        spacer = tk.Frame(self, bg=C["sidebar"])
        spacer.pack(fill="both", expand=True)
        self._build_bottom()

    def _build_bottom(self) -> None:
        bottom = tk.Frame(self, bg=C["sidebar_deep"])
        bottom.pack(fill="x", side="bottom")
        tk.Frame(bottom, bg=C["cyan"], height=px(2)).pack(fill="x")
        panel = tk.Frame(bottom, bg=C["sidebar_deep"])
        panel.pack(fill="x", padx=px(16), pady=(px(13), px(13)))
        row = tk.Frame(panel, bg=C["sidebar_deep"])
        row.pack(fill="x")
        self.dot = tk.Canvas(row, width=px(10), height=px(10), bg=C["sidebar_deep"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", pady=(px(1), 0))
        self._dot(False)
        self.status_label = tk.Label(row, text="STANDBY", bg=C["sidebar_deep"],
                                     fg=C["nav_text"], font=display_font(8, "bold"))
        self.status_label.pack(side="left", padx=(px(7), 0))
        self.detail_label = tk.Label(panel, text="机器人尚未启动", bg=C["sidebar_deep"],
                                     fg=C["nav_text_dim"], font=font(8), anchor="w")
        self.detail_label.pack(fill="x", pady=(px(4), 0))
        self.model_label = tk.Label(panel, text="MODEL —", bg=C["sidebar_deep"],
                                    fg=C["nav_text_dim"], font=display_font(6, "normal"),
                                    anchor="w")
        self.model_label.pack(fill="x", pady=(px(7), 0))
        meta = tk.Frame(bottom, bg="#06182B")
        meta.pack(fill="x")
        tk.Label(meta, text=f"v{__version__}", bg="#06182B", fg="#50758F",
                 font=display_font(6, "normal")).pack(side="left", padx=px(16), pady=px(6))
        tk.Label(meta, text="UIA · LOCAL", bg="#06182B", fg="#50758F",
                 font=display_font(6, "normal")).pack(side="right", padx=px(16), pady=px(6))

    def _dot(self, online: bool) -> None:
        self.dot.delete("all")
        color = C["green"] if online else "#56748A"
        self.dot.create_oval(px(2), px(2), px(8), px(8), fill=color, outline="")
        if online:
            self.dot.create_oval(px(0), px(0), px(10), px(10), outline=mix(C["green"], C["sidebar_deep"], 0.58))

    def set_active(self, key: str) -> None:
        for nav_key, item in self.nav.items():
            item.set_active(nav_key == key)

    def update_state(self, state: dict) -> None:
        running = bool(state.get("running"))
        online = bool(state.get("wechat_connected"))
        if running and online:
            self.status_label.configure(text="ONLINE", fg=C["cyan"])
            self.detail_label.configure(text=f"微信已连接 · {state.get('targets', 0)} 个监听")
        elif running:
            self.status_label.configure(text="STARTING", fg=C["yellow"])
            self.detail_label.configure(text="正在连接微信…")
        else:
            self.status_label.configure(text="STANDBY", fg=C["nav_text"])
            self.detail_label.configure(text="机器人尚未启动")
        self._dot(running and online)
        model = state.get("model") or "未配置模型"
        self.model_label.configure(text="MODEL · " + truncate(model, 22).upper())


class TopBar(tk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, bg=C["bg"], height=px(84))
        self.pack_propagate(False)
        self.app = app
        left = tk.Frame(self, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(px(2), 0), pady=(px(12), 0))
        self.breadcrumb = tk.Label(left, text="SCHALE / DASHBOARD", bg=C["bg"],
                                   fg=C["cyan_deep"], font=display_font(7, "bold"))
        self.breadcrumb.pack(anchor="w")
        self.title = tk.Label(left, text="控制台", bg=C["bg"], fg=C["text"],
                              font=font(21, "bold"), anchor="w")
        self.title.pack(anchor="w", pady=(px(1), 0))
        self.subtitle = tk.Label(left, text="", bg=C["bg"], fg=C["muted"],
                                 font=font(8), anchor="w")
        self.subtitle.pack(anchor="w")
        right = tk.Frame(self, bg=C["bg"])
        right.pack(side="right", pady=(px(18), 0), padx=(0, px(2)))
        self.action = RoundedButton(right, text="启动机器人", command=app.toggle_bot,
                                    style="primary", width=108, height=32, bg=C["bg"],
                                    font_size=9)
        self.action.pack(side="right", padx=(px(8), px(3)))
        self.clock = tk.Label(right, text="", bg=C["bg"], fg=C["text_soft"],
                              font=display_font(10, "normal"))
        self.clock.pack(side="right", padx=(px(10), px(7)))
        self.date_label = tk.Label(right, text="", bg=C["bg"], fg=C["muted"],
                                   font=font(7))
        self.date_label.pack(side="right")
        self.model_pill = Pill(right, text="MODEL …", fg=C["blue"], bg="#E5F1FF",
                               height=26, font_size=8, width=200)
        self.model_pill.pack(side="right", padx=px(8))
        self.status_pill = Pill(right, text="待机", fg=C["muted"], bg="white",
                                height=26, font_size=8, dot=True, width=110)
        self.status_pill.pack(side="right", padx=px(3))
        self._tick()

    def set_page(self, title: str, subtitle: str, code: str) -> None:
        self.title.configure(text=title)
        self.subtitle.configure(text=subtitle)
        self.breadcrumb.configure(text=f"SCHALE / {code}")

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.configure(text=now.strftime("%H:%M:%S"))
        self.date_label.configure(text=now.strftime("%m.%d  %a").upper())
        try:
            self.after(1000, self._tick)
        except tk.TclError:
            pass

    def update_state(self, state: dict) -> None:
        running = bool(state.get("running"))
        starting = bool(state.get("starting"))
        online = bool(state.get("wechat_connected"))
        if starting:
            self.status_pill.set("连接中…", C["yellow_deep"], "#FFF5D8")
            self.action.set_text("连接中")
            self.action.set_enabled(False)
        elif running and online:
            self.status_pill.set("微信在线", "#178C69", C["green_pale"])
            self.action.set_text("停止机器人")
            self.action.set_enabled(True)
        elif running:
            self.status_pill.set("等待微信", C["yellow_deep"], "#FFF5D8")
            self.action.set_text("停止机器人")
            self.action.set_enabled(True)
        else:
            self.status_pill.set("待机", C["muted"], "#EDF3F7")
            self.action.set_text("启动机器人")
            self.action.set_enabled(True)
        model = state.get("model") or "未配置模型"
        self.model_pill.set(truncate("MODEL · " + model, 28), C["blue"], "#E5F1FF")
