"""Listening whitelist management."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import (
    BaseDialog,
    Card,
    LabeledEntry,
    RoundedButton,
    ScrollFrame,
    ToggleSwitch,
    confirm,
)
from .base import BasePage


class TargetDialog(BaseDialog):
    def __init__(self, parent, page: "TargetsPage", target: dict) -> None:
        self.page = page
        self.target = target
        super().__init__(parent, "编辑测试对象", "只有白名单中的会话会被回复", width=560,
                         height=670, icon=page.icon)
        body = tk.Frame(self.body, bg=C["card"])
        body.pack(fill="both", expand=True, padx=px(24), pady=px(20))
        self.name = LabeledEntry(body, "微信显示名称", target.get("name", ""),
                                 hint="需与聊天窗口标题一致", width=30)
        self.name.pack(fill="x", pady=(0, px(10)))
        row = tk.Frame(body, bg=C["card"])
        row.pack(fill="x", pady=(0, px(10)))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)
        type_frame = tk.Frame(row, bg=C["card"])
        type_frame.grid(row=0, column=0, sticky="ew", padx=(0, px(6)))
        tk.Label(type_frame, text="会话类型", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(4)))
        self.kind = ttk.Combobox(type_frame, values=["好友", "群聊"], state="readonly",
                                 style="Arona.TCombobox", font=font(9))
        self.kind.pack(fill="x")
        self.kind.set("群聊" if target.get("type") == "group" else "好友")
        persona_frame = tk.Frame(row, bg=C["card"])
        persona_frame.grid(row=0, column=1, sticky="ew", padx=(px(6), 0))
        tk.Label(persona_frame, text="使用人格", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(4)))
        self.persona = ttk.Combobox(persona_frame, values=[p.get("name", "") for p in
                                                           page.personas.list()],
                                    state="readonly", style="Arona.TCombobox", font=font(9))
        self.persona.pack(fill="x")
        persona_names = [p.get("name", "") for p in page.personas.list()]
        current_persona = next((p for p in page.personas.list()
                                if p.get("id") == target.get("persona_id")), None)
        self.persona.set(current_persona.get("name", "阿洛娜") if current_persona else
                         (persona_names[0] if persona_names else "阿洛娜"))
        ctx_frame = tk.Frame(body, bg=C["card"])
        ctx_frame.pack(fill="x", pady=(0, px(10)))
        self.context = LabeledEntry(ctx_frame, "上下文条数（至少 20）",
                                    str(target.get("context_limit", 30)),
                                    hint="20–100", width=12)
        self.context.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        prob_frame = tk.Frame(body, bg=C["card"])
        prob_frame.pack(fill="x", pady=(0, px(10)))
        self.probability = LabeledEntry(prob_frame, "私聊回复概率（0–1）",
                                        str(target.get("reply_probability", 1.0)),
                                        hint="仅好友生效", width=10)
        self.probability.pack(side="left", fill="x", expand=True, padx=(0, px(6)))
        self.group_probability = LabeledEntry(
            prob_frame, "群聊未 @ 概率（0–1）",
            str(target.get("group_reply_probability",
                           page.config.bot.get("group_reply_probability", 0.3))),
            hint="仅群聊生效", width=10,
        )
        self.group_probability.pack(side="left", fill="x", expand=True)
        news_frame = tk.Frame(body, bg=C["card"])
        news_frame.pack(fill="x", pady=(px(2), px(8)))
        self.news_enabled = tk.BooleanVar(value=bool(target.get("news_enabled", False)))
        tk.Checkbutton(
            news_frame, text="每日基沃托斯新闻", variable=self.news_enabled,
            bg=C["card"], fg=C["text"], activebackground=C["card"],
            activeforeground=C["text"], selectcolor=C["card_soft"],
            font=font(9, "bold"), anchor="w", cursor="hand2",
        ).pack(side="left")
        self.news_time = LabeledEntry(
            news_frame, "发送时间", str(target.get("news_time", "06:50")),
            hint="HH:MM", width=8,
        )
        self.news_time.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        self.note = LabeledEntry(body, "备注（可选）", target.get("note", ""), width=30)
        self.note.pack(fill="x", pady=(px(4), 0))
        self.error = tk.Label(body, text="", bg=C["card"], fg=C["red"], font=font(8),
                              anchor="w")
        self.error.pack(fill="x", pady=(px(6), 0))
        self.add_footer("取消", self.close, "secondary", 90)
        self.add_footer("保存", self._save, "primary", 100)

    def _save(self) -> None:
        name = self.name.get()
        if not name:
            self.error.configure(text="请填写微信显示名称。")
            return
        try:
            context = max(20, min(100, int(self.context.get() or 30)))
            probability = max(0.0, min(1.0, float(self.probability.get() or 1.0)))
            group_probability = max(0.0, min(1.0, float(
                self.group_probability.get() or self.page.config.bot.get("group_reply_probability", 0.3)
            )))
            news_time = self.news_time.get().strip() or "06:50"
            datetime.strptime(news_time, "%H:%M")
            news_enabled = bool(self.news_enabled.get())
        except ValueError:
            self.error.configure(text="上下文条数和回复概率必须是数字。")
            return
        persona_name = self.persona.get()
        persona_id = next((p.get("id") for p in self.page.personas.list()
                           if p.get("name") == persona_name), "arona")
        try:
            self.page.config.update_target(self.target["id"], {
                "name": name,
                "type": "group" if self.kind.get() == "群聊" else "friend",
                "persona_id": persona_id,
                "context_limit": context,
                "reply_probability": probability,
                "group_reply_probability": group_probability,
                "news_enabled": news_enabled,
                "news_time": news_time,
                "require_at": bool(self.target.get("require_at", True)),
                "note": self.note.get(),
            })
            self.close()
            self.page.refresh()
            self.page.app.reload_listening()
            self.page.app.toast("测试对象已更新。", "success")
        except Exception as exc:
            self.error.configure(text=str(exc))


class TargetRow(tk.Frame):
    def __init__(self, parent, page: "TargetsPage", target: dict) -> None:
        self.target = target
        self.page = page
        bg = C["card"]
        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=C["line"])
        inner = tk.Frame(self, bg=bg)
        inner.pack(fill="x", padx=px(14), pady=px(11))
        self.switch = ToggleSwitch(inner, value=bool(target.get("enabled", True)),
                                   command=lambda value: page.toggle(target, value))
        self.switch.pack(side="left", padx=(0, px(10)), pady=(px(5), 0))
        avatar = tk.Label(inner, text="群" if target.get("type") == "group" else "友",
                          bg=C["cyan_pale"] if target.get("type") == "group" else C["card_blue"],
                          fg=C["cyan_deep"] if target.get("type") == "group" else C["blue"],
                          font=font(9, "bold"), width=3, height=1)
        avatar.pack(side="left", padx=(0, px(10)), pady=(px(1), 0))
        mid = tk.Frame(inner, bg=bg)
        mid.pack(side="left", fill="x", expand=True)
        name_row = tk.Frame(mid, bg=bg)
        name_row.pack(fill="x")
        tk.Label(name_row, text=truncate(target.get("name", ""), 28), bg=bg, fg=C["text"],
                 font=font(10, "bold")).pack(side="left")
        tk.Label(name_row, text="群聊" if target.get("type") == "group" else "好友",
                 bg=C["card_soft"], fg=C["muted"], font=font(7), padx=px(5)).pack(
            side="left", padx=(px(7), 0))
        if target.get("type") == "group":
            tk.Label(name_row, text="群聊概率", bg=C["pink_pale"], fg=C["pink_deep"],
                     font=font(7), padx=px(5)).pack(side="left", padx=(px(5), 0))
        persona = page.personas.get(target.get("persona_id"))
        context_count = page.memory.context_count(target.get("name", ""))
        probability_note = ""
        if target.get("type") == "group":
            probability_note = f"  ·  未@概率 {round(float(target.get('group_reply_probability', 0.3)) * 100)}%"
        news_note = ""
        if target.get("news_enabled"):
            news_note = f"  ·  新闻 {target.get('news_time', '06:50')}"
        note = (f"人格：{persona.get('name', '阿洛娜')}  ·  已存 {context_count} 条上下文"
                f"  ·  上下文窗口 {target.get('context_limit', 30)}{probability_note}{news_note}")
        if target.get("note"):
            note += "  ·  " + truncate(target.get("note"), 26)
        tk.Label(mid, text=note, bg=bg, fg=C["muted"], font=font(8), anchor="w").pack(fill="x")
        actions = tk.Frame(inner, bg=bg)
        actions.pack(side="right")
        if target.get("type") == "group":
            RoundedButton(
                actions, text="新闻开" if target.get("news_enabled") else "新闻关",
                command=lambda: page.toggle_news(target),
                style="ghost", width=52, height=28, bg=bg, font_size=7,
            ).pack(side="left")
        RoundedButton(actions, text="同步上下文", command=lambda: page.sync(target),
                      style="ghost", width=82, height=28, bg=bg, font_size=7).pack(side="left")
        if target.get("type") == "group":
            RoundedButton(actions, text="清空上下文", command=lambda: page.clear_context(target),
                          style="ghost", width=82, height=28, bg=bg, font_size=7).pack(
                side="left", padx=(px(3), 0)
            )
        RoundedButton(actions, text="编辑", command=lambda: page.edit(target),
                      style="ghost", width=48, height=28, bg=bg, font_size=7).pack(side="left")
        RoundedButton(actions, text="删除", command=lambda: page.delete(target),
                      style="ghost", width=48, height=28, bg=bg, font_size=7).pack(side="left")


class TargetsPage(BasePage):
    key = "targets"
    title = "测试对象"
    subtitle = "只把消息交给阿洛娜，也只回复老师允许的会话；新闻发送也在这里按群设置。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._persona_ids: list[str] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        banner = tk.Frame(wrap, bg=C["sidebar"], height=px(70))
        banner.pack(fill="x", pady=(0, px(12)))
        banner.pack_propagate(False)
        tk.Frame(banner, bg=C["cyan"], width=px(5)).pack(side="left", fill="y")
        tk.Label(banner, text="◇", bg=C["sidebar"], fg=C["cyan"], font=font(22)).pack(
            side="left", padx=(px(16), px(10)))
        text = tk.Frame(banner, bg=C["sidebar"])
        text.pack(side="left", fill="both", expand=True, pady=px(12))
        tk.Label(text, text="测试对象之外，阿洛娜不会回复任何人", bg=C["sidebar"], fg="white",
                 font=font(10, "bold"), anchor="w").pack(fill="x")
        tk.Label(text, text="名称必须和微信聊天窗口标题完全一致；修改后会热更新监听，不会打断回复线程。",
                 bg=C["sidebar"], fg=C["nav_text"], font=font(8), anchor="w").pack(fill="x")
        tk.Label(banner, text="SAFE / WHITELIST", bg=C["sidebar"], fg=C["cyan"],
                 font=display_font(7, "bold")).pack(side="right", padx=px(18))

        add_card = Card(wrap, padding=16, accent=C["cyan"])
        add_card.pack(fill="x", pady=(0, px(14)))
        tk.Label(add_card.body, text="添加测试对象", bg=C["card"], fg=C["text"],
                 font=font(10, "bold"), anchor="w").pack(fill="x", pady=(0, px(8)))
        row = tk.Frame(add_card.body, bg=C["card"])
        row.pack(fill="x")
        self.name_entry = LabeledEntry(row, "微信显示名称", "", hint="备注名 / 群名", width=30)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        type_frame = tk.Frame(row, bg=C["card"])
        type_frame.pack(side="left", padx=(0, px(8)))
        tk.Label(type_frame, text="类型", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(5)))
        self.type_combo = ttk.Combobox(type_frame, values=["好友", "群聊"], state="readonly",
                                       width=8, style="Arona.TCombobox", font=font(9))
        self.type_combo.pack()
        self.type_combo.set("好友")
        persona_frame = tk.Frame(row, bg=C["card"])
        persona_frame.pack(side="left", padx=(0, px(8)))
        tk.Label(persona_frame, text="人格", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(5)))
        self.persona_combo = ttk.Combobox(persona_frame, state="readonly", width=14,
                                          style="Arona.TCombobox", font=font(9))
        self.persona_combo.pack()
        self.add_btn = RoundedButton(row, text="添加", command=self.add, style="primary",
                                     width=84, height=38, bg=C["card"], font_size=9)
        self.add_btn.pack(side="left", pady=(px(19), 0))
        self.add_error = tk.Label(add_card.body, text="", bg=C["card"], fg=C["red"],
                                  font=font(8), anchor="w")
        self.add_error.pack(fill="x", pady=(px(6), 0))
        hint = tk.Label(add_card.body,
                        text="安全提示：优先用「文件传输助手」测试；添加前请确认该会话属于你本人可操作的范围。",
                        bg=C["card"], fg=C["muted"], font=font(8), anchor="w")
        hint.pack(fill="x", pady=(px(4), 0))

        header = tk.Frame(wrap, bg=C["bg"])
        header.pack(fill="x", pady=(0, px(7)))
        self.count_label = tk.Label(header, text="已监听 0 个对象", bg=C["bg"],
                                     fg=C["text"], font=font(10, "bold"))
        self.count_label.pack(side="left")
        tk.Label(header, text="LAST UPDATED / CONFIG.JSON", bg=C["bg"], fg=C["muted"],
                 font=display_font(6, "normal")).pack(side="right", pady=(px(3), 0))
        self.list_frame = tk.Frame(wrap, bg=C["bg"])
        self.list_frame.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()

    def _refresh_personas(self) -> None:
        people = self.personas.list()
        self._persona_ids = [p["id"] for p in people]
        names = [p.get("name", "未命名") for p in people]
        self.persona_combo.configure(values=names)
        active = self.config.data.get("active_persona_id")
        index = self._persona_ids.index(active) if active in self._persona_ids else 0
        if self.persona_combo.current() < 0:
            self.persona_combo.current(index)

    def refresh(self) -> None:
        self._refresh_personas()
        for child in self.list_frame.winfo_children():
            child.destroy()
        targets = self.config.targets()
        enabled = sum(1 for target in targets if target.get("enabled"))
        self.count_label.configure(text=f"已监听 {enabled} 个对象  ·  共 {len(targets)} 条白名单")
        if not targets:
            empty = Card(self.list_frame, padding=30)
            empty.pack(fill="x")
            tk.Label(empty.body, text="◎", bg=C["card"], fg=C["cyan"], font=font(30)).pack()
            tk.Label(empty.body, text="还没有测试对象", bg=C["card"], fg=C["text"],
                     font=font(12, "bold")).pack(pady=(px(4), px(2)))
            tk.Label(empty.body, text="在上方输入微信窗口显示的完整名称，或先用「文件传输助手」做本地测试。\n"
                                       "未加入白名单的会话永远不会收到自动回复。",
                     bg=C["card"], fg=C["muted"], font=font(8), justify="center").pack()
            return
        for target in targets:
            row = TargetRow(self.list_frame, self, target)
            row.pack(fill="x", pady=px(4))

    # ------------------------------------------------------------------
    def add(self) -> None:
        name = self.name_entry.get()
        if not name:
            self.add_error.configure(text="请填写微信显示名称。")
            return
        persona_name = self.persona_combo.get()
        persona_id = next((p["id"] for p in self.personas.list()
                           if p.get("name") == persona_name), "arona")
        try:
            self.config.add_target(
                name, "group" if self.type_combo.get() == "群聊" else "friend",
                persona_id=persona_id, require_at=True,
            )
            self.name_entry.set("")
            self.add_error.configure(text="")
            self.refresh()
            self.app.reload_listening()
            self.app.toast(f"已添加测试对象：{name}", "success")
        except Exception as exc:
            self.add_error.configure(text=str(exc))

    def edit(self, target: dict) -> None:
        TargetDialog(self.winfo_toplevel(), self, target).show()

    def toggle(self, target: dict, value: bool) -> None:
        try:
            self.config.update_target(target["id"], {"enabled": value})
            self.app.reload_listening()
            self.app.toast(("已启用监听：" if value else "已暂停监听：") + target.get("name", ""),
                           "info")
        except Exception as exc:
            self.app.toast("更新测试对象失败：" + str(exc), "error")
        self.refresh()

    def delete(self, target: dict) -> None:
        if confirm(self.winfo_toplevel(), "移除测试对象",
                   f"确定不再监听「{target.get('name', '')}」吗？\n已保存的上下文不会自动删除。",
                   ok_text="移除", danger=True, icon=self.icon):
            self.config.delete_target(target["id"])
            self.refresh()
            self.app.reload_listening()
            self.app.toast("测试对象已移除。", "info")

    def sync(self, target: dict) -> None:
        if not self.service.running:
            self.app.toast("请先启动机器人，再同步微信上下文。", "warn")
            return
        self.app.toast("正在从微信读取最近消息…", "info")

        def worker():
            try:
                count = self.service.sync_context(target["name"], target.get("context_limit", 30))
                self.after(0, lambda: self._synced(target, count))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self.app.toast("同步失败：" + message, "error"))

        threading.Thread(target=worker, daemon=True).start()

    def toggle_news(self, target: dict) -> None:
        try:
            self.config.update_target(target["id"], {
                "news_enabled": not bool(target.get("news_enabled")),
            })
        except Exception as exc:
            self.app.toast("切换新闻失败：" + str(exc), "error")
            return
        state = "开启" if self.config.target_by_name(target["name"]).get("news_enabled") else "关闭"
        self.app.toast(f"「{target['name']}」每日新闻已{state}。", "success")
        self.refresh()

    def clear_context(self, target: dict) -> None:
        if not confirm(self.winfo_toplevel(), "清空群聊上下文",
                       f"确定清空「{target.get('name')}」的全部上下文记忆吗？\n"
                       "只影响这个群，不会删除其他会话。",
                       ok_text="清空", danger=True, icon=self.app.icon):
            return
        count = self.memory.clear(target.get("name", ""))
        self.app.toast(f"已清空「{target.get('name')}」的 {count} 条上下文。", "success")
        self.refresh()

    def _synced(self, target: dict, count: int) -> None:
        self.app.toast(f"已同步「{target['name']}」的 {count} 条上下文。", "success")
        self.refresh()
