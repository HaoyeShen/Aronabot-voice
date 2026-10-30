"""Local chat debugger and one-click robot diagnostics."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from ...prompting import is_serious
from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import (
    Card,
    ProgressBar,
    RoundedButton,
    ScrollFrame,
    SectionHeading,
    ToggleRow,
)
from .base import BasePage


class ChatView(tk.Frame):
    def __init__(self, parent, page: "DebugPage") -> None:
        super().__init__(parent, bg=C["card"])
        self.page = page
        self.scroll = ScrollFrame(self, bg=C["card"])
        self.scroll.pack(fill="both", expand=True)
        self.messages = tk.Frame(self.scroll.inner, bg=C["card"])
        self.messages.pack(fill="both", expand=True, padx=px(14), pady=px(10))
        self.typing_frame: tk.Frame | None = None
        self.empty_frame: tk.Frame | None = None
        self._loaded = False

    def load_history(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        rows = self.page.memory.recent("调试台", 30)
        for item in rows:
            sticker_path = ""
            if item.get("sticker_name"):
                sticker = self.page.stickers.find_by_name(item["sticker_name"])
                if sticker:
                    sticker_path = str(self.page.stickers.path_for(sticker))
            self.add_message(
                item.get("role", "assistant"), item.get("content", ""),
                sticker_path=sticker_path, sticker_name=item.get("sticker_name", ""),
                time_label=str(item.get("created_iso", ""))[11:16],
                animate=False,
            )
        if not rows:
            self.show_empty()

    def show_empty(self) -> None:
        if self.empty_frame is not None:
            self.empty_frame.destroy()
        empty = tk.Frame(self.messages, bg=C["card"])
        empty.pack(fill="both", expand=True, pady=px(40))
        tk.Label(empty, text="✦", bg=C["card"], fg=C["cyan"], font=font(26)).pack()
        tk.Label(empty, text="这里是只属于老师和阿洛娜的调试台。", bg=C["card"],
                 fg=C["text"], font=font(11, "bold")).pack(pady=(px(8), px(3)))
        tk.Label(empty, text="消息不会发送到微信；可以放心测试人格、上下文和表情包。",
                 bg=C["card"], fg=C["muted"], font=font(8)).pack()
        self.empty_frame = empty

    def add_message(self, role: str, text: str, *, sticker_path: str = "",
                    sticker_name: str = "", time_label: str = "",
                    animate: bool = True) -> None:
        if self.empty_frame is not None:
            self.empty_frame.destroy()
            self.empty_frame = None
        row = tk.Frame(self.messages, bg=C["card"])
        row.pack(fill="x", pady=px(4))
        if role == "user":
            holder = tk.Frame(row, bg=C["card"])
            holder.pack(side="right", anchor="e")
            meta = tk.Frame(holder, bg=C["card"])
            meta.pack(fill="x", padx=px(4))
            tk.Label(meta, text=time_label or "老师", bg=C["card"], fg=C["muted"],
                     font=font(7)).pack(side="right")
            bubble = tk.Frame(holder, bg=C["cyan_deep"], padx=px(12), pady=px(8))
            bubble.pack(anchor="e")
            tk.Label(bubble, text=text, bg=C["cyan_deep"], fg="white", font=font(9),
                     wraplength=px(430), justify="left", anchor="w").pack()
        else:
            avatar = self.page.bank.photo("assets/avatar_96.png", (px(34), px(34)),
                                           fit="contain", circular=True)
            left = tk.Frame(row, bg=C["card"])
            left.pack(side="left", anchor="w")
            head = tk.Frame(left, bg=C["card"])
            head.pack(fill="x", pady=(0, px(4)))
            if avatar:
                label = tk.Label(head, image=avatar, bg=C["card"], bd=0)
                label.image = avatar
                label.pack(side="left", padx=(0, px(6)))
            tk.Label(head, text="阿洛娜", bg=C["card"], fg=C["text"],
                     font=font(8, "bold")).pack(side="left")
            tk.Label(head, text=time_label or "", bg=C["card"], fg=C["muted"],
                     font=font(7)).pack(side="left", padx=(px(7), 0))
            bubble = tk.Frame(left, bg=C["card_blue"], padx=px(12), pady=px(8),
                              highlightthickness=0)
            bubble.pack(anchor="w", padx=(px(40), 0))
            accent = tk.Frame(bubble, bg=C["cyan"], width=px(3))
            accent.pack(side="left", fill="y", padx=(0, px(8)))
            content = tk.Frame(bubble, bg=C["card_blue"])
            content.pack(side="left", fill="both")
            if text:
                tk.Label(content, text=text, bg=C["card_blue"], fg=C["text"], font=font(9),
                         wraplength=px(470), justify="left", anchor="w").pack(anchor="w")
            if sticker_path:
                photo = self.page.bank.photo(sticker_path, (px(136), px(136)), fit="contain")
                if photo:
                    image_label = tk.Label(content, image=photo, bg=C["card_blue"], bd=0)
                    image_label.image = photo
                    image_label.pack(anchor="w", pady=(px(7), 0))
            if sticker_name:
                tk.Label(content, text="☺ " + sticker_name, bg=C["card_blue"],
                         fg=C["pink_deep"], font=font(7)).pack(anchor="w", pady=(px(4), 0))
        if animate:
            self.scroll.scroll_to_bottom()

    def show_typing(self) -> None:
        self.hide_typing()
        row = tk.Frame(self.messages, bg=C["card"])
        row.pack(fill="x", pady=px(5))
        avatar = self.page.bank.photo("assets/avatar_96.png", (px(28), px(28)),
                                       fit="contain", circular=True)
        if avatar:
            label = tk.Label(row, image=avatar, bg=C["card"], bd=0)
            label.image = avatar
            label.pack(side="left", padx=(0, px(7)))
        bubble = tk.Frame(row, bg=C["card_blue"], padx=px(12), pady=px(7))
        bubble.pack(side="left")
        tk.Label(bubble, text="阿洛娜正在输入…", bg=C["card_blue"], fg=C["muted"],
                 font=font(8)).pack()
        self.typing_frame = row
        self.scroll.scroll_to_bottom()

    def hide_typing(self) -> None:
        if self.typing_frame is not None:
            self.typing_frame.destroy()
            self.typing_frame = None

    def clear(self) -> None:
        for child in self.messages.winfo_children():
            child.destroy()
        self.typing_frame = None
        self.empty_frame = None
        self._loaded = True
        self.show_empty()


class DebugPage(BasePage):
    key = "debug"
    title = "自动调试"
    subtitle = "在不打扰微信的前提下，检查人格、上下文与表情包。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._sending = False
        self._diagnosing = False
        self._persona_ids: list[str] = []
        self._build()
        self._refresh_personas()

    def _build(self) -> None:
        root = tk.Frame(self, bg=C["bg"])
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=3)
        root.grid_columnconfigure(1, weight=1, minsize=px(310))
        root.grid_rowconfigure(0, weight=1)

        left = Card(root, padding=0, accent=C["cyan"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(8)))
        head = tk.Frame(left.body, bg=C["card"], height=px(58))
        head.pack(fill="x", padx=px(16))
        head.pack_propagate(False)
        avatar = self.bank.photo("assets/avatar_96.png", (px(38), px(38)), fit="contain",
                                  circular=True)
        if avatar:
            label = tk.Label(head, image=avatar, bg=C["card"], bd=0)
            label.image = avatar
            label.pack(side="left", pady=px(10))
        head_text = tk.Frame(head, bg=C["card"])
        head_text.pack(side="left", padx=(px(9), 0), pady=px(10))
        tk.Label(head_text, text="阿洛娜 · 本地调试台", bg=C["card"], fg=C["text"],
                 font=font(10, "bold")).pack(anchor="w")
        tk.Label(head_text, text="LOCAL SYNTHESIS · NOT SENT TO WECHAT", bg=C["card"],
                 fg=C["cyan_deep"], font=display_font(6, "bold")).pack(anchor="w")
        self.clear_btn = RoundedButton(head, text="清空", command=self._clear,
                                       style="ghost", width=58, height=28,
                                       bg=C["card"], font_size=8)
        self.clear_btn.pack(side="right", pady=px(14))
        tk.Frame(left.body, bg=C["line"], height=1).pack(fill="x")
        self.chat = ChatView(left.body, self)
        self.chat.pack(fill="both", expand=True)
        tk.Frame(left.body, bg=C["line"], height=1).pack(fill="x")
        composer = tk.Frame(left.body, bg=C["card"])
        composer.pack(fill="x", padx=px(14), pady=px(10))
        self.input = tk.Text(composer, height=2, bg=C["card_soft"], fg=C["text"],
                             insertbackground=C["cyan_deep"], relief="flat", bd=0,
                             highlightthickness=1, highlightbackground=C["line"],
                             highlightcolor=C["cyan"], font=font(10), wrap="word",
                             padx=px(9), pady=px(7))
        self.input.pack(side="left", fill="both", expand=True)
        self.send_btn = RoundedButton(composer, text="发送", command=self._send,
                                      style="primary", width=86, height=40,
                                      bg=C["card"], font_size=9)
        self.send_btn.pack(side="right", padx=(px(8), 0))
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<Shift-Return>", lambda _e: None)

        right_scroll = ScrollFrame(root, bg=C["bg"])
        right_scroll.grid(row=0, column=1, sticky="nsew", padx=(px(8), 0))
        right = tk.Frame(right_scroll.inner, bg=C["bg"])
        right.pack(fill="both", expand=True, padx=(0, px(3)))

        diag_card = Card(right, padding=16, accent=C["pink"])
        diag_card.pack(fill="x")
        tk.Label(diag_card.body, text="自动调试机器人", bg=C["card"], fg=C["text"],
                 font=font(11, "bold"), anchor="w").pack(fill="x")
        tk.Label(diag_card.body,
                 text="依次检查运行环境、配置、数据库、模型连接、微信桌面端、监听白名单，并验证上下文。",
                 bg=C["card"], fg=C["muted"], font=font(8), justify="left",
                 wraplength=px(280), anchor="w").pack(fill="x", pady=(px(3), px(8)))
        self.deep_var = tk.BooleanVar(value=False)
        deep = tk.Checkbutton(diag_card.body, text="包含 3 轮上下文对话测试（会调用模型）",
                              variable=self.deep_var, bg=C["card"], fg=C["text_soft"],
                              activebackground=C["card"], activeforeground=C["text"],
                              selectcolor=C["card"], font=font(8), anchor="w",
                              highlightthickness=0, bd=0)
        deep.pack(fill="x", pady=(0, px(8)))
        self.diag_btn = RoundedButton(diag_card.body, text="开始自动调试",
                                       command=self._run_diagnostics, style="primary",
                                       width=280, height=38, bg=C["card"], font_size=9)
        self.diag_btn.pack(fill="x")
        self.progress = ProgressBar(diag_card.body, height=7)
        self.progress.pack(fill="x", pady=(px(10), px(4)))
        self.diag_status = tk.Label(diag_card.body, text="准备就绪。",
                                     bg=C["card"], fg=C["muted"], font=font(8),
                                     anchor="w", wraplength=px(280), justify="left")
        self.diag_status.pack(fill="x")
        self.last_result = tk.Label(diag_card.body, text="", bg=C["card"],
                                     fg=C["cyan_deep"], font=font(8, "bold"), anchor="w")
        self.last_result.pack(fill="x", pady=(px(5), 0))

        persona_card = Card(right, padding=16)
        persona_card.pack(fill="x", pady=(px(12), 0))
        tk.Label(persona_card.body, text="调试参数", bg=C["card"], fg=C["text"],
                 font=font(10, "bold"), anchor="w").pack(fill="x")
        tk.Label(persona_card.body, text="使用人格", bg=C["card"], fg=C["muted"],
                 font=font(8), anchor="w").pack(fill="x", pady=(px(8), px(3)))
        self.persona_combo = ttk.Combobox(persona_card.body, state="readonly",
                                          style="Arona.TCombobox", font=font(9))
        self.persona_combo.pack(fill="x")
        self.persona_combo.bind("<<ComboboxSelected>>", lambda _e: self._persona_changed())
        self.model_label = tk.Label(persona_card.body, text="MODEL —", bg=C["card"],
                                     fg=C["cyan_deep"], font=display_font(7, "bold"),
                                     anchor="w")
        self.model_label.pack(fill="x", pady=(px(8), 0))
        self.persist_var = tk.BooleanVar(value=True)
        tk.Checkbutton(persona_card.body, text="保留调试对话作为上下文", variable=self.persist_var,
                       bg=C["card"], fg=C["text_soft"], activebackground=C["card"],
                       selectcolor=C["card"], font=font(8), anchor="w",
                       highlightthickness=0, bd=0).pack(fill="x", pady=(px(7), 0))
        self.sticker_test_btn = RoundedButton(persona_card.body, text="测试表情包选择",
                                               command=self._test_sticker, style="secondary",
                                               width=280, height=34, bg=C["card"],
                                               font_size=8)
        self.sticker_test_btn.pack(fill="x", pady=(px(8), 0))

        tips = Card(right, padding=16, bg=C["card_soft"])
        tips.pack(fill="x", pady=(px(12), px(14)))
        tk.Label(tips.body, text="DEBUG NOTES", bg=C["card_soft"], fg=C["cyan_deep"],
                 font=display_font(7, "bold"), anchor="w").pack(fill="x")
        tk.Label(tips.body,
                 text="• 这里的对话不会发送到微信\n"
                      "• 阿洛娜会读取最近 20+ 条调试上下文\n"
                      "• 想测试真实回复，请先在「测试对象」添加白名单\n"
                      "• 自动调试不会截屏或录制屏幕",
                 bg=C["card_soft"], fg=C["text_soft"], font=font(8), justify="left",
                 anchor="w", wraplength=px(275)).pack(fill="x", pady=(px(7), 0))

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.chat.load_history()
        self._refresh_personas()
        state = self.service.state()
        self.model_label.configure(text="MODEL · " + truncate(state.get("model") or "未配置", 30))
        if self.input.focus_get() is None:
            self.after(80, self.input.focus_set)

    def _refresh_personas(self) -> None:
        personas = self.personas.list()
        self._persona_ids = [p["id"] for p in personas]
        labels = [p.get("name", "未命名") for p in personas]
        self.persona_combo.configure(values=labels)
        active = self.config.data.get("active_persona_id")
        index = self._persona_ids.index(active) if active in self._persona_ids else 0
        self.persona_combo.current(index)
        state = self.service.state()
        self.model_label.configure(text="MODEL · " + truncate(state.get("model") or "未配置", 30))

    def _persona_changed(self) -> None:
        index = self.persona_combo.current()
        if 0 <= index < len(self._persona_ids):
            self.config.data["active_persona_id"] = self._persona_ids[index]
            self.config.save()
            self.app.toast("调试人格已切换为 " + self.persona_combo.get(), "info")

    def _selected_persona(self) -> str:
        index = self.persona_combo.current()
        if 0 <= index < len(self._persona_ids):
            return self._persona_ids[index]
        return self.config.data.get("active_persona_id", "arona")

    def _on_enter(self, event) -> str | None:
        if event.state & 0x0001:  # Shift
            return None
        self._send()
        return "break"

    def _send(self, preset: str | None = None) -> None:
        if self._sending:
            return
        text = (preset if preset is not None else self.input.get("1.0", "end")).strip()
        if not text:
            self.app.toast("先写一句想对阿洛娜说的话吧。", "warn")
            return
        self._sending = True
        self.send_btn.set_busy(True, "思考中")
        self.input.delete("1.0", "end")
        self.chat.add_message("user", text, time_label="现在")
        self.chat.show_typing()
        persona_id = self._selected_persona()
        persist = bool(self.persist_var.get())

        def worker():
            try:
                result = self.service.debug_chat(text, persona_id=persona_id,
                                                 persist=persist)
                self.after(0, lambda: self._show_result(result))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._show_error(message))

        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, result: dict) -> None:
        self._sending = False
        self.send_btn.set_busy(False, "发送")
        self.chat.hide_typing()
        sticker = result.get("sticker")
        self.chat.add_message(
            "assistant", result.get("text") or "（只发送了一张表情包）",
            sticker_path=result.get("sticker_path", ""),
            sticker_name=sticker.get("name", "") if sticker else "",
            time_label=f"{result.get('latency_ms', 0)} ms",
        )
        self.chat.load_history()
        if sticker:
            self.last_result.configure(text=f"☺ 选择了表情包：{sticker.get('name')}",
                                       fg=C["pink_deep"])
        else:
            self.last_result.configure(text="")
        self.input.focus_set()

    def _show_error(self, message: str) -> None:
        self._sending = False
        self.send_btn.set_busy(False, "发送")
        self.chat.hide_typing()
        self.chat.add_message("assistant", "唔……这次没有连上模型。\n" + message,
                              time_label="错误")
        self.app.toast("模型调用失败：" + truncate(message, 60), "error")

    def _clear(self) -> None:
        if self._sending:
            self.app.toast("阿洛娜还在思考，稍等一下再清空。", "warn")
            return
        self.service.clear_debug()
        self.chat.clear()
        self.last_result.configure(text="")
        self.app.toast("调试对话已清空。", "info")

    def _test_sticker(self) -> None:
        if self._sending:
            return
        self._send("请给老师发一张适合现在气氛的表情包，并说一句短句。")

    # ------------------------------------------------------------------
    def _run_diagnostics(self) -> None:
        if self._diagnosing or getattr(self.app, "diagnostics_busy", False):
            self.app.toast("已有一次自动调试正在进行。", "warn")
            return
        self._diagnosing = True
        self.app.diagnostics_busy = True
        self.diag_btn.set_busy(True, "调试中")
        self.progress.set(2)
        self.diag_status.configure(text="正在检查 ARONA 系统…", fg=C["cyan_deep"])
        self.last_result.configure(text="")
        deep = bool(self.deep_var.get())

        def progress_cb(percent: int, name: str) -> None:
            self.after(0, lambda: self._progress(percent, name))

        def worker():
            try:
                result = self.service.diagnostics(run_model=True, run_chat_test=deep,
                                                  progress=progress_cb)
                self.after(0, lambda: self._diagnostics_done(result))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._diagnostics_error(message))

        threading.Thread(target=worker, daemon=True).start()

    def _progress(self, percent: int, name: str) -> None:
        self.progress.set(percent)
        self.diag_status.configure(text=name + "…", fg=C["text_soft"])

    def _diagnostics_done(self, result: dict) -> None:
        self._diagnosing = False
        self.app.diagnostics_busy = False
        self.diag_btn.set_busy(False, "开始自动调试")
        self.progress.set(100)
        failed = result.get("failed", 0)
        warnings = result.get("warnings", 0)
        passed = result.get("passed", 0)
        if failed:
            self.diag_status.configure(text=f"发现 {failed} 个需要处理的问题。", fg=C["red"])
        elif warnings:
            self.diag_status.configure(text=f"{passed} 项通过，{warnings} 项提示。", fg=C["yellow_deep"])
        else:
            self.diag_status.configure(text=f"全部 {passed} 项检查通过。", fg=C["green"])
        self.last_result.configure(text=f"最近调试：{result.get('finished_at', '')}",
                                   fg=C["cyan_deep"])
        self.app.last_diagnostics = result
        self.app.show_diagnostics(result)

    def _diagnostics_error(self, message: str) -> None:
        self._diagnosing = False
        self.app.diagnostics_busy = False
        self.diag_btn.set_busy(False, "开始自动调试")
        self.progress.set(0)
        self.diag_status.configure(text="调试失败：" + message, fg=C["red"])
        self.app.toast("自动调试失败：" + truncate(message, 70), "error")
