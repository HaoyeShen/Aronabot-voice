"""Persona / role management."""

from __future__ import annotations

import re
import tkinter as tk
import uuid

from ...ui.theme import C, display_font, font, mix, px, truncate
from ...ui.widgets import (
    BaseDialog,
    Card,
    LabeledEntry,
    LabeledText,
    RoundedButton,
    ScrollFrame,
    confirm,
)
from .base import BasePage


def examples_to_text(examples: list[dict], name: str = "阿洛娜") -> str:
    blocks = []
    for item in examples or []:
        if not isinstance(item, dict):
            continue
        user = str(item.get("user", "")).strip()
        assistant = str(item.get("assistant", "")).strip()
        if user and assistant:
            blocks.append(f"老师：{user}\n{name}：{assistant}")
    return "\n\n".join(blocks)


def text_to_examples(text: str) -> list[dict]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    result = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        user = ""
        assistant = ""
        for line in lines:
            if re.match(r"^(老师|用户|user|User)\s*[:：]", line):
                user = re.sub(r"^(老师|用户|user|User)\s*[:：]\s*", "", line)
            elif re.match(r"^[^:：]{1,20}\s*[:：]", line) and user:
                assistant = re.sub(r"^[^:：]{1,20}\s*[:：]\s*", "", line)
        if user and assistant:
            result.append({"user": user, "assistant": assistant})
    return result


class PersonaDialog(BaseDialog):
    def __init__(self, parent, page: "PersonasPage", persona: dict | None = None) -> None:
        self.page = page
        self.persona = persona
        existing = bool(persona and persona.get("id"))
        super().__init__(parent, "编辑人格" if existing else "创建新人格",
                         "设定阿洛娜的语气、角色与说话边界", width=760, height=740,
                         icon=page.icon)
        scroll = ScrollFrame(self.body, bg=C["card"])
        scroll.pack(fill="both", expand=True, padx=px(22), pady=px(12))
        form = tk.Frame(scroll.inner, bg=C["card"])
        form.pack(fill="both", expand=True, padx=px(4), pady=px(6))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)

        self.name = LabeledEntry(form, "角色名称", (persona or {}).get("name", ""),
                                 hint="例如：阿洛娜", width=24)
        self.name.grid(row=0, column=0, sticky="ew", padx=(0, px(8)), pady=(0, px(8)))
        self.name_en = LabeledEntry(form, "英文名 / 代号", (persona or {}).get("name_en", ""),
                                    width=24)
        self.name_en.grid(row=0, column=1, sticky="ew", padx=(px(8), 0), pady=(0, px(8)))
        self.title = LabeledEntry(form, "角色标题", (persona or {}).get("title", ""),
                                  hint="一句话身份", width=40)
        self.title.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.source = LabeledEntry(form, "出处 / 作品（可选）", (persona or {}).get("source", ""),
                                   width=40)
        self.source.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.description = LabeledText(form, "角色背景", (persona or {}).get("description", ""),
                                       height=3, hint="她是谁、和老师的关系")
        self.description.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.tone = LabeledText(form, "语气与性格", (persona or {}).get("tone", ""),
                                height=2, hint="温柔 / 元气 / 毒舌…")
        self.tone.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.style = LabeledText(form, "说话风格", (persona or {}).get("speech_style", ""),
                                 height=3, hint="句子长度、称呼、口癖")
        self.style.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.system = LabeledText(
            form, "核心设定 / System Prompt", (persona or {}).get("system_prompt", ""),
            height=6, hint="最重要的行为规则", )
        self.system.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.boundaries = LabeledText(form, "行为边界（安全与底线）",
                                      (persona or {}).get("boundaries", ""), height=2)
        self.boundaries.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        catch = " / ".join((persona or {}).get("catchphrases", []))
        self.catchphrases = LabeledEntry(form, "口癖 / 常用句", catch, hint="用 / 分隔",
                                          width=40)
        self.catchphrases.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.sticker_guidance = LabeledText(
            form, "表情包使用倾向", (persona or {}).get("sticker_guidance", ""),
            height=2, hint="什么时候想用表情包")
        self.sticker_guidance.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.examples = LabeledText(
            form, "对话示例（可选）", examples_to_text((persona or {}).get("examples", []),
                                                       (persona or {}).get("name", "阿洛娜")),
            height=5, hint="每段：老师：… / 角色：…，空行分隔")
        self.examples.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        self.error = tk.Label(form, text="", bg=C["card"], fg=C["red"], font=font(8),
                              anchor="w")
        self.error.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(px(4), px(8)))
        self.add_footer("取消", self.close, "secondary", 90)
        self.add_footer("保存人格", self._save, "primary", 110)

    def _save(self) -> None:
        name = self.name.get()
        if not name:
            self.error.configure(text="请填写角色名称。")
            return
        if not self.system.get() and not self.description.get():
            self.error.configure(text="请至少填写角色背景或核心设定，否则模型不知道她是谁。")
            return
        data = dict(self.persona or {})
        data.update({
            "id": data.get("id") or "persona_" + uuid.uuid4().hex[:8],
            "name": name,
            "name_en": self.name_en.get(),
            "title": self.title.get() or "自定义角色",
            "source": self.source.get(),
            "description": self.description.get(),
            "tone": self.tone.get(),
            "speech_style": self.style.get(),
            "system_prompt": self.system.get(),
            "boundaries": self.boundaries.get(),
            "catchphrases": [x.strip() for x in re.split(r"[/／|｜]", self.catchphrases.get())
                             if x.strip()],
            "sticker_guidance": self.sticker_guidance.get(),
            "examples": text_to_examples(self.examples.get()),
            "avatar": data.get("avatar") or "assets/avatar_256.png",
            "builtin": data.get("builtin", False),
        })
        try:
            self.page.personas.upsert(data)
            self.close()
            self.page.refresh()
            self.page.app.toast("人格设定已保存。", "success")
        except Exception as exc:
            self.error.configure(text=str(exc))


