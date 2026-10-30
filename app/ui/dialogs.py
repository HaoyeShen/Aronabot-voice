"""Detail and diagnostics dialogs."""

from __future__ import annotations

import tkinter as tk

from .theme import C, display_font, font, mix, px, truncate
from .widgets import BaseDialog, RoundedButton, ScrollFrame


class DiagnosticsDialog(BaseDialog):
    def __init__(self, parent, result: dict, icon=None, bank=None) -> None:
        self.result = result
        super().__init__(parent, "自动调试报告", "ARONA SYSTEM DIAGNOSTICS",
                         width=820, height=760, icon=icon)
        overall = result.get("overall", "warn")
        color = {"ok": C["green"], "warn": C["yellow_deep"], "fail": C["red"]}.get(
            overall, C["muted"])
        summary = tk.Frame(self.body, bg=mix(color, "white", 0.88), height=px(86))
        summary.pack(fill="x", padx=px(18), pady=(px(14), px(8)))
        summary.pack_propagate(False)
        tk.Label(summary, text={"ok": "✓", "warn": "!", "fail": "×"}.get(overall, "i"),
                 bg=mix(color, "white", 0.88), fg=color, font=font(28, "bold")).pack(
            side="left", padx=(px(18), px(10)))
        text = tk.Frame(summary, bg=mix(color, "white", 0.88))
        text.pack(side="left", pady=px(15))
        tk.Label(text, text={"ok": "系统检查通过", "warn": "系统可用，但有提示",
                             "fail": "发现问题，需要处理"}.get(overall),
                 bg=mix(color, "white", 0.88), fg=C["text"], font=font(13, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(text, text=f"{result.get('passed', 0)} 项通过  ·  "
                            f"{result.get('warnings', 0)} 项提示  ·  "
                            f"{result.get('failed', 0)} 项失败  ·  "
                            f"{result.get('finished_at', '')}",
                 bg=mix(color, "white", 0.88), fg=C["text_soft"], font=font(8),
                 anchor="w").pack(fill="x")
        scroll = ScrollFrame(self.body, bg=C["card"])
        scroll.pack(fill="both", expand=True, padx=px(18), pady=(px(4), px(8)))
        inner = tk.Frame(scroll.inner, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=px(2), pady=px(2))
        for index, check in enumerate(result.get("checks", [])):
            status = check.get("status", "info")
            item_color = {"ok": C["green"], "warn": C["yellow_deep"], "fail": C["red"],
                          "info": C["blue"]}.get(status, C["muted"])
            row = tk.Frame(inner, bg=C["card_soft"] if index % 2 == 0 else C["card"],
                           highlightthickness=1, highlightbackground=C["line"])
            row.pack(fill="x", pady=px(3))
            icon = {"ok": "✓", "warn": "!", "fail": "×", "info": "i"}.get(status, "i")
            tk.Label(row, text=icon, bg=item_color, fg="white", font=font(10, "bold"),
                     width=2, height=1).pack(side="left", padx=(px(8), px(9)), pady=px(9))
            mid = tk.Frame(row, bg=row.cget("bg"))
            mid.pack(side="left", fill="x", expand=True, pady=px(7))
            tk.Label(mid, text=check.get("title", ""), bg=row.cget("bg"), fg=C["text"],
                     font=font(9, "bold"), anchor="w").pack(fill="x")
            tk.Label(mid, text=check.get("detail", ""), bg=row.cget("bg"),
                     fg=C["text_soft"], font=font(8), anchor="w", justify="left",
                     wraplength=px(590)).pack(fill="x")
            if check.get("hint"):
                tk.Label(mid, text="→ " + check.get("hint", ""), bg=row.cget("bg"),
                         fg=item_color, font=font(7), anchor="w", justify="left",
                         wraplength=px(590)).pack(fill="x", pady=(px(2), 0))
            tk.Label(row, text=status.upper(), bg=row.cget("bg"), fg=item_color,
                     font=display_font(6, "bold")).pack(side="right", padx=px(9))
        self.add_footer("关闭", self.close, "primary", 100)


class ReplyDetailDialog(BaseDialog):
    def __init__(self, parent, reply: dict, *, bank=None, icon=None) -> None:
        super().__init__(parent, "回复详情", "ARONA MESSAGE RECORD", width=680,
                         height=660, icon=icon)
        scroll = ScrollFrame(self.body, bg=C["card"])
        scroll.pack(fill="both", expand=True, padx=px(20), pady=px(14))
        wrap = tk.Frame(scroll.inner, bg=C["card"])
        wrap.pack(fill="both", expand=True, padx=px(4), pady=px(4))
        meta = tk.Frame(wrap, bg=C["card_soft"], highlightthickness=1,
                        highlightbackground=C["line"])
        meta.pack(fill="x", pady=(0, px(10)))
        sender_name = reply.get("user_sender") or "老师"
        values = [
            ("对象", reply.get("chat", "")),
            ("发送者", sender_name),
            ("时间", str(reply.get("created_iso", "")).replace("T", " ")),
            ("方向", "入站消息 → 阿洛娜回复"),
            ("模型", truncate(str(reply.get("profile_name", "") or reply.get("model", "")), 30)),
            ("耗时", f"{reply.get('latency_ms', 0)} ms"),
        ]
        for i, (key, value) in enumerate(values):
            cell = tk.Frame(meta, bg=C["card_soft"])
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=px(12), pady=px(6))
            tk.Label(cell, text=key, bg=C["card_soft"], fg=C["muted"], font=font(7),
                     anchor="w", width=5).pack(side="left")
            tk.Label(cell, text=str(value), bg=C["card_soft"], fg=C["text"],
                     font=font(8), anchor="w").pack(side="left")
        meta.grid_columnconfigure(0, weight=1)
        meta.grid_columnconfigure(1, weight=1)

        tk.Label(wrap, text=f"{sender_name}（入站）", bg=C["card"], fg=C["muted"],
                 font=display_font(7, "bold"), anchor="w").pack(fill="x")
        user_box = tk.Frame(wrap, bg=C["card_blue"], highlightthickness=1,
                            highlightbackground=C["line"])
        user_box.pack(fill="x", pady=(px(4), px(12)))
        tk.Label(user_box, text=reply.get("user_content") or "（无文本记录）",
                 bg=C["card_blue"], fg=C["text"], font=font(9), wraplength=px(580),
                 justify="left", anchor="w", padx=px(12), pady=px(9)).pack(fill="x")

        tk.Label(wrap, text="阿洛娜", bg=C["card"], fg=C["pink_deep"],
                 font=display_font(7, "bold"), anchor="w").pack(fill="x")
        reply_box = tk.Frame(wrap, bg=C["sidebar"], highlightthickness=0)
        reply_box.pack(fill="x", pady=(px(4), px(12)))
        tk.Frame(reply_box, bg=C["cyan"], width=px(4)).pack(side="left", fill="y")
        tk.Label(reply_box, text=reply.get("content") or "（只发送了一张表情包）",
                 bg=C["sidebar"], fg="white", font=font(10), wraplength=px(570),
                 justify="left", anchor="w", padx=px(12), pady=px(12)).pack(
            fill="x", side="left")
        sticker_name = reply.get("sticker_name") or ""
        if sticker_name:
            tk.Label(wrap, text="☺ 附带表情包：" + sticker_name, bg=C["card"],
                     fg=C["pink_deep"], font=font(9, "bold"), anchor="w").pack(fill="x")
            path = reply.get("sticker_path", "")
            if path and bank:
                photo = bank.photo(path, (px(180), px(180)), fit="contain")
                if photo:
                    label = tk.Label(wrap, image=photo, bg=C["card"], bd=0)
                    label.image = photo
                    label.pack(anchor="w", pady=(px(6), 0))
        self.add_footer("关闭", self.close, "primary", 100)
