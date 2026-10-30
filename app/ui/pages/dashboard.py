"""Dashboard page."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime

from ...paths import ASSETS
from ...prompting import is_serious
from ...ui.theme import C, display_font, font, mix, px, truncate
from ...ui.widgets import (
    Card,
    ImageBank,
    Pill,
    ProgressBar,
    RoundedButton,
    ScrollFrame,
    SectionHeading,
    ToastManager,
)
from .base import BasePage


class HeroBanner(tk.Canvas):
    def __init__(self, parent, page: "DashboardPage") -> None:
        super().__init__(parent, bg=C["bg"], height=px(214), highlightthickness=0, bd=0)
        self.page = page
        self.hero_photo = page.bank.photo(ASSETS / "arona_hero.png", (px(220), px(220)),
                                          fit="contain")
        self._image_id = None
        self._b1 = RoundedButton(self, text="启动机器人", command=page.app.toggle_bot,
                                 style="primary", width=132, height=38,
                                 bg=mix(C["hero"], C["hero_2"], 0.18), font_size=9)
        self._b2 = RoundedButton(self, text="自动调试", command=lambda: page.app.show_page("debug"),
                                 style="secondary", width=120, height=38,
                                 bg=mix(C["hero"], C["hero_2"], 0.18), font_size=9)
        self._w1 = self.create_window(px(28), px(157), window=self._b1, anchor="nw")
        self._w2 = self.create_window(px(170), px(157), window=self._b2, anchor="nw")
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _draw(self) -> None:
        self.delete("art")
        self._image_id = None
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 2:
            w, h = 900, px(214)
        # Gradient sky.
        steps = 72
        for i in range(steps):
            ratio = i / max(1, steps - 1)
            color = mix("#F2FDFF", "#C3F0FE", ratio)
            x0 = int(w * i / steps)
            x1 = int(w * (i + 1) / steps) + 1
            self.create_rectangle(x0, 0, x1, h, fill=color, outline="", tags="art")
        # Blue Archive halftone field.
        for row in range(6):
            for col in range(24):
                x = w * 0.48 + col * px(25) + (row % 2) * px(12)
                y = px(18) + row * px(25)
                if x < w - 10:
                    r = px(1.5)
                    self.create_oval(x - r, y - r, x + r, y + r,
                                     fill=mix("#B7EBFA", "#F5FEFF", row / 7), outline="",
                                     tags="art")
        # Rings and diagonal tech lines.
        self.create_oval(w - px(500), -px(145), w + px(270), px(310),
                         outline="#A8E7F8", width=2, tags="art")
        self.create_oval(w - px(440), -px(95), w + px(210), px(255),
                         outline="#D0F3FC", width=1, tags="art")
        self.create_line(px(0), h - px(1), w, h - px(1), fill="#AEE7F6", width=1, tags="art")
        for i in range(3):
            x = w - px(300) + i * px(20)
            self.create_line(x, h, x + px(54), h - px(54), fill="#A9EAF8", width=1, tags="art")
        # Signature notch.
        self.create_polygon(w - px(40), 0, w, 0, w, px(40), fill=C["sidebar"], outline="",
                            tags="art")
        self.create_rectangle(0, 0, px(5), h, fill=C["cyan"], outline="", tags="art")
        self.create_rectangle(px(5), 0, px(9), h, fill=C["pink"], outline="", tags="art")
        # Text.
        self.create_text(px(28), px(22), text="S.C.H.A.L.E.  /  ARONA SYSTEM",
                         anchor="w", fill=C["cyan_deep"], font=display_font(7, "bold"), tags="art")
        now = datetime.now()
        if now.hour < 6:
            greeting = "夜深了，老师。"
        elif now.hour < 12:
            greeting = "早上好，老师。"
        elif now.hour < 18:
            greeting = "下午好，老师。"
        else:
            greeting = "晚上好，老师。"
        self.create_text(px(28), px(51), text=greeting, anchor="w", fill=C["text"],
                         font=font(22, "bold"), tags="art")
        state = self.page.service.state()
        if state.get("running"):
            sub = "阿洛娜正在待命，会认真听老师说话的。"
            dot_color = C["green"]
            dot_text = "ONLINE"
        else:
            sub = "阿洛娜在什亭之匣待命中。准备好就一起开始吧。"
            dot_color = C["cyan_deep"]
            dot_text = "STANDBY"
        self.create_text(px(29), px(94), text=sub, anchor="w", fill=C["text_soft"],
                         font=font(9), tags="art")
        self.create_oval(px(30), px(120), px(38), px(128), fill=dot_color, outline="", tags="art")
        self.create_text(px(44), px(124), text=dot_text, anchor="w", fill=dot_color,
                         font=display_font(7, "bold"), tags="art")
        # Model note.
        model = state.get("model") or "未选择模型"
        self.create_text(px(126), px(124), text="·  " + truncate(str(model), 30), anchor="w",
                         fill=C["text_soft"], font=font(8), tags="art")
        if self.hero_photo and w > px(680):
            if self._image_id is None:
                self._image_id = self.create_image(w - px(130), h // 2 - px(4),
                                                   image=self.hero_photo, tags="art")
            else:
                self.coords(self._image_id, w - px(130), h // 2 - px(4))
            self.tag_raise(self._image_id)
        elif self._image_id is not None:
            self.delete(self._image_id)
            self._image_id = None
        self.coords(self._w1, px(28), px(153))
        self.coords(self._w2, px(170), px(153))
        if state.get("running"):
            self._b1.set_text("停止机器人")
        else:
            self._b1.set_text("启动机器人")
        self._b2.set_text("自动调试")


class MetricCard(Card):
    def __init__(self, parent, title: str, value: str, note: str, color: str,
                 icon: str) -> None:
        super().__init__(parent, padding=14, bg=C["card"])
        top = tk.Frame(self.body, bg=C["card"])
        top.pack(fill="x")
        chip = tk.Frame(top, bg=mix(color, "white", 0.82), width=px(34), height=px(34))
        chip.pack(side="left")
        chip.pack_propagate(False)
        tk.Label(chip, text=icon, bg=mix(color, "white", 0.82), fg=color,
                 font=("Segoe UI Symbol", px(15))).pack(expand=True)
        self.value = tk.Label(top, text=value, bg=C["card"], fg=C["text"],
                              font=display_font(17, "bold"))
        self.value.pack(side="right", pady=(px(3), 0))
        tk.Label(self.body, text=title, bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold"), anchor="w").pack(fill="x", pady=(px(10), 0))
        self.note = tk.Label(self.body, text=note, bg=C["card"], fg=C["muted"],
                             font=font(7), anchor="w")
        self.note.pack(fill="x", pady=(px(2), 0))

    def set(self, value: str, note: str | None = None, color: str | None = None) -> None:
        self.value.configure(text=value)
        if color:
            self.value.configure(fg=color)
        if note is not None:
            self.note.configure(text=note)


class StatusRow(tk.Frame):
    def __init__(self, parent, title: str, value: str = "—", note: str = "") -> None:
        super().__init__(parent, bg=C["card"])
        self.dot = tk.Canvas(self, width=px(10), height=px(10), bg=C["card"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(0, px(7)), pady=(px(2), 0))
        self._dot(C["muted"])
        self.title = tk.Label(self, text=title, bg=C["card"], fg=C["text"],
                              font=font(9, "bold"), anchor="w")
        self.title.pack(side="left")
        self.note = tk.Label(self, text=note, bg=C["card"], fg=C["muted"], font=font(7),
                             anchor="w")
        self.note.pack(side="left", padx=(px(7), 0), pady=(px(1), 0))
        self.value = tk.Label(self, text=value, bg=C["card"], fg=C["text_soft"],
                              font=font(8), anchor="e")
        self.value.pack(side="right")

    def _dot(self, color: str) -> None:
        self.dot.delete("all")
        self.dot.create_oval(px(2), px(2), px(8), px(8), fill=color, outline="")

    def set(self, value: str, color: str = C["text_soft"], note: str | None = None) -> None:
        self.value.configure(text=value, fg=color)
        self._dot(color)
        if note is not None:
            self.note.configure(text=note)


class DashboardPage(BasePage):
    key = "dashboard"
    title = "控制台"
    subtitle = "和老师一起，把今天也整理得井井有条。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._quick_busy = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        self.hero = HeroBanner(wrap, self)
        self.hero.pack(fill="x", pady=(0, px(14)))

        metrics = tk.Frame(wrap, bg=C["bg"])
        metrics.pack(fill="x", pady=(0, px(14)))
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1, uniform="metric")
        self.m_wechat = MetricCard(metrics, "微信连接", "待机", "UI Automation 通道", C["green"], "◎")
        self.m_replies = MetricCard(metrics, "今日回复", "0", "阿洛娜发出的消息", C["cyan_deep"], "✦")
        self.m_targets = MetricCard(metrics, "测试对象", "0", "仅回复白名单会话", C["pink_deep"], "◇")
        self.m_context = MetricCard(metrics, "上下文记忆", "30 条", "最近对话自动带入", C["blue"], "≡")
        for i, card in enumerate((self.m_wechat, self.m_replies, self.m_targets, self.m_context)):
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(6),
                                                             0 if i == 3 else px(6)))

        token_card = Card(wrap, padding=14, accent=C["pink"])
        token_card.pack(fill="x", pady=(0, px(14)))
        token_head = tk.Frame(token_card.body, bg=C["card"])
        token_head.pack(fill="x", pady=(0, px(7)))
        tk.Label(token_head, text="TOKEN 消耗", bg=C["card"], fg=C["text"],
                 font=display_font(8, "bold")).pack(side="left")
        tk.Label(token_head, text="阿洛娜累计使用的模型 Token", bg=C["card"],
                 fg=C["muted"], font=font(8)).pack(side="left", padx=(px(8), 0))
        self.balance_btn = RoundedButton(token_head, text="刷新余额",
                                         command=lambda: self.service.refresh_balance(force=True),
                                         style="ghost", width=82, height=28,
                                         bg=C["card"], font_size=7)
        self.balance_btn.pack(side="right")
        token_body = tk.Frame(token_card.body, bg=C["card"])
        token_body.pack(fill="x")
        for col in range(4):
            token_body.grid_columnconfigure(col, weight=1)
        self.token_total = MetricCard(token_body, "累计总 Token", "0",
                                      "所有模型调用", C["pink_deep"], "Σ")
        self.token_split = MetricCard(token_body, "输入 / 输出", "0 / 0",
                                      "prompt / completion", C["blue"], "⇄")
        self.token_today = MetricCard(token_body, "今日 Token", "0",
                                      "今天累计", C["cyan_deep"], "◷")
        self.token_balance = MetricCard(token_body, "DeepSeek 余额", "—",
                                        "折算人民币", C["green"], "¥")
        for i, card in enumerate((self.token_total, self.token_split,
                                  self.token_today, self.token_balance)):
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else px(5), 0 if i == 3 else px(5)))

        row = tk.Frame(wrap, bg=C["bg"])
        row.pack(fill="both", expand=True)
        row.grid_columnconfigure(0, weight=2)
        row.grid_columnconfigure(1, weight=1)
        left = tk.Frame(row, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(7)))
        right = tk.Frame(row, bg=C["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(px(7), 0))

        status_card = Card(left, padding=16, accent=C["cyan"])
        status_card.pack(fill="x")
        heading = tk.Frame(status_card.body, bg=C["card"])
        heading.pack(fill="x", pady=(0, px(10)))
        tk.Label(heading, text="运行状态", bg=C["card"], fg=C["text"],
                 font=font(11, "bold")).pack(side="left")
        self.health_btn = RoundedButton(heading, text="健康检查", command=self.app.run_health_check,
                                        style="secondary", width=92, height=28,
                                        bg=C["card"], font_size=8)
        self.health_btn.pack(side="right")
        tk.Label(heading, text="LIVE STATUS", bg=C["card"], fg=C["muted"],
                 font=display_font(6, "normal")).pack(side="left", padx=(px(9), 0), pady=(px(3), 0))
        self.rows = {
            "bot": StatusRow(status_card.body, "机器人进程", "未启动", "等待命令"),
            "wechat": StatusRow(status_card.body, "微信连接", "未连接", "DB + UIA"),
            "unread": StatusRow(status_card.body, "未读会话", "0 条", "白名单 / 其他"),
            "model": StatusRow(status_card.body, "模型服务", "未检测", "Ollama / API"),
            "memory": StatusRow(status_card.body, "上下文记忆", "0 条", "SQLite 本地存储"),
            "stickers": StatusRow(status_card.body, "表情包库", "0 张", "启用的表情包"),
        }
        for row_widget in self.rows.values():
            row_widget.pack(fill="x", pady=px(4))

        recent_card = Card(left, padding=16)
        recent_card.pack(fill="both", expand=True, pady=(px(14), 0))
        section = tk.Frame(recent_card.body, bg=C["card"])
        section.pack(fill="x", pady=(0, px(8)))
        tk.Label(section, text="最近回复", bg=C["card"], fg=C["text"],
                 font=font(11, "bold")).pack(side="left")
        tk.Button(section, text="查看全部 →", bg=C["card"], fg=C["cyan_deep"],
                  activebackground=C["card"], activeforeground=C["pink_deep"], bd=0,
                  relief="flat", font=font(8, "bold"), cursor="hand2",
                  command=lambda: self.app.show_page("monitor")).pack(side="right")
        self.recent_box = tk.Frame(recent_card.body, bg=C["card"])
        self.recent_box.pack(fill="both", expand=True)

        quick_card = Card(right, padding=16, accent=C["pink"])
        quick_card.pack(fill="both", expand=True)
        tk.Label(quick_card.body, text="快捷试聊", bg=C["card"], fg=C["text"],
                 font=font(11, "bold"), anchor="w").pack(fill="x")
        tk.Label(quick_card.body, text="只在本地调试台运行，不会发送到微信。",
                 bg=C["card"], fg=C["muted"], font=font(8), anchor="w",
                 justify="left", wraplength=px(270)).pack(fill="x", pady=(px(2), px(10)))
        self.persona_hint = tk.Label(quick_card.body, text="PERSONA · 阿洛娜",
                                     bg=C["cyan_pale"], fg=C["cyan_deep"],
                                     font=display_font(6, "bold"), anchor="w",
                                     padx=px(7), pady=px(3))
        self.persona_hint.pack(anchor="w", pady=(0, px(8)))
        self.quick_input = tk.Text(quick_card.body, height=4, bg=C["card_soft"],
                                   fg=C["text"], insertbackground=C["cyan_deep"],
                                   relief="flat", bd=0, highlightthickness=1,
                                   highlightbackground=C["line"], highlightcolor=C["cyan"],
                                   font=font(10), wrap="word", padx=px(9), pady=px(7))
        self.quick_input.pack(fill="x")
        self.quick_input.insert("1.0", "老师，在吗？")
        self.quick_btn = RoundedButton(quick_card.body, text="试聊一下", command=self._quick_send,
                                       style="primary", width=110, height=34,
                                       bg=C["card"], font_size=9)
        self.quick_btn.pack(anchor="e", pady=(px(8), px(10)))
        tk.Frame(quick_card.body, bg=C["line"], height=1).pack(fill="x")
        tk.Label(quick_card.body, text="ARONA 的输出", bg=C["card"], fg=C["muted"],
                 font=display_font(6, "bold"), anchor="w").pack(fill="x", pady=(px(10), px(4)))
        self.quick_output = tk.Label(quick_card.body, text="…… 等老师先开口。",
                                     bg=C["card_soft"], fg=C["text_soft"], font=font(9),
                                     anchor="w", justify="left", wraplength=px(280),
                                     padx=px(10), pady=px(9))
        self.quick_output.pack(fill="x")
        self.quick_sticker = tk.Label(quick_card.body, bg=C["card"], bd=0)
        self.quick_sticker.pack(pady=(px(8), 0))

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()
        self.service.refresh_balance()

    def on_event(self, kind: str, data: dict) -> None:
        if kind in ("incoming", "reply", "state", "started", "stopped", "model_health",
                    "adapter_status", "usage", "balance", "unread"):
            self.refresh()

    def refresh(self) -> None:
        state = self.service.state()
        running = state.get("running")
        connected = state.get("wechat_connected")
        backend = str(state.get("backend") or "UI Automation")
        if running and connected:
            self.m_wechat.set("在线", "微信已连接 · UI Automation", C["green"])
            self.rows["wechat"].set("已连接", C["green"], backend)
        elif running:
            self.m_wechat.set("连接中", "正在连接微信桌面端", C["yellow_deep"])
            self.rows["wechat"].set("连接中", C["yellow_deep"], "等待微信响应")
        else:
            self.m_wechat.set("待机", "UI Automation 通道", C["muted"])
            self.rows["wechat"].set("未连接", C["muted"], "启动后连接")
        self.m_replies.set(str(state.get("db", {}).get("today", 0)), "今天发出的消息", C["cyan_deep"])
        self.m_targets.set(str(state.get("target_count", 0)), "仅回复白名单会话", C["pink_deep"])
        context_limit = int(state.get("context_limit", 30))
        self.m_context.set(f"{context_limit} 条", "自动带入最近对话", C["blue"])
        usage = state.get("usage", {}) or {}
        total = int(usage.get("total", 0) or 0)
        prompt = int(usage.get("prompt", 0) or 0)
        completion = int(usage.get("completion", 0) or 0)
        self.token_total.set(f"{total:,}", f"{usage.get('requests', 0)} 次模型调用", C["pink_deep"])
        self.token_split.set(f"{prompt:,} / {completion:,}", "输入 / 输出", C["blue"])
        self.token_today.set(f"{int(usage.get('today_total', 0) or 0):,}",
                             f"今日 {int(usage.get('today_requests', 0) or 0)} 次",
                             C["cyan_deep"])
        profile = self.config.active_profile()
        is_deepseek = (str(profile.get("provider", "")) == "openai"
                       and "deepseek.com" in str(profile.get("base_url", "")).lower())
        balance = state.get("balance", {}) or {}
        if is_deepseek:
            text = str(balance.get("text") or "正在查询…")
            if balance.get("ok"):
                color = C["green"]
                note = "DeepSeek · 折算人民币"
            elif "正在" in text:
                color = C["yellow_deep"]
                note = "正在查询 DeepSeek 余额"
            else:
                color = C["muted"]
                note = balance.get("note") or "点击刷新余额重试"
            self.token_balance.set(text, note, color)
        else:
            self.token_balance.set("—", "当前模型不是 DeepSeek", C["muted"])
        self.rows["bot"].set("运行中" if running else "未启动",
                             C["green"] if running else C["muted"],
                             "正在监听消息" if running else "等待命令")
        unread = int(state.get("unread_count", 0) or 0)
        sessions = state.get("unread_sessions", []) or []
        outside = [item for item in sessions
                   if isinstance(item, dict) and not item.get("whitelisted")]
        if unread:
            note = ("未监听：" + "、".join(str(item.get("name", "")) for item in outside[:2])
                    if outside else "白名单会话有未读")
            self.rows["unread"].set(f"{unread} 条", C["yellow_deep"], note)
        else:
            self.rows["unread"].set("0 条", C["muted"], "没有未读消息")
        model = state.get("model") or "未配置"
        model_ok = state.get("model_ok")
        model_color = C["green"] if model_ok else (C["red"] if model_ok is False else C["muted"])
        self.rows["model"].set(str(model), model_color, state.get("model_status", "Ollama / API"))
        stats = state.get("db", {})
        self.rows["memory"].set(f"{stats.get('total', 0)} 条", C["blue"],
                                f"本地 SQLite · 上下文 {context_limit}")
        self.rows["stickers"].set(f"{state.get('sticker_count', 0)} 张", C["pink_deep"],
                                  "AI 可语义选择")
        person = self.personas.get(self.config.data.get("active_persona_id"))
        self.persona_hint.configure(text="PERSONA · " + person.get("name", "阿洛娜"))
        self._refresh_recent()
        self.hero._draw()
        try:
            self.app.topbar.update_state(state)
        except Exception:
            pass

    def _refresh_recent(self) -> None:
        for child in self.recent_box.winfo_children():
            child.destroy()
        replies = self.service.recent_replies(5)
        if not replies:
            empty = tk.Frame(self.recent_box, bg=C["card"])
            empty.pack(fill="x", pady=px(15))
            tk.Label(empty, text="还没有回复记录。", bg=C["card"], fg=C["muted"],
                     font=font(9)).pack()
            tk.Label(empty, text="启动机器人后，阿洛娜的回复会出现在这里。", bg=C["card"],
                     fg=C["muted"], font=font(8)).pack()
            return
        for item in replies:
            row = tk.Frame(self.recent_box, bg=C["card"], height=px(44))
            row.pack(fill="x")
            row.pack_propagate(False)
            time_label = tk.Label(row, text=str(item.get("created_iso", ""))[11:16],
                                  bg=C["card"], fg=C["muted"], font=display_font(7),
                                  width=5)
            time_label.pack(side="left", pady=(px(9), 0))
            dot = tk.Canvas(row, width=px(6), height=px(6), bg=C["card"],
                            highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(px(4), px(8)), pady=(px(10), 0))
            dot.create_oval(1, 1, px(5), px(5), fill=C["cyan"], outline="")
            body = tk.Frame(row, bg=C["card"])
            body.pack(side="left", fill="both", expand=True, pady=px(5))
            head = tk.Frame(body, bg=C["card"])
            head.pack(fill="x")
            tk.Label(head, text=truncate(item.get("chat", ""), 18), bg=C["card"],
                     fg=C["text"], font=font(8, "bold")).pack(side="left")
            if item.get("sticker_name"):
                tk.Label(head, text="☺ " + item["sticker_name"], bg=C["card"],
                         fg=C["pink_deep"], font=font(7)).pack(side="right")
            tk.Label(body, text=truncate(item.get("content") or "（表情包）", 44),
                     bg=C["card"], fg=C["text_soft"], font=font(8), anchor="w").pack(fill="x")
            tk.Frame(self.recent_box, bg=C["line"], height=1).pack(fill="x")

    # ------------------------------------------------------------------
    def _quick_send(self) -> None:
        if self._quick_busy:
            return
        text = self.quick_input.get("1.0", "end").strip()
        if not text:
            self.app.toast("先写一句想对阿洛娜说的话吧。", "warn")
            return
        self._quick_busy = True
        self.quick_btn.set_busy(True, "思考中")
        self.quick_output.configure(text="阿洛娜正在认真想……", fg=C["muted"])
        self.quick_sticker.configure(image="")
        self.quick_sticker.image = None
        self.quick_input.delete("1.0", "end")

        def worker():
            try:
                result = self.service.debug_chat(text, chat_name="控制台速聊")
                self.after(0, lambda: self._show_quick(result))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._quick_error(message))

        threading.Thread(target=worker, daemon=True).start()

    def _show_quick(self, result: dict) -> None:
        self._quick_busy = False
        self.quick_btn.set_busy(False, "试聊一下")
        self.quick_output.configure(text=result.get("text") or "（只发了一张表情包）",
                                    fg=C["text"])
        sticker = result.get("sticker")
        if sticker and result.get("sticker_path"):
            photo = self.bank.photo(result["sticker_path"], (px(108), px(108)), fit="contain")
            if photo:
                self.quick_sticker.configure(image=photo)
                self.quick_sticker.image = photo
        self.quick_output.configure(text=(self.quick_output.cget("text") + "\n\n" +
                                          f"☺ {sticker.get('name')}") if sticker else
                                    self.quick_output.cget("text"))

    def _quick_error(self, message: str) -> None:
        self._quick_busy = False
        self.quick_btn.set_busy(False, "试聊一下")
        self.quick_output.configure(text="唔……连接模型失败了。\n" + message, fg=C["red"])
        self.app.toast("模型调用失败，请检查模型配置。", "error")
