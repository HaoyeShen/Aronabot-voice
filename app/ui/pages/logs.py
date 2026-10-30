"""Runtime log viewer."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ...logbus import BUS, LogItem
from ...paths import LOG_FILE, LOGS
from ...ui.theme import C, display_font, font, mono_font, px
from ...ui.widgets import RoundedButton, ToggleSwitch
from .base import BasePage


class LogsPage(BasePage):
    key = "logs"
    title = "运行日志"
    subtitle = "出问题时，先来这里看看阿洛娜留下了什么线索。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._last_seq = 0
        self._items: list[LogItem] = []
        self._auto = True
        self._after_id = None
        self._build()
        self._append_snapshot(BUS.snapshot())
        self._schedule()

    def _build(self) -> None:
        wrap = tk.Frame(self, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        toolbar = tk.Frame(wrap, bg=C["bg"])
        toolbar.pack(fill="x", pady=(0, px(10)))
        tk.Label(toolbar, text="ARONA RUNTIME LOG", bg=C["bg"], fg=C["cyan_deep"],
                 font=display_font(7, "bold")).pack(side="left", pady=(px(10), 0))
        RoundedButton(toolbar, text="打开日志文件夹", command=self._open_folder,
                      style="secondary", width=112, height=32, bg=C["bg"],
                      font_size=8).pack(side="right", padx=(px(6), 0))
        RoundedButton(toolbar, text="导出…", command=self._export, style="ghost",
                      width=64, height=32, bg=C["bg"], font_size=8).pack(side="right")
        RoundedButton(toolbar, text="清空视图", command=self._clear, style="ghost",
                      width=70, height=32, bg=C["bg"], font_size=8).pack(side="right")
        self.auto_switch = ToggleSwitch(toolbar, value=True,
                                        command=self._set_auto)
        self.auto_switch.pack(side="right", padx=(px(10), px(5)), pady=(px(6), 0))
        tk.Label(toolbar, text="自动刷新", bg=C["bg"], fg=C["muted"], font=font(8)).pack(
            side="right", pady=(px(8), 0))
        self.level = ttk.Combobox(toolbar, values=["全部", "INFO", "WARNING", "ERROR", "DEBUG"],
                                  state="readonly", width=9, style="Arona.TCombobox",
                                  font=font(8))
        self.level.pack(side="right", padx=(px(5), px(8)))
        self.level.set("全部")
        self.level.bind("<<ComboboxSelected>>", lambda _e: self._rebuild())
        self.search = tk.Entry(toolbar, width=20, bg=C["card"], fg=C["text"],
                               relief="flat", bd=0, highlightthickness=1,
                               highlightbackground=C["line"], highlightcolor=C["cyan"],
                               insertbackground=C["cyan_deep"], font=font(8))
        self.search.pack(side="right", padx=(0, px(5)), ipady=px(5), ipadx=px(6))
        self.search.bind("<KeyRelease>", lambda _e: self._rebuild())
        self.search.insert(0, "搜索…")
        self.search.bind("<FocusIn>", self._search_in)
        self.search.bind("<FocusOut>", self._search_out)
        self._search_active = False
        self.status = tk.Label(toolbar, text="", bg=C["bg"], fg=C["muted"], font=font(8))
        self.status.pack(side="left", padx=(px(12), 0), pady=(px(9), 0))

        card = tk.Frame(wrap, bg=C["sidebar"], highlightthickness=0)
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=C["sidebar"], height=px(34))
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="TIME", bg=C["sidebar"], fg=C["nav_text_dim"],
                 font=display_font(6, "normal"), width=10, anchor="w").pack(
            side="left", padx=(px(12), 0))
        tk.Label(head, text="LEVEL", bg=C["sidebar"], fg=C["nav_text_dim"],
                 font=display_font(6, "normal"), width=10, anchor="w").pack(side="left")
        tk.Label(head, text="MESSAGE", bg=C["sidebar"], fg=C["nav_text_dim"],
                 font=display_font(6, "normal"), anchor="w").pack(side="left")
        body = tk.Frame(card, bg="#071B2F")
        body.pack(fill="both", expand=True, padx=px(1), pady=(0, px(1)))
        self.text = tk.Text(body, bg="#071B2F", fg="#BFDCEA", insertbackground=C["cyan"],
                            relief="flat", bd=0, font=mono_font(9), wrap="word",
                            padx=px(12), pady=px(10), spacing1=2, spacing3=2,
                            selectbackground="#1D4A70")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview,
                                  style="Arona.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("INFO", foreground="#9CC7DD")
        self.text.tag_configure("DEBUG", foreground="#5E8197")
        self.text.tag_configure("WARNING", foreground="#FFD37A")
        self.text.tag_configure("ERROR", foreground="#FF8FA1")
        self.text.tag_configure("CRITICAL", foreground="white", background="#9E2A3A")
        self.text.tag_configure("time", foreground="#52778F")
        self.text.tag_configure("level", font=mono_font(8))
        self.text.configure(state="disabled")

    def _search_in(self, _=None) -> None:
        if not self._search_active:
            self.search.delete(0, "end")
            self.search.configure(fg=C["text"])
            self._search_active = True

    def _search_out(self, _=None) -> None:
        if self._search_active and not self.search.get().strip():
            self.search.insert(0, "搜索…")
            self.search.configure(fg=C["muted"])
            self._search_active = False

    def _query(self) -> str:
        return self.search.get().strip().lower() if self._search_active else ""

    def _set_auto(self, value: bool) -> None:
        self._auto = value
        self.status.configure(text="AUTO REFRESH ON" if value else "PAUSED")

    def on_show(self) -> None:
        self._append_snapshot(BUS.snapshot())
        self._rebuild()

    def _schedule(self) -> None:
        try:
            self._after_id = self.after(850, self._poll)
        except tk.TclError:
            pass

    def _poll(self) -> None:
        if self._auto:
            items = BUS.snapshot()
            if items and (not self._items or items[-1].seq != self._items[-1].seq):
                self._append_snapshot(items, incremental=True)
        self._schedule()

    def _append_snapshot(self, items: list[LogItem], incremental: bool = False) -> None:
        if not incremental:
            self._items = list(items)
        else:
            self._items = list(items)
        if self._auto:
            self._rebuild(keep_bottom=True)

    def _rebuild(self, keep_bottom: bool = True) -> None:
        if not self.text.winfo_exists():
            return
        at_bottom = self.text.yview()[1] > 0.98 if keep_bottom else False
        level = self.level.get()
        query = self._query()
        filtered = []
        for item in self._items:
            if level != "全部" and item.level != level:
                continue
            if query and query not in item.message.lower() and query not in item.level.lower():
                continue
            filtered.append(item)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for item in filtered[-1600:]:
            self.text.insert("end", f"{item.time}  ", "time")
            self.text.insert("end", f"{item.level:<8}", ("level", item.level))
            self.text.insert("end", item.message + "\n", item.level)
        self.text.configure(state="disabled")
        self.status.configure(
            text=f"{len(filtered)} / {len(self._items)} 条  ·  " +
                 ("AUTO REFRESH ON" if self._auto else "PAUSED"))
        if at_bottom:
            self.text.see("end")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="导出运行日志",
            defaultextension=".log", initialfile="arona-runtime.log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt")],
        )
        if not path:
            return
        try:
            level = self.level.get()
            query = self._query()
            lines = []
            for item in self._items:
                if level != "全部" and item.level != level:
                    continue
                if query and query not in item.message.lower():
                    continue
                lines.append(item.as_text())
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.app.toast(f"已导出 {len(lines)} 条日志。", "success")
        except Exception as exc:
            self.app.toast("导出失败：" + str(exc), "error")

    def _clear(self) -> None:
        BUS.clear_view()
        self._items = []
        self._rebuild()

    def _open_folder(self) -> None:
        try:
            os.startfile(str(LOGS))
        except Exception as exc:
            self.app.toast("无法打开日志文件夹：" + str(exc), "error")
