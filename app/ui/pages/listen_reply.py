"""Listening & reply behaviour: pacing, stale messages, quote and triggers."""

from __future__ import annotations

import tkinter as tk
import threading
import logging
from datetime import datetime
from tkinter import ttk

from ...ui.theme import C, font, px
from ...ui.widgets import (
    BaseDialog,
    Card,
    LabeledEntry,
    RoundedButton,
    ScrollFrame,
    SectionHeading,
    ToggleRow,
    confirm,
)
from .base import BasePage

log = logging.getLogger(__name__)


class VoicePresetDialog(BaseDialog):
    def __init__(self, parent, preset: dict, on_save) -> None:
        super().__init__(parent, "编辑语音音色", "调整 Edge TTS 音色与参数",
                         width=520, height=520, icon=getattr(parent, "icon", None))
        self.preset = dict(preset)
        self.on_save = on_save
        body = tk.Frame(self.body, bg=C["card"])
        body.pack(fill="both", expand=True, padx=px(22), pady=px(16))
        self.label = LabeledEntry(body, "音色名称", preset.get("label", ""), width=28)
        self.label.pack(fill="x", pady=(0, px(7)))
        self.voice = LabeledEntry(
            body, "Edge 音色 ID", preset.get("voice", "zh-CN-XiaoyiNeural"),
            hint="例如 zh-CN-XiaoyiNeural", width=32,
        )
        self.voice.pack(fill="x", pady=(0, px(7)))
        row = tk.Frame(body, bg=C["card"])
        row.pack(fill="x", pady=(0, px(7)))
        self.rate = LabeledEntry(row, "语速", preset.get("rate", "+0%"), width=8)
        self.rate.pack(side="left", fill="x", expand=True, padx=(0, px(4)))
        self.pitch = LabeledEntry(row, "音调", preset.get("pitch", "+0Hz"), width=8)
        self.pitch.pack(side="left", fill="x", expand=True, padx=px(4))
        self.volume = LabeledEntry(row, "音量", preset.get("volume", "+0%"), width=8)
        self.volume.pack(side="left", fill="x", expand=True, padx=(px(4), 0))
        self.description = LabeledEntry(
            body, "说明（可选）", preset.get("description", ""), width=32,
        )
        self.description.pack(fill="x", pady=(0, px(6)))
        self.error = tk.Label(body, text="", bg=C["card"], fg=C["red"], font=font(8),
                              anchor="w")
        self.error.pack(fill="x")
        self.add_footer("取消", self.close, "secondary", 90)
        self.add_footer("保存", self.save, "primary", 100)

    def save(self) -> None:
        label = self.label.get().strip()
        voice = self.voice.get().strip()
        if not label or not voice:
            self.error.configure(text="音色名称和 Edge 音色 ID 不能为空。")
            return
        def signed(value: str, suffix: str) -> str:
            value = (value or "").strip()
            if not value:
                return "+0" + suffix
            if not value.startswith(("+", "-")):
                value = "+" + value
            return value if value.endswith(suffix) else value + suffix
        preset = dict(self.preset)
        preset.update({
            "label": label,
            "voice": voice,
            "rate": signed(self.rate.get(), "%"),
            "pitch": signed(self.pitch.get(), "Hz"),
            "volume": signed(self.volume.get(), "%"),
            "description": self.description.get().strip(),
        })
        try:
            from wecvoice_auto.voices import resolve_voice
            resolve_voice(preset["voice"], rate=preset["rate"],
                          pitch=preset["pitch"], volume=preset["volume"])
        except Exception as exc:
            self.error.configure(text=f"参数格式不正确：{exc}")
            return
        self.on_save(preset)
        self.close()


