"""Sticker library management."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import (
    BaseDialog,
    Card,
    ConfirmDialog,
    LabeledEntry,
    LabeledText,
    RoundedButton,
    ScrollFrame,
    SearchBox,
    ToggleSwitch,
    confirm,
)
from .base import BasePage

EMOTIONS = ["开心", "害羞", "思考", "难过", "生气", "困倦", "疑惑", "惊讶", "温柔", "其他"]


class StickerDialog(BaseDialog):
    def __init__(self, parent, page: "StickersPage", sticker: dict | None = None) -> None:
        self.page = page
        self.sticker = sticker
        self.source_file: str = ""
        super().__init__(parent, "编辑表情包" if sticker else "导入表情包",
                         "表情包会由阿洛娜根据语义自动选择", width=650, height=660,
                         icon=page.icon)
        self._error = tk.Label(self.body, text="", bg=C["card"], fg=C["red"], font=font(8))
        self._error.pack(fill="x", padx=px(22), pady=(px(8), 0))
        body = ScrollFrame(self.body, bg=C["card"])
        body.pack(fill="both", expand=True, padx=px(20), pady=(px(4), px(8)))
        form = tk.Frame(body.inner, bg=C["card"])
        form.pack(fill="both", expand=True, padx=px(4), pady=px(6))
        form.grid_columnconfigure(0, weight=0)
        form.grid_columnconfigure(1, weight=1)

        preview_card = tk.Frame(form, bg=C["card_soft"], width=px(210), height=px(250),
                                 highlightthickness=1, highlightbackground=C["line"])
        preview_card.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=(0, px(18)),
                          pady=(0, px(10)))
        preview_card.grid_propagate(False)
        self.preview = tk.Label(preview_card, bg=C["card_soft"], text="选择一张图片\nPNG / GIF / JPG",
                                fg=C["muted"], font=font(9), justify="center")
        self.preview.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        self.replace_btn = RoundedButton(preview_card, text="选择图片…", command=self._choose_file,
                                         style="secondary", width=150, height=32,
                                         bg=C["card_soft"], font_size=8)
        self.replace_btn.pack(pady=(0, px(10)))
        self.path_label = tk.Label(form, text="", bg=C["card"], fg=C["muted"], font=font(7),
                                   wraplength=px(360), anchor="w")
        self.path_label.grid(row=4, column=1, sticky="ew", pady=(px(4), px(8)))

        self.name = LabeledEntry(form, "名称（AI 通过此名称选择）", (sticker or {}).get("name", ""),
                                 hint="必填 · 唯一", width=30)
        self.name.grid(row=0, column=1, sticky="ew", pady=(0, px(8)))
        emotion_row = tk.Frame(form, bg=C["card"])
        emotion_row.grid(row=1, column=1, sticky="ew", pady=(0, px(8)))
        tk.Label(emotion_row, text="情绪", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(4)))
        self.emotion = ttk.Combobox(emotion_row, values=EMOTIONS, state="readonly",
                                    style="Arona.TCombobox", font=font(9))
        self.emotion.pack(fill="x")
        current_emotion = (sticker or {}).get("emotion", "开心")
        self.emotion.set(current_emotion if current_emotion in EMOTIONS else EMOTIONS[0])
        self.tags = LabeledEntry(form, "标签", "，".join((sticker or {}).get("tags", [])),
                                 hint="用逗号分隔", width=30)
        self.tags.grid(row=2, column=1, sticky="ew", pady=(0, px(8)))
        self.description = LabeledText(form, "画面 / 适用场景描述",
                                        (sticker or {}).get("description", ""), height=4,
                                        hint="帮助 AI 理解何时使用")
        self.description.grid(row=3, column=1, sticky="ew", pady=(0, px(4)))
        enabled_row = tk.Frame(form, bg=C["card"])
        enabled_row.grid(row=5, column=1, sticky="ew", pady=(px(4), px(8)))
        tk.Label(enabled_row, text="允许阿洛娜使用", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(side="left")
        self.enabled = ToggleSwitch(enabled_row, value=(sticker or {}).get("enabled", True))
        self.enabled.pack(side="right")

        self.add_footer("取消", self.close, "secondary", 90)
        self.add_footer("保存", self._save, "primary", 100)
        if sticker and sticker.get("file"):
            self.source_file = str(page.stickers.path_for(sticker))
            self._load_preview(self.source_file)
            self.path_label.configure(text=str(sticker.get("file", "")))
        elif (sticker or {}).get("file"):
            self._load_preview(str(page.stickers.path_for(sticker)))

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择表情包图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.source_file = path
        self.path_label.configure(text=Path(path).name)
        if not self.name.get():
            self.name.set(Path(path).stem)
        self._load_preview(path)

    def _load_preview(self, path: str) -> None:
        photo = self.page.bank.photo(path, (px(190), px(200)), fit="contain")
        if photo:
            self.preview.configure(image=photo, text="")
            self.preview.image = photo
        else:
            self.preview.configure(text="无法预览此图片", image="")

    def _save(self) -> None:
        self._error.configure(text="")
        name = self.name.get()
        if not name:
            self._error.configure(text="请填写表情包名称。")
            return
        tags = [part.strip() for part in self.tags.get().replace("，", ",").split(",")
                if part.strip()]
        try:
            if self.sticker is None:
                if not self.source_file:
                    self._error.configure(text="请选择一张表情包图片。")
                    return
                self.page.stickers.add_from_file(
                    self.source_file, name, self.emotion.get(), tags,
                    self.description.get(),
                )
            else:
                new_file = None
                old_path = self.page.stickers.path_for(self.sticker)
                if self.source_file and Path(self.source_file).resolve() != old_path.resolve():
                    new_file = self.source_file
                self.page.stickers.update(
                    self.sticker["id"],
                    {"name": name, "emotion": self.emotion.get(), "tags": tags,
                     "description": self.description.get(), "enabled": self.enabled.get()},
                    new_file=new_file,
                )
            self.close()
            self.page.refresh()
            self.page.app.toast("表情包已保存。", "success")
        except Exception as exc:
            self._error.configure(text=str(exc))


class StickerCard(tk.Frame):
    def __init__(self, parent, page: "StickersPage", item: dict, index: int) -> None:
        self.item = item
        self.page = page
        bg = C["card"]
        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=C["line"],
                         bd=0, width=px(208), height=px(328))
        self.pack_propagate(False)
        self.grid_propagate(False)
        inner = tk.Frame(self, bg=bg)
        inner.pack(fill="both", expand=True, padx=px(11), pady=px(11))
        image_holder = tk.Frame(inner, bg=C["card_soft"], height=px(152),
                                highlightthickness=0)
        image_holder.pack(fill="x")
        image_holder.pack_propagate(False)
        photo = page.bank.photo(page.stickers.path_for(item), (px(148), px(142)),
                                fit="contain")
        if photo:
            label = tk.Label(image_holder, image=photo, bg=C["card_soft"], bd=0,
                             cursor="hand2")
            label.image = photo
            label.pack(expand=True)
            label.bind("<Double-Button-1>", lambda _e: page.edit(item))
        else:
            tk.Label(image_holder, text="文件缺失", bg=C["card_soft"], fg=C["red"],
                     font=font(9)).pack(expand=True)
        top = tk.Frame(inner, bg=bg)
        top.pack(fill="x", pady=(px(8), 0))
        ToggleSwitch(top, value=bool(item.get("enabled", True)),
                     command=lambda value: page.toggle(item, value)).pack(side="left")
        emotion = str(item.get("emotion", "") or "未标注")
        tk.Label(top, text=emotion, bg=C["pink_pale"], fg=C["pink_deep"], font=font(7, "bold"),
                 padx=px(6), pady=px(2)).pack(side="right")
        tk.Label(inner, text=truncate(item.get("name", "未命名"), 16), bg=bg, fg=C["text"],
                 font=font(10, "bold"), anchor="w").pack(fill="x", pady=(px(5), 0))
        tags = " · ".join(str(t) for t in item.get("tags", [])[:3]) or "无标签"
        tk.Label(inner, text=truncate(tags, 18), bg=bg, fg=C["muted"], font=font(7),
                 anchor="w").pack(fill="x", pady=(px(2), 0))
        tk.Label(inner, text=truncate(item.get("description", ""), 25), bg=bg,
                 fg=C["text_soft"], font=font(7), anchor="w",
                 wraplength=px(178), justify="left").pack(fill="x", pady=(px(3), 0))
        actions = tk.Frame(inner, bg=bg)
        actions.pack(fill="x", side="bottom", pady=(px(6), 0))
        RoundedButton(actions, text="编辑", command=lambda: page.edit(item),
                      style="ghost", width=55, height=26, bg=bg,
                      font_size=8).pack(side="left")
        RoundedButton(actions, text="删除", command=lambda: page.delete(item),
                      style="ghost", width=55, height=26, bg=bg,
                      font_size=8).pack(side="right")


class StickersPage(BasePage):
    key = "stickers"
    title = "表情包"
    subtitle = "让阿洛娜在合适的情绪里，用一张图把话说完。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._query = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        toolbar = tk.Frame(wrap, bg=C["bg"])
        toolbar.pack(fill="x", pady=(0, px(12)))
        self.summary = tk.Label(toolbar, text="", bg=C["bg"], fg=C["cyan_deep"],
                                font=display_font(7, "bold"))
        self.summary.pack(side="left", pady=(px(8), 0))
        RoundedButton(toolbar, text="＋ 导入表情包", command=self.add,
                      style="primary", width=132, height=34, bg=C["bg"],
                      font_size=9).pack(side="right")
        RoundedButton(toolbar, text="重新扫描", command=self.scan,
                      style="secondary", width=92, height=34, bg=C["bg"],
                      font_size=8).pack(side="right", padx=(0, px(8)))
        self.search = SearchBox(toolbar, placeholder="搜索名称、标签或情绪…", width=24,
                                command=self._search)
        self.search.pack(side="right", padx=(0, px(10)))
        tk.Label(toolbar, text="AI 只能使用启用且存在的图片", bg=C["bg"],
                 fg=C["muted"], font=font(8)).pack(side="left", padx=(px(12), 0), pady=(px(8), 0))

        info = tk.Frame(wrap, bg=C["cyan_pale"], height=px(47))
        info.pack(fill="x", pady=(0, px(12)))
        info.pack_propagate(False)
        tk.Label(info, text="✦", bg=C["cyan_pale"], fg=C["cyan_deep"],
                 font=font(14)).pack(side="left", padx=(px(14), px(6)))
        tk.Label(info,
                 text="阿洛娜会读取每张表情包的名称、情绪、标签和描述，结合上下文判断是否发送；你也可以随时替换图片。",
                 bg=C["cyan_pale"], fg=C["text_soft"], font=font(8)).pack(side="left")

        self.grid_frame = tk.Frame(wrap, bg=C["bg"])
        self.grid_frame.pack(fill="both", expand=True)
        for i in range(4):
            self.grid_frame.grid_columnconfigure(i, weight=1, uniform="stickers")
        self.empty = tk.Frame(wrap, bg=C["bg"])
        self.empty.pack(fill="x", pady=px(30))
        tk.Label(self.empty, text="☺", bg=C["bg"], fg=C["cyan"], font=font(30)).pack()
        tk.Label(self.empty, text="还没有可用的表情包", bg=C["bg"], fg=C["text"],
                 font=font(12, "bold")).pack(pady=(px(6), px(2)))
        tk.Label(self.empty, text="点击右上角「导入表情包」，或把图片放进 data/stickers 后重新扫描。",
                 bg=C["bg"], fg=C["muted"], font=font(8)).pack()

    def _search(self, query: str) -> None:
        self._query = query.lower()
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        items = self.stickers.list()
        enabled = sum(1 for item in items if item.get("enabled"))
        available = sum(1 for item in items if item.get("enabled") and
                        self.stickers.path_for(item).is_file())
        self.summary.configure(text=f"STICKER LIBRARY · {len(items)} 张 / {enabled} 启用 / {available} 可用")
        filtered = []
        for item in items:
            haystack = " ".join([str(item.get("name", "")), str(item.get("emotion", "")),
                                 str(item.get("description", "")),
                                 " ".join(str(t) for t in item.get("tags", []))]).lower()
            if not self._query or self._query in haystack:
                filtered.append(item)
        if not filtered:
            self.grid_frame.pack_forget()
            self.empty.pack(fill="x", pady=px(30))
            return
        self.empty.pack_forget()
        self.grid_frame.pack(fill="both", expand=True)
        for index, item in enumerate(filtered):
            card = StickerCard(self.grid_frame, self, item, index)
            card.grid(row=index // 4, column=index % 4, sticky="nsew",
                      padx=px(5), pady=px(5))
        for col in range(4):
            self.grid_frame.grid_columnconfigure(col, weight=1, uniform="stickers")
        for row in range(max(1, (len(filtered) + 3) // 4)):
            self.grid_frame.grid_rowconfigure(row, weight=0)

    def add(self) -> None:
        StickerDialog(self.winfo_toplevel(), self).show()
        self.refresh()

    def edit(self, item: dict) -> None:
        StickerDialog(self.winfo_toplevel(), self, item).show()
        self.refresh()

    def toggle(self, item: dict, value: bool) -> None:
        try:
            self.stickers.update(item["id"], {"enabled": value})
            self.app.service.emit("stickers_changed", {})
            self.app.toast(("已启用 " if value else "已停用 ") + item.get("name", ""), "info")
        except Exception as exc:
            self.app.toast("更新失败：" + str(exc), "error")
        self.refresh()

    def delete(self, item: dict) -> None:
        if confirm(self.winfo_toplevel(), "删除表情包",
                   f"确定要删除「{item.get('name', '')}」吗？\n图片文件也会被移除，此操作不可撤销。",
                   ok_text="删除", danger=True, icon=self.icon):
            self.stickers.delete(item["id"])
            self.app.toast("表情包已删除。", "info")
            self.refresh()

    def scan(self) -> None:
        try:
            added = self.stickers.scan_folder()
            self.refresh()
            self.app.toast(f"扫描完成，新增 {added} 张表情包。" if added else "没有发现新的图片。",
                           "success" if added else "info")
        except Exception as exc:
            self.app.toast("扫描失败：" + str(exc), "error")