class PersonaCard(tk.Frame):
    def __init__(self, parent, page: "PersonasPage", persona: dict, active: bool) -> None:
        self.persona = persona
        self.page = page
        bg = C["sidebar"] if active else C["card"]
        border = C["cyan"] if active else C["line"]
        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=border)
        inner = tk.Frame(self, bg=bg)
        inner.pack(fill="both", expand=True, padx=px(16), pady=px(14))
        top = tk.Frame(inner, bg=bg)
        top.pack(fill="x")
        avatar_path = persona.get("avatar") or "assets/avatar_256.png"
        photo = page.bank.photo(avatar_path, (px(58), px(58)), fit="contain", circular=True)
        if photo:
            image = tk.Label(top, image=photo, bg=bg, bd=0)
            image.image = photo
            image.pack(side="left")
        else:
            tk.Label(top, text="♡", bg=bg, fg=C["cyan"], font=font(25)).pack(side="left")
        text = tk.Frame(top, bg=bg)
        text.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        label_fg = "white" if active else C["text"]
        sub_fg = C["nav_text"] if active else C["muted"]
        tk.Label(text, text=persona.get("name", "未命名"), bg=bg, fg=label_fg,
                 font=font(12, "bold"), anchor="w").pack(fill="x")
        tk.Label(text, text=truncate(persona.get("title", ""), 28), bg=bg, fg=sub_fg,
                 font=font(8), anchor="w").pack(fill="x")
        if active:
            tk.Label(top, text="使用中", bg=C["cyan"], fg=C["sidebar"], font=font(7, "bold"),
                     padx=px(7), pady=px(2)).pack(side="right", anchor="n")
        desc = truncate(persona.get("description", ""), 96)
        tk.Label(inner, text=desc, bg=bg, fg=C["nav_text"] if active else C["text_soft"],
                 font=font(8), justify="left", anchor="w", wraplength=px(350)).pack(
            fill="x", pady=(px(10), px(6)))
        catch = " / ".join(persona.get("catchphrases", [])[:3])
        if catch:
            tk.Label(inner, text=truncate(catch, 52), bg=bg, fg=C["cyan"] if active else C["pink_deep"],
                     font=font(7), anchor="w").pack(fill="x", pady=(0, px(6)))
        actions = tk.Frame(inner, bg=bg)
        actions.pack(fill="x", pady=(px(4), 0))
        action_style = "on_dark" if active else "ghost"
        if not active:
            RoundedButton(actions, text="设为默认", command=lambda: page.activate(persona),
                          style="secondary", width=76, height=28, bg=bg,
                          font_size=7).pack(side="left")
        RoundedButton(actions, text="编辑", command=lambda: page.edit(persona),
                      style=action_style, width=48, height=28, bg=bg, font_size=7).pack(
            side="left", padx=(px(3), 0))
        RoundedButton(actions, text="测试", command=lambda: page.test(persona),
                      style=action_style, width=48, height=28, bg=bg, font_size=7).pack(side="left")
        if not persona.get("builtin"):
            RoundedButton(actions, text="删除", command=lambda: page.delete(persona),
                          style=action_style, width=48, height=28, bg=bg, font_size=7).pack(
                side="right")
        if persona.get("builtin"):
            RoundedButton(actions, text="重置", command=lambda: page.reset(persona),
                          style=action_style, width=48, height=28, bg=bg, font_size=7).pack(
                side="right")


class AddPersonaCard(tk.Frame):
    def __init__(self, parent, page: "PersonasPage") -> None:
        super().__init__(parent, bg=C["card_blue"], highlightthickness=1,
                         highlightbackground=C["line_strong"], cursor="hand2")
        inner = tk.Frame(self, bg=C["card_blue"])
        inner.pack(expand=True, padx=px(20), pady=px(30))
        tk.Label(inner, text="＋", bg=C["card_blue"], fg=C["cyan_deep"],
                 font=font(28, "bold")).pack()
        tk.Label(inner, text="创建新人格", bg=C["card_blue"], fg=C["text"],
                 font=font(11, "bold")).pack(pady=(px(4), px(2)))
        tk.Label(inner, text="为阿洛娜换一种语气，或写一个全新的角色。\n人格设定会直接影响每一次回复。",
                 bg=C["card_blue"], fg=C["muted"], font=font(8), justify="center").pack()
        for widget in (self, inner, *inner.winfo_children()):
            widget.bind("<Button-1>", lambda _e: page.add())