class ListenReplyPage(BasePage):
    key = "listen_reply"
    title = "监听与回复"
    subtitle = "决定阿洛娜什么时候回复、如何引用，以及哪些话算是在叫她。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._voice_test_busy = False
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
            "没被 @ 时，确认对阿洛娜或全群说话后按概率回复；对其他人说话不插话。",
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
        self.followup_timeout = LabeledEntry(
            group_frame, "@ 后续聊超时（分钟）",
            str(self.config.bot.get("followup_timeout_minutes", 5)),
            hint="默认 5，范围 1–1440", width=8,
        )
        self.followup_timeout.pack(fill="x", pady=(px(8), 0))
        tk.Label(group_frame, text="@ 回复后，确认仍对她说话就继续回复，无需再次 @。\n连续 5 条非对她发言或超时后退出，各群独立。",
                 bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
                 justify="left", wraplength=px(420)).pack(fill="x", pady=(px(4), 0))

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
            voice.body, "允许自动发送微信语音气泡",
            "普通回复按概率选语音；明确要求声音时优先响应，失败回退文字。",
            self.config.bot.get("voice_enabled", False),
        )
        self.voice_enabled.pack(fill="x", pady=px(4))
        self.natural_voice = ToggleRow(
            voice.body, "保留自然语音停顿", "语音保留句读，不受文字标点替换影响；关闭可恢复旧读法。",
            self.config.bot.get("natural_voice", True),
        )
        self.natural_voice.pack(fill="x", pady=px(4))
        self.voice_probability = LabeledEntry(
            voice.body, "语音概率（%）",
            str(round(float(self.config.bot.get("voice_probability", 0.5)) * 100)),
            hint="默认 50", width=8,
        )
        self.voice_probability.pack(fill="x", pady=(px(4), px(3)))
        self.voice_max_chars = LabeledEntry(
            voice.body, "语音最多字数",
            str(self.config.bot.get("voice_max_chars", 30)),
            hint="默认 30", width=8,
        )
        self.voice_max_chars.pack(fill="x", pady=(px(3), px(3)))
        preset_row = tk.Frame(voice.body, bg=C["card"])
        preset_row.pack(fill="x", pady=(px(4), px(4)))
        tk.Label(preset_row, text="音色", bg=C["card"], fg=C["text_soft"],
                 font=font(8, "bold")).pack(side="left", padx=(0, px(5)))
        self.voice_preset = ttk.Combobox(
            preset_row, state="readonly", style="Arona.TCombobox", font=font(8),
        )
        self.voice_preset.pack(side="left", fill="x", expand=True)
        self._refresh_voice_presets()
        buttons = tk.Frame(voice.body, bg=C["card"])
        buttons.pack(fill="x", pady=(px(2), px(4)))
        RoundedButton(buttons, text="新增", command=self.add_voice,
                      style="ghost", width=52, height=28, bg=C["card"], font_size=7).pack(side="left")
        RoundedButton(buttons, text="编辑", command=self.edit_voice,
                      style="ghost", width=52, height=28, bg=C["card"], font_size=7).pack(side="left", padx=px(4))
        RoundedButton(buttons, text="删除", command=self.delete_voice,
                      style="ghost", width=52, height=28, bg=C["card"], font_size=7).pack(side="left")
        RoundedButton(buttons, text="试听「你好」", command=self.preview_voice,
                      style="secondary", width=100, height=28, bg=C["card"], font_size=7).pack(side="right")
        tk.Label(
            voice.body,
            text="需要 WecVoice-auto、VB-CABLE 虚拟声卡和微信语音权限；试听只在本机播放，不发送到微信。",
            bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
            justify="left", wraplength=px(420),
        ).pack(fill="x")

        tk.Label(voice.body, text="测试语音目标群聊（完整群名）", bg=C["card"],
                 fg=C["text_soft"], font=font(8, "bold"), anchor="w").pack(
                     fill="x", pady=(px(12), px(4)))
        self.voice_test_chat = ttk.Combobox(
            voice.body, style="Arona.TCombobox", font=font(8),
        )
        self.voice_test_chat.pack(fill="x")
        self.voice_test_chat.set(self.config.bot.get("voice_test_chat", "test"))
        self._refresh_voice_test_chats()
        self.voice_test_btn = RoundedButton(
            voice.body, text="发送测试语音气泡", command=self.send_test_voice,
            style="secondary", width=178, height=32, bg=C["card"], font_size=8,
        )
        self.voice_test_btn.pack(anchor="w", pady=(px(7), px(4)))
        self.voice_test_status = tk.Label(
            voice.body, text="请先连接微信；使用当前音色发送「你好，语音测试。」，失败回退文字。",
            bg=C["card"], fg=C["muted"], font=font(7), anchor="w",
            justify="left", wraplength=px(420),
        )
        self.voice_test_status.pack(fill="x")

        trigger = Card(right, padding=16)
        trigger.pack(fill="x", pady=(px(12), 0))
        SectionHeading(trigger.body, "关键词触发", "没 @ 也可能是在叫她").pack(
            fill="x", pady=(0, px(8))
        )
        self.trigger_enabled = ToggleRow(
            trigger.body, "启用名称/关键词触发",
            "对阿洛娜说话时直接触发；第三人称公开讨论按群聊概率参与。",
            self.config.bot.get("trigger_enabled", True),
        )
        self.trigger_enabled.pack(fill="x", pady=px(4))
        self.trigger_semantic = ToggleRow(
            self.trigger_enabled.master, "结合上下文判断是否在叫阿洛娜",
            "区分直接呼叫与第三人称讨论；判断失败回退本地规则。",
            self.config.bot.get("trigger_semantic_check", True),
        )
        self.trigger_semantic.pack(fill="x", pady=px(4))
        self.read_links = ToggleRow(
            self.trigger_enabled.master, "读取分享链接与视频号卡片",
            "通过网络读取公开网页；视频号仅参考标题和封面，不在微信中打开。",
            self.config.bot.get("read_links", True),
        )
        self.read_links.pack(fill="x", pady=px(4))
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

    def on_show(self) -> None:
        self._refresh_voice_test_chats()

    def _refresh_voice_test_chats(self) -> None:
        names = ["test"] + [str(t["name"]) for t in self.config.data.get("targets", [])
                            if t.get("type") == "group"]
        self.voice_test_chat.configure(values=list(dict.fromkeys(names)))

    def send_test_voice(self) -> None:
        if self._voice_test_busy:
            return
        chat = self.voice_test_chat.get().strip()
        if not chat:
            self.app.toast("请填写测试群聊的完整名称。", "warn")
            return
        preset = self._selected_voice_preset()
        self.config.bot["voice_test_chat"] = chat
        try:
            self.config.save()
        except OSError as exc:
            log.warning("保存测试语音目标失败：%s", exc)
            self.app.toast("无法保存测试群聊，请检查配置文件权限。", "error")
            return
        self._voice_test_busy = True
        self.voice_test_btn.set_busy(True, "正在发送…")
        self.voice_test_status.configure(text=f"正在向「{chat}」发送测试语音，请勿切换微信会话。",
                                         fg=C["cyan_deep"])

        def worker() -> None:
            try:
                result = self.service.send_test_voice(chat, preset_id=preset["id"])
            except Exception as exc:
                log.warning("测试语音任务失败：%s", exc)
                result = {"ok": False, "message": "测试语音发送失败，请查看运行日志。"}
            # The existing main-thread event pump also works while this page is hidden.
            self.service.emit("voice_test_done", result)

        threading.Thread(target=worker, daemon=True).start()

    def on_event(self, kind: str, data: dict) -> None:
        if kind != "voice_test_done":
            return
        self._voice_test_busy = False
        self.voice_test_btn.set_busy(False, "发送测试语音气泡")
        message = data.get("message") or "测试语音发送失败。"
        self.voice_test_status.configure(text=message,
                                         fg=C["green"] if data.get("ok") else C["red"])
        self.app.toast(message, "success" if data.get("ok") else "warn")

    def _selected_voice_preset(self) -> dict:
        labels = list(self._voice_label_map)
        label = self.voice_preset.get() if hasattr(self, "voice_preset") else ""
        preset_id = self._voice_label_map.get(label, "arona")
        return self.service.voice_presets.get(preset_id)

    def _refresh_voice_presets(self, select_id: str = "") -> None:
        presets = self.service.voice_presets.list()
        self._voice_label_map = {item["label"]: item["id"] for item in presets}
        self._voice_id_map = {item["id"]: item["label"] for item in presets}
        self.voice_preset["values"] = list(self._voice_label_map)
        target = select_id or str(self.config.bot.get("voice_name", "arona"))
        self.voice_preset.set(self._voice_id_map.get(target, presets[0]["label"]))

    def add_voice(self) -> None:
        def saved(preset: dict) -> None:
            result = self.service.voice_presets.upsert(preset)
            self._refresh_voice_presets(result["id"])
            self.app.toast("音色已新增。", "success")
        VoicePresetDialog(self.winfo_toplevel(), {
            "label": "自定义音色", "voice": "zh-CN-XiaoyiNeural",
            "rate": "+0%", "pitch": "+0Hz", "volume": "+0%",
        }, saved).show()

    def edit_voice(self) -> None:
        preset = self._selected_voice_preset()
        def saved(value: dict) -> None:
            result = self.service.voice_presets.upsert(value)
            self._refresh_voice_presets(result["id"])
            self.app.toast("音色已保存。", "success")
        VoicePresetDialog(self.winfo_toplevel(), preset, saved).show()

    def delete_voice(self) -> None:
        preset = self._selected_voice_preset()
        if preset.get("builtin"):
            self.app.toast("阿洛娜和普拉娜是内置音色，不能删除。", "warn")
            return
        if not confirm(self.winfo_toplevel(), "删除音色",
                       f"删除自定义音色「{preset.get('label')}」？",
                       ok_text="删除", danger=True, icon=self.icon):
            return
        if self.service.voice_presets.delete(preset["id"]):
            self._refresh_voice_presets("arona")
            self.app.toast("音色已删除。", "success")

    def preview_voice(self) -> None:
        preset = self._selected_voice_preset()
        self.app.toast("正在合成试听语音…", "info")
        def worker() -> None:
            try:
                from wecvoice_auto import VoiceSender
                sender = VoiceSender()
                result = sender.send(
                    "你好", preview=True,
                    voice=preset.get("voice"),
                    rate=preset.get("rate"),
                    pitch=preset.get("pitch"),
                    volume=preset.get("volume"),
                )
                if result.ok:
                    self.after(0, lambda: self.app.toast("试听播放完成。", "success"))
                else:
                    reason = result.message
                    self.after(0, lambda reason=reason: self.app.toast(
                        "试听失败：" + reason, "warn"))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self.app.toast(
                    "试听失败：" + message, "error"))
        threading.Thread(target=worker, daemon=True).start()

    def save(self) -> None:
        try:
            stale = max(1, min(1440, int(self.stale_minutes.get() or 10)))
            followup_timeout = max(1, min(1440, int(self.followup_timeout.get() or 5)))
            probability = max(0, min(100, int(float(
                self.group_probability_value.get() or 30
            ))))
            voice_probability = max(0, min(100, int(float(
                self.voice_probability.get() or 50
            ))))
            voice_max_chars = max(8, min(60, int(
                self.voice_max_chars.get() or 30
            )))
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
            "followup_timeout_minutes": followup_timeout,
            "voice_enabled": self.voice_enabled.get(),
            "voice_test_chat": self.voice_test_chat.get().strip() or "test",
            "natural_voice": self.natural_voice.get(),
            "read_links": self.read_links.get(),
            "trigger_semantic_check": self.trigger_semantic.get(),
            "voice_name": self._voice_label_map.get(
                self.voice_preset.get(), "arona"
            ),
            "voice_probability": voice_probability / 100.0,
            "voice_max_chars": voice_max_chars,
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
