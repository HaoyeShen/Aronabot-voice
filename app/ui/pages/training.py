"""Local document training / retrieval page."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from ...training import TRAINING_DIR
from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import (
    Card,
    LabeledEntry,
    ProgressBar,
    RoundedButton,
    ScrollFrame,
    SectionHeading,
    ToggleRow,
    confirm,
    info,
)
from .base import BasePage


FILE_TYPES = [
    ("支持的资料", "*.txt *.md *.markdown *.json *.jsonl *.csv *.tsv *.docx *.pdf"),
    ("文本 / Markdown", "*.txt *.md *.markdown"),
    ("数据 / JSON", "*.json *.jsonl *.csv *.tsv"),
    ("Office / PDF", "*.docx *.pdf"),
    ("所有文件", "*.*"),
]


class TrainingPage(BasePage):
    key = "training"
    title = "本地训练"
    subtitle = "上传资料，让阿洛娜在回复时检索你的本地知识；不修改模型权重。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._busy = False
        self._build()
        self.on_show()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        self._scroll = scroll
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        banner = tk.Frame(wrap, bg=C["sidebar"], height=px(72))
        banner.pack(fill="x", pady=(0, px(10)))
        banner.pack_propagate(False)
        tk.Frame(banner, bg=C["cyan"], width=px(5)).pack(side="left", fill="y")
        tk.Label(banner, text="✎", bg=C["sidebar"], fg=C["cyan"],
                 font=font(23)).pack(side="left", padx=(px(16), px(10)))
        text = tk.Frame(banner, bg=C["sidebar"])
        text.pack(side="left", fill="both", expand=True, pady=px(12))
        tk.Label(text, text="让阿洛娜记住老师给的资料", bg=C["sidebar"], fg="white",
                 font=font(11, "bold"), anchor="w").pack(fill="x")
        tk.Label(text, text="资料只保存在本机；回复前会检索最相关的片段，"
                           "不会把整份文件塞进模型。",
                 bg=C["sidebar"], fg=C["nav_text"], font=font(8), anchor="w").pack(fill="x")
        tk.Label(banner, text="LOCAL / RAG", bg=C["sidebar"], fg=C["cyan"],
                 font=display_font(7, "bold")).pack(side="right", padx=px(18))

        note = tk.Frame(wrap, bg="#FFF6DA")
        note.pack(fill="x", pady=(0, px(12)))
        tk.Label(note, text="说明：Ollama 不提供权重微调接口。这里做的是本地知识训练"
                            "（文档切分 + FTS5 检索 + 提示词注入），不会改变原模型文件。",
                 bg="#FFF6DA", fg=C["yellow_deep"], font=font(8),
                 anchor="w", justify="left", wraplength=px(1080),
                 padx=px(12), pady=px(8)).pack(fill="x")

        stats_row = tk.Frame(wrap, bg=C["bg"])
        stats_row.pack(fill="x", pady=(0, px(12)))
        self.stat_values: dict[str, tk.Label] = {}
        for index, (key, label, color) in enumerate((
            ("docs", "资料文件", C["cyan_deep"]),
            ("chunks", "检索片段", C["blue"]),
            ("chars", "有效字数", C["pink_deep"]),
        )):
            card = tk.Frame(stats_row, bg=C["card"], highlightthickness=1,
                            highlightbackground=C["line"])
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else px(7), 0))
            stats_row.grid_columnconfigure(index, weight=1)
            tk.Frame(card, bg=color, height=px(3)).pack(fill="x")
            body = tk.Frame(card, bg=C["card"])
            body.pack(fill="x", padx=px(14), pady=px(10))
            value = tk.Label(body, text="0", bg=C["card"], fg=color,
                             font=display_font(18, "bold"), anchor="w")
            value.pack(fill="x")
            tk.Label(body, text=label, bg=C["card"], fg=C["muted"],
                     font=font(8), anchor="w").pack(fill="x")
            self.stat_values[key] = value

        upload = Card(wrap, padding=16, accent=C["cyan"])
        upload.pack(fill="x", pady=(0, px(12)))
        head = tk.Frame(upload.body, bg=C["card"])
        head.pack(fill="x", pady=(0, px(8)))
        SectionHeading(head, "上传训练资料", "支持 txt / md / json / jsonl / csv / docx / pdf",
                       bg=C["card"]).pack(side="left", fill="x", expand=True)
        actions = tk.Frame(head, bg=C["card"])
        actions.pack(side="right")
        self.upload_btn = RoundedButton(actions, text="上传文件", command=self.choose_files,
                                        style="primary", width=96, height=30,
                                        bg=C["card"], font_size=8)
        self.upload_btn.pack(side="left", padx=(0, px(5)))
        self.reindex_btn = RoundedButton(actions, text="重建索引", command=self.reindex,
                                         style="ghost", width=82, height=30,
                                         bg=C["card"], font_size=8)
        self.reindex_btn.pack(side="left", padx=(0, px(5)))
        self.clear_btn = RoundedButton(actions, text="清空", command=self.clear,
                                       style="danger", width=58, height=30,
                                       bg=C["card"], font_size=8)
        self.clear_btn.pack(side="left")

        body = tk.Frame(upload.body, bg=C["card"])
        body.pack(fill="x")
        tk.Label(body, text="文件会复制到 data/training/files；删除资料时同步删除副本。",
                 bg=C["card"], fg=C["muted"], font=font(8), anchor="w").pack(fill="x")
        self.progress = ProgressBar(body, height=7)
        self.progress.pack(fill="x", pady=(px(8), px(4)))
        self.status = tk.Label(body, text="等待上传资料", bg=C["card"], fg=C["muted"],
                               font=font(8), anchor="w")
        self.status.pack(fill="x")

        settings = Card(wrap, padding=16, accent=C["blue"])
        settings.pack(fill="x", pady=(0, px(12)))
        SectionHeading(settings.body, "训练设置", "应用于所有监听会话与调试台").pack(
            fill="x", pady=(0, px(8)))
        self.enabled_row = ToggleRow(
            settings.body, "启用本地知识训练",
            "关闭后仍保留资料，但回复不会检索它们。",
            value=bool(self.config.training.get("enabled", True)),
            command=self._toggle_enabled,
        )
        self.enabled_row.pack(fill="x", pady=(0, px(8)))
        grid = tk.Frame(settings.body, bg=C["card"])
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        self.top_k = LabeledEntry(grid, "每次最多片段数", str(self.config.training.get("top_k", 3)),
                                  hint="1–8", width=10)
        self.top_k.grid(row=0, column=0, sticky="ew", padx=(0, px(8)))
        self.max_chars = LabeledEntry(grid, "注入字符上限", str(self.config.training.get("max_chars", 1800)),
                                      hint="400–6000", width=10)
        self.max_chars.grid(row=0, column=1, sticky="ew", padx=(px(8), 0))
        save_row = tk.Frame(settings.body, bg=C["card"])
        save_row.pack(fill="x", pady=(px(8), 0))
        self.model_label = tk.Label(save_row, text="", bg=C["card"], fg=C["muted"],
                                    font=font(8), anchor="w")
        self.model_label.pack(side="left", fill="x", expand=True)
        RoundedButton(save_row, text="保存设置", command=self.save_settings,
                      style="secondary", width=88, height=30, bg=C["card"],
                      font_size=8).pack(side="right")

        docs = Card(wrap, padding=16, accent=C["pink"])
        docs.pack(fill="x", pady=(0, px(12)))
        doc_head = tk.Frame(docs.body, bg=C["card"])
        doc_head.pack(fill="x", pady=(0, px(8)))
        self.doc_count = tk.Label(doc_head, text="资料库", bg=C["card"], fg=C["text"],
                                  font=font(10, "bold"), anchor="w")
        self.doc_count.pack(side="left")
        RoundedButton(doc_head, text="打开资料目录", command=self.open_folder,
                      style="ghost", width=94, height=28, bg=C["card"],
                      font_size=7).pack(side="right")
        self.docs_frame = tk.Frame(docs.body, bg=C["card"])
        self.docs_frame.pack(fill="x")

        test = Card(wrap, padding=16, accent=C["green"])
        test.pack(fill="x")
        SectionHeading(test.body, "测试检索", "输入问题，查看会参考哪些片段").pack(
            fill="x", pady=(0, px(8)))
        test_row = tk.Frame(test.body, bg=C["card"])
        test_row.pack(fill="x")
        self.test_entry = LabeledEntry(test_row, "问题", "", hint="例如：阿洛娜喜欢什么",
                                       width=30)
        self.test_entry.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        self.test_btn = RoundedButton(test_row, text="测试", command=self.test_search,
                                      style="secondary", width=76, height=38,
                                      bg=C["card"], font_size=9)
        self.test_btn.pack(side="left", pady=(px(19), 0))
        self.test_output = tk.Text(test.body, height=6, bg=C["card_soft"], fg=C["text"],
                                   relief="flat", bd=0, highlightthickness=1,
                                   highlightbackground=C["line"], font=font(9),
                                   wrap="word", padx=px(9), pady=px(7))
        self.test_output.pack(fill="x", pady=(px(8), 0))
        self.test_output.insert("1.0", "上传资料后，可以在这里测试检索效果。")
        self.test_output.configure(state="disabled")

    # ------------------------------------------------------------------ state
    def _set_status(self, text: str, color: str | None = None) -> None:
        try:
            self.status.configure(text=text, fg=color or C["muted"])
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        for button in (self.upload_btn, self.reindex_btn, self.clear_btn, self.test_btn):
            try:
                button.set_enabled(not busy)
            except Exception:
                pass
        if busy:
            self._set_status(label or "处理中…", C["cyan_deep"])

    def _update_stats(self) -> None:
        stats = self.training.stats()
        self.stat_values["docs"].configure(text=str(stats["docs"]))
        self.stat_values["chunks"].configure(text=str(stats["chunks"]))
        self.stat_values["chars"].configure(text=f"{stats['chars']:,}")
        self.doc_count.configure(text=f"资料库 · {stats['docs']} 个文件 / {stats['chunks']} 个片段")

    def _model_text(self) -> str:
        profile = self.config.active_profile()
        provider = str(profile.get("provider", "ollama"))
        model = str(profile.get("model") or "未配置")
        base = str(profile.get("base_url", ""))
        local = base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        if provider == "ollama" and local:
            return f"当前模型：Ollama · {model} · 本地资料检索已启用"
        if local:
            return f"当前模型：{provider} · {model} · 本地地址"
        return f"当前模型：{provider} · {model}；这不是本地地址，命中的片段会发送到该接口。"

    def _refresh_docs(self) -> None:
        for child in self.docs_frame.winfo_children():
            child.destroy()
        docs = self.training.list_documents()
        if not docs:
            tk.Label(self.docs_frame, text="还没有资料。点击「上传文件」添加第一份资料。",
                     bg=C["card"], fg=C["muted"], font=font(9), anchor="w").pack(
                fill="x", pady=px(8))
            return
        for doc in docs:
            self._doc_row(doc)

    def _doc_row(self, doc: dict) -> None:
        row = tk.Frame(self.docs_frame, bg=C["card_soft"], highlightthickness=1,
                       highlightbackground=C["line"])
        row.pack(fill="x", pady=(0, px(6)))
        inner = tk.Frame(row, bg=C["card_soft"])
        inner.pack(fill="x", padx=px(10), pady=px(8))
        suffix = Path(str(doc.get("name", ""))).suffix.lower() or "?"
        icon = {"txt": "TXT", "md": "MD", "pdf": "PDF", "docx": "DOC",
                "json": "JSON", "jsonl": "JSONL", "csv": "CSV"}.get(
            suffix.lstrip("."), suffix.lstrip(".").upper()[:4] or "FILE")
        tk.Label(inner, text=icon, bg=C["card_blue"], fg=C["blue"], font=font(8, "bold"),
                 width=5, height=1).pack(side="left", padx=(0, px(9)))
        mid = tk.Frame(inner, bg=C["card_soft"])
        mid.pack(side="left", fill="x", expand=True)
        tk.Label(mid, text=truncate(doc.get("name", ""), 48), bg=C["card_soft"],
                 fg=C["text"], font=font(9, "bold"), anchor="w").pack(fill="x")
        created = str(doc.get("created_at", ""))[:16].replace("T", " ")
        status = "就绪" if doc.get("status") == "ready" else "索引异常"
        tk.Label(mid, text=f"{doc.get('chunk_count', 0)} 片段 · "
                           f"{doc.get('chars', 0):,} 字 · {created} · {status}",
                 bg=C["card_soft"], fg=C["muted"], font=font(8), anchor="w").pack(fill="x")
        RoundedButton(inner, text="删除", command=lambda: self.delete_document(doc),
                      style="danger", width=52, height=26, bg=C["card_soft"],
                      font_size=7).pack(side="right")

    def _load_settings(self) -> None:
        training = self.config.training
        self.top_k.set(str(training.get("top_k", 3)))
        self.max_chars.set(str(training.get("max_chars", 1800)))
        self.enabled_row.switch.set(bool(training.get("enabled", True)))
        self.model_label.configure(text=self._model_text())

    def refresh(self) -> None:
        if not self.winfo_exists():
            return
        self._update_stats()
        self._refresh_docs()
        self._load_settings()

    def on_show(self) -> None:
        self.refresh()

    # ------------------------------------------------------------------ actions
    def choose_files(self) -> None:
        if self._busy:
            return
        paths = filedialog.askopenfilenames(parent=self, title="选择训练资料",
                                            filetypes=FILE_TYPES)
        if paths:
            self._start_upload(list(paths))

    def _start_upload(self, paths: list[str]) -> None:
        self._set_busy(True, f"正在解析 {len(paths)} 个文件…")
        self.progress.set(2)

        def worker() -> None:
            try:
                result = self.training.add_files(
                    paths,
                    progress=lambda index, total, name: self.after(
                        0, lambda: self._on_progress(index, total, name)
                    ),
                )
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._finish_job("上传失败", None, message))
                return
            self.after(0, lambda: self._finish_upload(result))

        threading.Thread(target=worker, daemon=True, name="arona-training-upload").start()

    def _on_progress(self, index: int, total: int, name: str) -> None:
        if not self.winfo_exists():
            return
        pct = int(index / max(1, total) * 100)
        self.progress.set(pct)
        self._set_status(f"正在解析 {index}/{total}：{name}", C["cyan_deep"])

    def _finish_upload(self, result: dict) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        self.progress.set(0)
        added = result.get("added", [])
        duplicates = result.get("duplicates", [])
        errors = result.get("errors", [])
        parts = []
        if added:
            parts.append(f"已添加 {len(added)} 个")
        if duplicates:
            parts.append(f"重复 {len(duplicates)} 个")
        if errors:
            parts.append(f"失败 {len(errors)} 个")
        self._set_status(" · ".join(parts) or "没有新增资料", C["green"] if not errors else C["yellow_deep"])
        self.refresh()
        if errors:
            detail = "\n\n".join(f"{item.get('name')}：{item.get('error')}" for item in errors[:6])
            info(self, "部分资料未导入", detail, icon=self.icon)
        else:
            self.app.toast("训练资料已更新：" + "，".join(parts), "success")

    def reindex(self) -> None:
        if self._busy or not self.training.list_documents():
            if not self._busy:
                self.app.toast("资料库为空。", "info")
            return
        if not confirm(self, "重建索引", "重新解析所有已上传资料？已有资料不会被删除。",
                       ok_text="重建", icon=self.icon):
            return
        self._set_busy(True, "正在重建索引…")
        self.progress.set(2)

        def worker() -> None:
            try:
                result = self.training.reindex(
                    progress=lambda index, total, name: self.after(
                        0, lambda: self._on_progress(index, total, name)
                    ),
                )
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._finish_job("重建失败", None, message))
                return
            self.after(0, lambda: self._finish_reindex(result))

        threading.Thread(target=worker, daemon=True, name="arona-training-reindex").start()

    def _finish_reindex(self, result: dict) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        self.progress.set(0)
        failed = result.get("failed", [])
        self._set_status(f"重建完成：{result.get('docs', 0)} 个文件",
                         C["green"] if not failed else C["yellow_deep"])
        self.refresh()
        if failed:
            info(self, "部分资料重建失败",
                 "\n\n".join(f"{item.get('name')}：{item.get('error')}" for item in failed[:6]),
                 icon=self.icon)
        else:
            self.app.toast("资料索引已重建。", "success")

    def _finish_job(self, title: str, _result, error: str) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        self.progress.set(0)
        self._set_status(error, C["red"])
        self.refresh()
        info(self, title, error, icon=self.icon)

    def delete_document(self, doc: dict) -> None:
        if self._busy:
            return
        if not confirm(self, "删除训练资料",
                       f"删除「{doc.get('name')}」及其所有检索片段？",
                       ok_text="删除", danger=True, icon=self.icon):
            return
        if self.training.delete_document(str(doc.get("id"))):
            self._set_status("资料已删除", C["muted"])
            self.refresh()
            self.app.toast("训练资料已删除。", "success")

    def clear(self) -> None:
        if self._busy:
            return
        if not confirm(self, "清空训练资料",
                       "删除全部训练资料和本地副本？此操作不可撤销。",
                       ok_text="全部清空", danger=True, icon=self.icon):
            return
        self.training.clear()
        self._set_status("训练资料已清空", C["muted"])
        self.refresh()
        self.app.toast("训练资料已清空。", "success")

    def save_settings(self) -> None:
        try:
            top_k = max(1, min(8, int(self.top_k.get() or 3)))
            max_chars = max(400, min(6000, int(self.max_chars.get() or 1800)))
        except ValueError:
            self.app.toast("片段数和字符上限必须是数字。", "error")
            return
        self.config.data.setdefault("training", {})
        self.config.training.update({"top_k": top_k, "max_chars": max_chars})
        self.config.save()
        self.top_k.set(str(top_k))
        self.max_chars.set(str(max_chars))
        self.app.toast("训练设置已保存。", "success")

    def _toggle_enabled(self, value: bool) -> None:
        self.config.training["enabled"] = bool(value)
        self.config.save()
        self.app.toast("本地知识训练已" + ("启用。" if value else "关闭。"), "success")

    def test_search(self) -> None:
        if self._busy:
            return
        query = self.test_entry.get().strip()
        if len(query) < 2:
            self.app.toast("请输入至少 2 个字符的问题。", "error")
            return
        self.test_btn.set_busy(True, "检索中")
        self._set_status("正在检索…", C["cyan_deep"])

        def worker() -> None:
            try:
                hits = self.training.search(
                    query,
                    limit=int(self.config.training.get("top_k", 3)),
                    max_chars=int(self.config.training.get("max_chars", 1800)),
                )
                self.after(0, lambda: self._show_test_result(query, hits))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message, query=query: self._show_test_result(query, [], message))

        threading.Thread(target=worker, daemon=True, name="arona-training-test").start()

    def _show_test_result(self, query: str, hits: list[dict], error: str = "") -> None:
        if not self.winfo_exists():
            return
        self.test_btn.set_busy(False)
        self._set_status("检索完成" if not error else "检索失败",
                         C["green"] if not error else C["red"])
        lines = [f"问题：{query}", ""]
        if error:
            lines.append("检索失败：" + error)
        elif not hits:
            lines.append("没有命中资料。可以尝试更具体的关键词，或检查训练开关。")
        else:
            for index, hit in enumerate(hits, start=1):
                lines.append(f"{index}. {hit.get('name')} · 片段 {int(hit.get('seq', 0)) + 1}")
                lines.append(str(hit.get("content", ""))[:700])
                lines.append("")
        self.test_output.configure(state="normal")
        self.test_output.delete("1.0", "end")
        self.test_output.insert("1.0", "\n".join(lines).strip())
        self.test_output.configure(state="disabled")

    def open_folder(self) -> None:
        try:
            TRAINING_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(TRAINING_DIR))
        except Exception as exc:
            self.app.toast("无法打开资料目录：" + str(exc), "error")
