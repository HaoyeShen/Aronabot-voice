"""Listening & reply behaviour: pacing, stale messages, quote and triggers."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime

from ...ui.theme import C, font, px
from ...ui.widgets import (
    Card,
    LabeledEntry,
    RoundedButton,
    ScrollFrame,
    SectionHeading,
    ToggleRow,
    confirm,
)
from .base import BasePage


class ListenReplyPage(BasePage):
    key = "listen_reply"
    title = "监听与回复"
    subtitle = "决定阿洛娜什么时候回复、如何引用，以及哪些话算是在叫她。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._build()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(20)))

        top = tk.Frame(wrap, bg=C["bg"])
        top.pack(fill="x", pady=(0, px(12)))
        tk.Label(top, text="LISTEN & REPLY", bg=C["bg"], fg=C["cyan_deep"],
                 font=font(7, "bold")).pack(side="left", pady=(px(8), 0))
        RoundedButton(top, text="保存设置", command=self.save, style="primary",
                      width=112, height=34, bg=C["bg"], font_size=9).pack(side="right")
        self.status = tk.Label(top, text="修改后请点击保存", bg=C["bg"], fg=C["muted"],
                               font=font(8))
        self.status.pack(side="right", padx=(0, px(9)), pady=(px(8), 0))

        columns = tk.Frame(wrap, bg=C["bg"])
        columns.pack(fill="both", expand=True)
        columns.grid_columnconfigure(0, weight=1, uniform="reply")
        columns.grid_columnconfigure(1, weight=1, uniform="reply")
        left = tk.Frame(columns, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(7)))
        right = tk.Frame(columns, bg=C["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(px(7), 0))

        reply = Card(left, padding=16, accent=C["cyan"])
        reply.pack(fill="x")
        SectionHeading(reply.body, "回复行为", "怎么回、什么时候回").pack(
            fill="x", pady=(0, px(10))
        )
        self.auto_reply = ToggleRow(
            reply.body, "自动回复",
            "关闭后仍会记录消息与上下文，但不会发回微信。",
            self.config.bot.get("auto_reply", True),
        )
        self.auto_reply.pack(fill="x", pady=px(5))
        self.auto_start = ToggleRow(
            reply.body, "启动 ARONA 时自动连接",
            "打开程序后自动开始监听，无需再点「启动机器人」。",
            self.config.bot.get("auto_start", True),
        )
        self.auto_start.pack(fill="x", pady=px(5))
        self.auto_sticker = ToggleRow(
            reply.body, "允许使用表情包",
            "阿洛娜会结合语境从已启用的表情包里语义选择。",
            self.config.bot.get("auto_sticker", True),
        )
        self.auto_sticker.pack(fill="x", pady=px(5))
        self.quiet = ToggleRow(
            reply.body, "免打扰时段",
            "该时段内只记录消息，不自动回复。",
            self.config.bot.get("quiet_hours_enabled", False),
        )
        self.quiet.pack(fill="x", pady=px(5))

        quiet_times = tk.Frame(reply.body, bg=C["card"])
        quiet_times.pack(fill="x", pady=(px(4), px(8)))
        self.quiet_start = LabeledEntry(
            quiet_times, "开始", self.config.bot.get("quiet_start", "23:30"),
            hint="HH:MM", width=8,
        )
        self.quiet_start.pack(side="left", fill="x", expand=True, padx=(0, px(5)))
        self.quiet_end = LabeledEntry(
            quiet_times, "结束", self.config.bot.get("quiet_end", "07:30"),
            hint="HH:MM", width=8,
        )
        self.quiet_end.pack(side="left", fill="x", expand=True, padx=(px(5), 0))

        group_frame = tk.Frame(reply.body, bg=C["card"])
        group_frame.pack(fill="x", pady=(px(4), 0))
        self.group_at_always = ToggleRow(
            group_frame, "群聊 @ 后必回复",
            "被 @ 时不管概率多少，都会回复。",
            self.config.bot.get("group_at_always_reply", True),
        )
        self.group_at_always.pack(fill="x")
        self.group_at_self_name = ToggleRow(
            group_frame, "群聊 @ 我的微信昵称也触发",
            "关闭后只认 @阿洛娜 / @ARONA，不把登录账号昵称算作 @。",
            self.config.bot.get("group_at_include_self_name", True),
        )
        self.group_at_self_name.pack(fill="x")
        self.group_probability = ToggleRow(
            group_frame, "群聊未 @ 概率回复",
            "没被 @ 时按下面的概率决定是否回复；关闭后未 @ 一律不回复。",
            self.config.bot.get("group_probability_reply", True),
        )
        self.group_probability.pack(fill="x")
        probability_row = tk.Frame(group_frame, bg=C["card"])
        probability_row.pack(fill="x", pady=(px(4), 0))
        self.group_probability_value = LabeledEntry(
            probability_row, "未 @ 回复概率（%）",
            str(round(float(self.config.bot.get("group_reply_probability", 0.3)) * 100)),
            hint="0–100", width=8,
        )
        self.group_probability_value.pack(fill="x")

        timing = Card(right, padding=16, accent=C["pink"])
        timing.pack(fill="x")
        SectionHeading(timing.body, "时效、引用与标点", "别回太旧的消息").pack(
            fill="x", pady=(0, px(10))
        )
        self.stale_minutes = LabeledEntry(
            timing.body, "超过多久不回复（分钟）",
            str(self.config.bot.get("stale_reply_minutes", 10)),
            hint="默认 10；旧消息只存上下文", width=10,
        )
        self.stale_minutes.pack(fill="x", pady=(0, px(7)))
        self.quote_reply = ToggleRow(
            timing.body, "使用微信引用回复",
            "引用阿洛娜正在回复的那条消息；找不到时自动改为普通发送。",
            self.config.bot.get("quote_reply", False),
        )
        self.quote_reply.pack(fill="x", pady=px(5))
        self.replace_punctuation = ToggleRow(
            timing.body, "把句号、逗号、分号变成空格",
            "开启后阿洛娜的文字更松散，不再出现这些标点。",
            self.config.bot.get("replace_punctuation", False),
        )
        self.replace_punctuation.pack(fill="x", pady=px(5))
        tk.Label(
            timing.body,
            text="超过时效的旧消息不会触发回复，也不会作为新鲜上下文使用；已有摘要仍会保留。",
            bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
            justify="left", wraplength=px(420),
        ).pack(fill="x", pady=(px(5), 0))

        voice = Card(right, padding=16, accent=C["cyan"])
        voice.pack(fill="x", pady=(px(12), 0))
        SectionHeading(voice.body, "语音气泡", "偶尔用声音回复").pack(
            fill="x", pady=(0, px(8))
        )
        self.voice_enabled = ToggleRow(
            voice.body, "允许发送微信语音气泡",
            "开启后按概率把文字回复改为语音；失败会保留文字发送。",
            self.config.bot.get("voice_enabled", False),
        )
        self.voice_enabled.pack(fill="x", pady=px(4))
        self.voice_probability = LabeledEntry(
            voice.body, "语音概率（%）",
            str(round(float(self.config.bot.get("voice_probability", 0.5)) * 100)),
            hint="默认 50", width=8,
        )
        self.voice_probability.pack(fill="x", pady=(px(4), px(3)))
        tk.Label(
            voice.body,
            text="需要 WecVoice-auto、VB-CABLE 虚拟声卡和微信语音权限；语音音色默认阿洛娜。",
            bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
            justify="left", wraplength=px(420),
        ).pack(fill="x")

        trigger = Card(right, padding=16)
        trigger.pack(fill="x", pady=(px(12), 0))
        SectionHeading(trigger.body, "关键词触发", "没 @ 也可能是在叫她").pack(
            fill="x", pady=(0, px(8))
        )
        self.trigger_enabled = ToggleRow(
            trigger.body, "启用名称/关键词触发",
            "群聊消息里出现这些词时，即使没有 @ 也会回复。",
            self.config.bot.get("trigger_enabled", True),
        )
        self.trigger_enabled.pack(fill="x", pady=px(4))
        tk.Label(
            trigger.body, text="关键词（每行一个，也可以逗号分隔）",
            bg=C["card"], fg=C["text_soft"], font=font(8, "bold"), anchor="w",
        ).pack(fill="x", pady=(px(4), px(3)))
        self.trigger_keywords = tk.Text(
            trigger.body, height=4, bg=C["card_soft"], fg=C["text"],
            insertbackground=C["text"], relief="flat", font=font(8),
            highlightthickness=1, highlightbackground=C["line"],
            highlightcolor=C["cyan"], padx=px(6), pady=px(4),
        )
        self.trigger_keywords.pack(fill="x")
        self.trigger_keywords.insert(
            "1.0", "\n".join(str(x) for x in
                             self.config.bot.get("trigger_keywords", ["阿洛娜", "Arona", "ARONA"]))
        )
        tk.Label(
            trigger.body, text="例如“阿洛娜你好啊”会触发；清空则只认 @。",
            bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
        ).pack(fill="x", pady=(px(4), 0))

    def save(self) -> None:
        try:
            stale = max(1, min(1440, int(self.stale_minutes.get() or 10)))
            probability = max(0, min(100, int(float(
                self.group_probability_value.get() or 30
            ))))
            voice_probability = max(0, min(100, int(float(
                self.voice_probability.get() or 50
            ))))
            for value in (self.quiet_start.get(), self.quiet_end.get()):
                if value:
                    datetime.strptime(value, "%H:%M")
        except ValueError:
            self.app.toast("请检查概率、分钟数和免打扰时间格式。", "error")
            return
        keywords = [line.strip() for line in
                    self.trigger_keywords.get("1.0", "end").replace("，", ",")
                    .replace("\n", ",").split(",") if line.strip()]
        self.config.bot.update({
            "auto_reply": self.auto_reply.get(),
            "auto_start": self.auto_start.get(),
            "auto_sticker": self.auto_sticker.get(),
            "quiet_hours_enabled": self.quiet.get(),
            "quiet_start": self.quiet_start.get(),
            "quiet_end": self.quiet_end.get(),
            "group_at_always_reply": self.group_at_always.get(),
            "group_at_include_self_name": self.group_at_self_name.get(),
            "group_probability_reply": self.group_probability.get(),
            "group_reply_probability": probability / 100.0,
            "voice_enabled": self.voice_enabled.get(),
            "voice_probability": voice_probability / 100.0,
            "stale_reply_minutes": stale,
            "quote_reply": self.quote_reply.get(),
            "replace_punctuation": self.replace_punctuation.get(),
            "trigger_enabled": self.trigger_enabled.get(),
            "trigger_keywords": keywords or ["阿洛娜"],
        })
        self.config.save()
        self.service.reload_config()
        self.status.configure(text="已保存 · " + datetime.now().strftime("%H:%M:%S"),
                              fg=C["green"])
        self.app.toast("监听与回复设置已保存。", "success")
        self.app.refresh_header()
