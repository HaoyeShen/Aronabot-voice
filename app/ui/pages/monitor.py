"""Live operation monitor and reply history."""

from __future__ import annotations

import tkinter as tk

from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import Card, RoundedButton, ScrollFrame, SectionHeading
from .base import BasePage


class HealthLine(tk.Frame):
    def __init__(self, parent, title: str, value: str = "—", detail: str = "") -> None:
        super().__init__(parent, bg=C["card"])
        self.dot = tk.Canvas(self, width=px(9), height=px(9), bg=C["card"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(0, px(7)), pady=(px(3), 0))
        self._set_dot(C["muted"])
        mid = tk.Frame(self, bg=C["card"])
        mid.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(mid, text=title, bg=C["card"], fg=C["text"],
                              font=font(9, "bold"), anchor="w")
        self.title.pack(fill="x")
        self.detail = tk.Label(mid, text=detail, bg=C["card"], fg=C["muted"],
                               font=font(7), anchor="w")
        self.detail.pack(fill="x")
        self.value = tk.Label(self, text=value, bg=C["card"], fg=C["text_soft"],
                              font=font(8), anchor="e")
        self.value.pack(side="right")

    def _set_dot(self, color: str) -> None:
        self.dot.delete("all")
        self.dot.create_oval(px(1), px(1), px(8), px(8), fill=color, outline="")

    def set(self, value: str, detail: str = "", color: str = C["text_soft"]) -> None:
        self.value.configure(text=value, fg=color)
        self.detail.configure(text=detail)
        self._set_dot(color)


class MonitorPage(BasePage):
    key = "monitor"
    title = "运行监控"
    subtitle = "阿洛娜的每一次回复，都在这里留下清晰的记录。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        top = tk.Frame(wrap, bg=C["bg"])
        top.pack(fill="x", pady=(0, px(12)))
        self.scan_btn = RoundedButton(top, text="运行健康检查", command=self.app.run_health_check,
                                      style="primary", width=132, height=34,
                                      bg=C["bg"], font_size=9)
        self.scan_btn.pack(side="right")
        RoundedButton(top, text="自动调试", command=lambda: self.app.show_page("debug"),
                      style="secondary", width=94, height=34, bg=C["bg"],
                      font_size=9).pack(side="right", padx=(0, px(8)))
        tk.Label(top, text="LIVE OPERATIONS", bg=C["bg"], fg=C["cyan_deep"],
                 font=display_font(7, "bold")).pack(side="left", pady=(px(8), 0))

        cards = tk.Frame(wrap, bg=C["bg"])
        cards.pack(fill="x", pady=(0, px(12)))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="health")
        self.card_runtime = self._metric(cards, "运行状态", "待机", C["cyan"], "◉")
        self.card_wechat = self._metric(cards, "微信连接", "离线", C["green"], "◎")
        self.card_model = self._metric(cards, "模型服务", "未检测", C["blue"], "◇")
        self.card_context = self._metric(cards, "本地记忆", "0 条", C["pink_deep"], "≡")
        for i, card in enumerate((self.card_runtime, self.card_wechat, self.card_model,
                                  self.card_context)):
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else px(6), 0 if i == 3 else px(6)))

        body = tk.Frame(wrap, bg=C["bg"])
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        health = Card(body, padding=16, accent=C["cyan"])
        health.grid(row=0, column=0, sticky="nsew", padx=(0, px(7)))
        SectionHeading(health.body, "系统健康", "实时快照").pack(fill="x", pady=(0, px(9)))
        self.health = {
            "bot": HealthLine(health.body, "机器人进程", "待机", "未启动"),
            "wechat": HealthLine(health.body, "微信通道", "离线", "wechatauto · UIA"),
            "model": HealthLine(health.body, "语言模型", "—", "未检测"),
            "db": HealthLine(health.body, "记忆数据库", "—", "SQLite"),
            "sticker": HealthLine(health.body, "表情包资源", "—", "本地文件"),
            "direction": HealthLine(health.body, "消息方向", "仅入站触发", "自己 / 手动消息不回复"),
            "privacy": HealthLine(health.body, "隐私模式", "DB + UIA", "无截图 / 无录屏"),
        }
        for line in self.health.values():
            line.pack(fill="x", pady=px(5))
        tk.Frame(health.body, bg=C["line"], height=1).pack(fill="x", pady=px(10))
        self.last_checked = tk.Label(health.body, text="尚未检查", bg=C["card"],
                                      fg=C["muted"], font=font(8), anchor="w")
        self.last_checked.pack(fill="x")
        self.error_label = tk.Label(health.body, text="", bg=C["card"], fg=C["red"],
                                     font=font(8), anchor="w", justify="left",
                                     wraplength=px(300))
        self.error_label.pack(fill="x", pady=(px(3), 0))

        replies = Card(body, padding=16)
        replies.grid(row=0, column=1, sticky="nsew", padx=(px(7), 0))
        header = tk.Frame(replies.body, bg=C["card"])
        header.pack(fill="x", pady=(0, px(8)))
        tk.Label(header, text="回复记录", bg=C["card"], fg=C["text"],
                 font=font(11, "bold")).pack(side="left")
        tk.Label(header, text="最近 20 条 · 点击查看完整内容", bg=C["card"],
                 fg=C["muted"], font=font(8)).pack(side="left", padx=(px(8), 0), pady=(px(4), 0))
        self.refresh_btn = RoundedButton(header, text="刷新", command=self.refresh,
                                         style="ghost", width=54, height=26,
                                         bg=C["card"], font_size=8)
        self.refresh_btn.pack(side="right")
        self.reply_box = tk.Frame(replies.body, bg=C["card"])
        self.reply_box.pack(fill="both", expand=True)
        self._build_header_row()
        self.reply_rows = tk.Frame(replies.body, bg=C["card"])
        self.reply_rows.pack(fill="both", expand=True)
        self.empty_label = tk.Label(self.reply_rows, text="还没有回复记录。\n启动机器人并收到消息后，这里会实时更新。",
                                    bg=C["card"], fg=C["muted"], font=font(9),
                                    justify="center")
        self.empty_label.pack(pady=px(30))

    def _metric(self, parent, title: str, value: str, color: str, icon: str) -> Card:
        card = Card(parent, padding=13)
        row = tk.Frame(card.body, bg=C["card"])
        row.pack(fill="x")
        chip = tk.Frame(row, bg=C["card_soft"], width=px(29), height=px(29))
        chip.pack(side="left")
        chip.pack_propagate(False)
        tk.Label(chip, text=icon, bg=C["card_soft"], fg=color,
                 font=("Segoe UI Symbol", px(13))).pack(expand=True)
        value_label = tk.Label(row, text=value, bg=C["card"], fg=C["text"],
                               font=font(12, "bold"))
        value_label.pack(side="right")
        tk.Label(card.body, text=title, bg=C["card"], fg=C["muted"],
                 font=font(8), anchor="w").pack(fill="x", pady=(px(6), 0))
        card.value_label = value_label
        card.color = color
        return card

    def _build_header_row(self) -> None:
        header = tk.Frame(self.reply_box, bg=C["card_soft"], height=px(28))
        header.pack(fill="x")
        header.pack_propagate(False)
        specs = [("时间", 6, 0), ("对象", 12, 0), ("发送者 / 消息", 26, 1),
                 ("阿洛娜回复", 32, 1), ("表情包", 10, 0), ("耗时", 7, 0)]
        for text, width, weight in specs:
            tk.Label(header, text=text, bg=C["card_soft"], fg=C["muted"],
                     font=font(7, "bold"), width=width, anchor="w").pack(
                side="left", padx=px(3), fill="x", expand=bool(weight))

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()

    def on_event(self, kind: str, data: dict) -> None:
        if kind in ("reply", "incoming", "state", "started", "stopped", "adapter_status",
                    "model_health", "context_synced"):
            self.refresh()
        if kind == "error":
            self.error_label.configure(text=data.get("message", ""))
        if kind == "model_health":
            self.last_checked.configure(text="最近检查：" + data.get("message", ""))

    def refresh(self) -> None:
        state = self.service.state()
        snapshot = self.service.health_snapshot()
        running = bool(state.get("running"))
        connected = bool(state.get("wechat_connected"))
        today = state.get("db", {}).get("today", 0)
        self.card_runtime.value_label.configure(
            text="运行中" if running else "待机",
            fg=C["green"] if running else C["text_soft"])
        self.card_wechat.value_label.configure(
            text="在线" if connected else ("等待" if running else "离线"),
            fg=C["green"] if connected else C["text_soft"])
        model_ok = snapshot.get("model_ok")
        self.card_model.value_label.configure(
            text="正常" if model_ok else ("异常" if model_ok is False else "未检测"),
            fg=C["green"] if model_ok else (C["red"] if model_ok is False else C["text_soft"]))
        self.card_context.value_label.configure(text=f"{state.get('db', {}).get('total', 0)} 条")
        self.health["bot"].set("运行中" if running else "待机",
                               "已连接微信" if connected else "未连接",
                               C["green"] if running else C["muted"])
        self.health["wechat"].set("在线" if connected else "离线",
                                  snapshot.get("backend") or "尚未建立连接",
                                  C["green"] if connected else C["muted"])
        self.health["model"].set("正常" if model_ok else ("异常" if model_ok is False else "未检测"),
                                 truncate(snapshot.get("model_status") or snapshot.get("model") or "—", 36),
                                 C["green"] if model_ok else (C["red"] if model_ok is False else C["muted"]))
        self.health["db"].set("正常" if snapshot.get("db_ok") else "异常",
                              f"SQLite · {state.get('db', {}).get('total', 0)} 条记录",
                              C["green"] if snapshot.get("db_ok") else C["red"])
        self.health["sticker"].set(f"{snapshot.get('stickers', 0)} 张",
                                   "启用且文件存在" if snapshot.get("stickers") else "还没有可用表情包",
                                   C["green"] if snapshot.get("stickers") else C["yellow_deep"])
        self.health["direction"].set("仅入站", "发送者与方向已标记；自己/手动消息只入上下文",
                                     C["cyan_deep"])
        self.health["privacy"].set("DB + UIA", "无截图 / 无录屏 / 无 Codex", C["cyan_deep"])
        model_status = str(snapshot.get("model_status") or "")
        if model_status and model_status != "尚未检测":
            self.last_checked.configure(text="最近检查：" + model_status)
        elif snapshot.get("last_probe_at"):
            self.last_checked.configure(text="微信通道最近探测：刚刚")
        else:
            self.last_checked.configure(text="尚未检查")
        self.error_label.configure(text=snapshot.get("last_error") or "")
        self._render_replies()

    def _render_replies(self) -> None:
        for child in self.reply_rows.winfo_children():
            child.destroy()
        rows = self.service.recent_replies(20)
        if not rows:
            tk.Label(self.reply_rows, text="还没有回复记录。\n启动机器人并收到消息后，这里会实时更新。",
                     bg=C["card"], fg=C["muted"], font=font(9),
                     justify="center").pack(pady=px(30))
            return
        for index, item in enumerate(rows):
            bg = C["card"] if index % 2 == 0 else "#FAFDFF"
            row = tk.Frame(self.reply_rows, bg=bg, height=px(34), cursor="hand2")
            row.pack(fill="x")
            row.pack_propagate(False)
            time_text = str(item.get("created_iso", ""))[11:16]
            sender = item.get("user_sender") or "老师"
            values = [
                (time_text, 6, 0, C["muted"], font(7)),
                (truncate(item.get("chat", ""), 14), 12, 0, C["cyan_deep"], font(8, "bold")),
                (truncate(f"{sender}：{item.get('user_content') or ''}", 34), 26, 1,
                 C["text_soft"], font(8)),
                (truncate(item.get("content") or "（表情包）", 36), 32, 1, C["text"], font(8)),
                ("☺ " + str(item.get("sticker_name")) if item.get("sticker_name") else "—",
                 10, 0, C["pink_deep"] if item.get("sticker_name") else C["muted"], font(7)),
                (f"{item.get('latency_ms', 0)} ms", 7, 0, C["muted"], font(7)),
            ]
            for text, width, weight, color, fnt in values:
                tk.Label(row, text=text, bg=bg, fg=color, font=fnt, width=width,
                         anchor="w").pack(side="left", padx=px(3), fill="x",
                                          expand=bool(weight))
            tk.Frame(self.reply_rows, bg=C["line"], height=1).pack(fill="x")
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda _e, data=item: self.app.show_reply_detail(data))
                widget.bind("<Enter>", lambda _e, r=row: r.configure(bg=C["card_blue"]))
                widget.bind("<Leave>", lambda _e, r=row, b=bg: r.configure(bg=b))