class PersonasPage(BasePage):
    key = "personas"
    title = "人格设定"
    subtitle = "不只是语气：她记得自己是谁，也知道该怎么陪老师说话。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        toolbar = tk.Frame(wrap, bg=C["bg"])
        toolbar.pack(fill="x", pady=(0, px(12)))
        tk.Label(toolbar, text="PERSONA LIBRARY", bg=C["bg"], fg=C["cyan_deep"],
                 font=display_font(7, "bold")).pack(side="left", pady=(px(8), 0))
        RoundedButton(toolbar, text="＋ 创建新人格", command=self.add, style="primary",
                      width=126, height=34, bg=C["bg"], font_size=9).pack(side="right")
        self.info = tk.Label(toolbar, text="", bg=C["bg"], fg=C["muted"], font=font(8))
        self.info.pack(side="left", padx=(px(12), 0), pady=(px(8), 0))

        self.grid_frame = tk.Frame(wrap, bg=C["bg"])
        self.grid_frame.pack(fill="both", expand=True)
        self.empty_add = tk.Frame(wrap, bg=C["bg"])

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        people = self.personas.list()
        active = self.config.data.get("active_persona_id")
        self.info.configure(text=f"{len(people)} 个角色 · 当前默认：{self.personas.get(active).get('name')}")
        for index, persona in enumerate(people):
            card = PersonaCard(self.grid_frame, self, persona, persona.get("id") == active)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=px(5), pady=px(5))
        add_index = len(people)
        add_card = AddPersonaCard(self.grid_frame, self)
        add_card.grid(row=add_index // 2, column=add_index % 2, sticky="nsew",
                      padx=px(5), pady=px(5))
        self.grid_frame.grid_columnconfigure(0, weight=1, uniform="persona")
        self.grid_frame.grid_columnconfigure(1, weight=1, uniform="persona")

    def add(self) -> None:
        seed = {
            "name": "新角色",
            "name_en": "",
            "title": "自定义角色",
            "description": "",
            "tone": "",
            "speech_style": "",
            "system_prompt": "你是老师的专属助手。请用自然、简洁的微信口吻回应老师。",
            "boundaries": "不提供违法或危险帮助；不冒充真人。",
            "catchphrases": [],
            "sticker_guidance": "在轻松、开心的对话里可以偶尔使用表情包。",
            "examples": [],
            "avatar": "assets/avatar_256.png",
            "builtin": False,
        }
        PersonaDialog(self.winfo_toplevel(), self, seed).show()
        self.refresh()

    def edit(self, persona: dict) -> None:
        PersonaDialog(self.winfo_toplevel(), self, persona).show()
        self.refresh()

    def activate(self, persona: dict) -> None:
        self.config.data["active_persona_id"] = persona["id"]
        self.config.save()
        self.refresh()
        self.app.toast(f"默认人格已切换为「{persona.get('name')}」。", "success")

    def delete(self, persona: dict) -> None:
        if len(self.personas.list()) <= 1:
            self.app.toast("至少需要保留一个人格。", "warn")
            return
        if not confirm(self.winfo_toplevel(), "删除人格",
                       f"确定删除「{persona.get('name')}」吗？\n使用该人格的测试对象会回退到默认人格。",
                       ok_text="删除", danger=True, icon=self.icon):
            return
        self.personas.delete(persona["id"])
        if self.config.data.get("active_persona_id") == persona["id"]:
            fallback = self.personas.list()[0]["id"]
            self.config.data["active_persona_id"] = fallback
        for target in self.config.targets():
            if target.get("persona_id") == persona["id"]:
                target["persona_id"] = self.config.data.get("active_persona_id", "arona")
        self.config.save()
        self.refresh()
        self.app.toast("人格已删除。", "info")

    def test(self, persona: dict) -> None:
        self.config.data["active_persona_id"] = persona["id"]
        self.config.save()
        self.app.show_page("debug")
        self.app.toast(f"已在调试台切换到「{persona.get('name')}」。", "info")

    def reset(self, persona: dict) -> None:
        name = persona.get("name", "角色")
        if not confirm(self.winfo_toplevel(), f"重置{name}",
                       f"将「{name}」恢复为内置默认设定？你后来做的编辑会被覆盖。",
                       ok_text="重置", danger=True, icon=self.icon):
            return
        self.personas.reset_builtin(persona.get("id", "arona"))
        self.refresh()
        self.app.toast(f"「{name}」已恢复为初始设定。", "success")
