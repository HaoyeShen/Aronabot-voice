"""Reusable Blue Archive styled Tk widgets."""

from __future__ import annotations

import math
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .theme import C, chamfer_rect, font, mono_font, px, rounded_rect, truncate

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow is in requirements
    PIL_AVAILABLE = False


class ImageBank:
    """Cached, resized Tk images.  Reads local files only; never the screen."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._cache: dict[tuple, object] = {}

    def photo(self, path: str | Path, size: tuple[int, int] | None = None,
              *, fit: str = "cover", bg: str | None = None,
              circular: bool = False):
        path = str(path)
        if not os.path.isabs(path):
            from ..paths import ROOT
            path = str(ROOT / path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, mtime, size, fit, bg, circular)
        if key in self._cache:
            return self._cache[key]
        image = None
        if PIL_AVAILABLE:
            try:
                im = Image.open(path).convert("RGBA")
                if size:
                    w, h = max(1, int(size[0])), max(1, int(size[1]))
                    if fit == "cover":
                        im = ImageOps.fit(im, (w, h), method=Image.Resampling.LANCZOS)
                    elif fit == "contain":
                        im.thumbnail((w, h), Image.Resampling.LANCZOS)
                        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                        if bg:
                            canvas = Image.new("RGBA", (w, h), bg)
                        canvas.alpha_composite(im, ((w - im.width) // 2,
                                                    (h - im.height) // 2))
                        im = canvas
                    else:
                        im = im.resize((w, h), Image.Resampling.LANCZOS)
                if circular:
                    side = min(im.width, im.height)
                    im = ImageOps.fit(im, (side, side), method=Image.Resampling.LANCZOS)
                    mask = Image.new("L", (side * 2, side * 2), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, side * 2 - 1, side * 2 - 1), fill=255)
                    mask = mask.resize((side, side), Image.Resampling.LANCZOS)
                    im.putalpha(mask)
                image = ImageTk.PhotoImage(im, master=self.root)
            except Exception:
                image = None
        if image is None:
            try:
                image = tk.PhotoImage(file=path, master=self.root)
                if size and image.width() > size[0]:
                    factor = max(1, math.ceil(image.width() / size[0]))
                    image = image.subsample(factor, factor)
            except Exception:
                image = None
        self._cache[key] = image
        return image


class RoundedButton(tk.Canvas):
    STYLES = {
        "primary": {"bg": C["cyan_deep"], "hover": "#2ABBE8", "fg": "white",
                    "line": C["cyan_deep"], "accent": C["pink"]},
        "blue": {"bg": C["blue"], "hover": "#4B98FF", "fg": "white",
                 "line": C["blue"], "accent": C["cyan"]},
        "secondary": {"bg": "white", "hover": C["card_blue"], "fg": C["cyan_deep"],
                      "line": C["cyan"], "accent": C["cyan"]},
        "pink": {"bg": C["pink_deep"], "hover": "#FA7FAF", "fg": "white",
                 "line": C["pink_deep"], "accent": C["yellow"]},
        "dark": {"bg": C["sidebar"], "hover": C["sidebar_hover"], "fg": "white",
                 "line": C["sidebar"], "accent": C["cyan"]},
        "ghost": {"bg": None, "hover": C["card_blue"], "fg": C["text_soft"],
                  "line": "", "accent": C["cyan"]},
        "on_dark": {"bg": None, "hover": "#1A446B", "fg": "white",
                    "line": "", "accent": C["cyan"]},
        "danger": {"bg": C["red_pale"], "hover": "#FFDCE1", "fg": C["red"],
                   "line": "#FFC8D1", "accent": C["red"]},
        "success": {"bg": C["green_pale"], "hover": "#D2F8EB", "fg": "#18956E",
                    "line": "#BEEEDD", "accent": C["green"]},
    }

    def __init__(self, parent, text: str = "", command: Callable | None = None,
                 style: str = "primary", width: int = 132, height: int = 38,
                 icon: str = "", font_size: int = 10, bg: str | None = None,
                 radius: int = 10, **kwargs) -> None:
        parent_bg = bg or _widget_bg(parent)
        super().__init__(parent, width=px(width), height=px(height), bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self._text = text
        self._command = command
        self._style = self.STYLES.get(style, self.STYLES["primary"])
        self._icon = icon
        self._font_size = font_size
        self._radius = radius
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._busy = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Key-space>", lambda _e: self._invoke())
        self.bind("<Key-Return>", lambda _e: self._invoke())
        self.configure(takefocus=1)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            w, h = int(self["width"]), int(self["height"])
        style = self._style
        if not self._enabled:
            fill, line, fg = "#E8F1F6", "#DCE9F1", C["muted"]
        else:
            fill = style["hover"] if (self._hover or self._pressed) else style["bg"]
            fill = fill or self["bg"]
            line = style["line"]
            fg = style["fg"]
        rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill,
                     outline=line or fill, width=1)
        # Signature Blue Archive corner notch / accent bar.
        if self._enabled and style.get("accent") and self._style["bg"] is not None:
            self.create_rectangle(w - px(18), 1, w - px(5), 3,
                                  fill=style["accent"], outline="")
        label = ("… " if self._busy else (self._icon + "  " if self._icon else "")) + self._text
        self.create_text(w / 2, h / 2 + (1 if self._pressed else 0), text=label,
                         fill=fg, font=font(self._font_size, "bold"))
        if self._enabled:
            self.configure(cursor="hand2")
        else:
            self.configure(cursor="arrow")

    def _on_enter(self, _=None) -> None:
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _=None) -> None:
        if self._enabled:
            self._pressed = True
            self.focus_set()
            self._draw()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if self._enabled and was_pressed and event is not None:
            if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
                self._invoke()

    def _invoke(self) -> None:
        if self._enabled and self._command and not self._busy:
            self._command()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._draw()

    def set_busy(self, busy: bool, text: str | None = None) -> None:
        self._busy = busy
        self._enabled = not busy
        if text is not None:
            self._text = text
        self._draw()


def _widget_bg(widget) -> str:
    try:
        bg = widget.cget("bg")
        if bg:
            return str(bg)
    except Exception:
        pass
    try:
        return str(widget.master.cget("bg"))
    except Exception:
        return C["bg"]


class Pill(tk.Canvas):
    def __init__(self, parent, text: str = "", fg: str | None = None,
                 bg: str | None = None, height: int = 24, font_size: int = 8,
                 dot: bool = False, width: int | None = None) -> None:
        self._fg = fg or C["cyan_deep"]
        self._fill = bg or C["cyan_pale"]
        self._text = text
        self._dot = dot
        self._font_size = font_size
        parent_bg = _widget_bg(parent)
        estimated = width or max(54, int(len(text) * (11.5 if font_size <= 8 else 13.0)) +
                                 (26 if dot else 18))
        super().__init__(parent, height=px(height), width=px(estimated),
                         bg=parent_bg, highlightthickness=0, bd=0)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            w, h = int(self["width"]), int(self["height"])
        rounded_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=self._fill,
                     outline=self._fill)
        text_x = w / 2 + (6 if self._dot else 0)
        if self._dot:
            r = px(3)
            self.create_oval(px(10), h / 2 - r, px(10) + 2 * r, h / 2 + r,
                             fill=self._fg, outline="")
        self.create_text(text_x, h / 2, text=self._text, fill=self._fg,
                         font=font(self._font_size, "bold"))

    def set(self, text: str, fg: str | None = None, bg: str | None = None) -> None:
        self._text = text
        if fg:
            self._fg = fg
        if bg:
            self._fill = bg
        self._draw()


class Card(tk.Frame):
    def __init__(self, parent, *, padding: int = 18, bg: str | None = None,
                 border: bool = True, accent: str | None = None,
                 radius: int = 0, **kwargs) -> None:
        bg = bg or C["card"]
        super().__init__(parent, bg=bg,
                         highlightthickness=1 if border else 0,
                         highlightbackground=C["line"] if border else bg,
                         bd=0, **kwargs)
        self._bg = bg
        if accent:
            bar = tk.Frame(self, bg=accent, height=px(3))
            bar.pack(fill="x", side="top")
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=px(padding), pady=px(padding))


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg: str | None = None, *, scrollbar: bool = True) -> None:
        bg = bg or _widget_bg(parent)
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                  style="Arona.Vertical.TScrollbar")
        if scrollbar:
            self.vbar.pack(side="right", fill="y", padx=(px(2), 0))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.inner.bind("<Enter>", self._bind_wheel)
        self.inner.bind("<Leave>", self._unbind_wheel)
        self._wheel_bound = False

    def _on_inner(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._on_inner()

    def _bind_wheel(self, _event=None) -> None:
        if not self._wheel_bound:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
            self._wheel_bound = True

    def _unbind_wheel(self, _event=None) -> None:
        if self._wheel_bound:
            self.canvas.unbind_all("<MouseWheel>")
            self._wheel_bound = False

    def _on_wheel(self, event) -> None:
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def scroll_to_bottom(self) -> None:
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, value: bool = False, command: Callable[[bool], None] | None = None,
                 width: int = 44, height: int = 23) -> None:
        super().__init__(parent, width=px(width), height=px(height), bg=_widget_bg(parent),
                         highlightthickness=0, bd=0, cursor="hand2")
        self._value = bool(value)
        self._command = command
        self._switch_w, self._switch_h = px(width), px(height)
        self.bind("<Button-1>", self._toggle)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 2 else self._switch_w
        h = self.winfo_height() if self.winfo_height() > 2 else self._switch_h
        fill = C["cyan"] if self._value else "#CBDDE8"
        rounded_rect(self, 0, 0, w, h, h / 2, fill=fill, outline=fill)
        knob = h - px(6)
        x = w - knob - px(3) if self._value else px(3)
        self.create_oval(x, px(3), x + knob, px(3) + knob, fill="white", outline="")

    def _toggle(self, _event=None) -> None:
        self._value = not self._value
        self._draw()
        if self._command:
            self._command(self._value)

    def get(self) -> bool:
        return self._value

    def set(self, value: bool, silent: bool = True) -> None:
        self._value = bool(value)
        self._draw()
        if not silent and self._command:
            self._command(self._value)


class ProgressBar(tk.Canvas):
    def __init__(self, parent, height: int = 8, color: str | None = None) -> None:
        super().__init__(parent, height=px(height), bg=_widget_bg(parent),
                         highlightthickness=0, bd=0)
        self._value = 0
        self._color = color or C["cyan"]
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, value: float) -> None:
        self._value = max(0, min(100, float(value)))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        rounded_rect(self, 0, 1, w, h - 1, h / 2, fill=C["card_blue"], outline="")
        if self._value > 0:
            fill_w = max(h, w * self._value / 100)
            rounded_rect(self, 0, 1, fill_w, h - 1, h / 2, fill=self._color, outline="")


class LabeledEntry(tk.Frame):
    def __init__(self, parent, label: str, value: str = "", *, hint: str = "",
                 width: int = 32, show: str | None = None, bg: str | None = None) -> None:
        bg = bg or _widget_bg(parent)
        super().__init__(parent, bg=bg)
        self.var = tk.StringVar(value=value)
        if label:
            head = tk.Frame(self, bg=bg)
            head.pack(fill="x", pady=(0, px(5)))
            tk.Label(head, text=label, bg=bg, fg=C["text_soft"], font=font(9, "bold")).pack(side="left")
            if hint:
                tk.Label(head, text=hint, bg=bg, fg=C["muted"], font=font(8)).pack(side="right")
        self.entry = tk.Entry(self, textvariable=self.var, width=width, show=show or "",
                              bg=C["card_soft"], fg=C["text"], insertbackground=C["cyan_deep"],
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground=C["line"], highlightcolor=C["cyan"],
                              font=font(10))
        self.entry.pack(fill="x", ipady=px(7), ipadx=px(8))

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value)


class LabeledText(tk.Frame):
    def __init__(self, parent, label: str, value: str = "", *, height: int = 4,
                 hint: str = "", bg: str | None = None) -> None:
        bg = bg or _widget_bg(parent)
        super().__init__(parent, bg=bg)
        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", pady=(0, px(5)))
        tk.Label(head, text=label, bg=bg, fg=C["text_soft"], font=font(9, "bold")).pack(side="left")
        if hint:
            tk.Label(head, text=hint, bg=bg, fg=C["muted"], font=font(8)).pack(side="right")
        self.text = tk.Text(self, height=height, bg=C["card_soft"], fg=C["text"],
                            insertbackground=C["cyan_deep"], relief="flat", bd=0,
                            highlightthickness=1, highlightbackground=C["line"],
                            highlightcolor=C["cyan"], font=font(10), wrap="word",
                            padx=px(8), pady=px(6), spacing1=2, spacing3=2)
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", value)

    def get(self) -> str:
        return self.text.get("1.0", "end").strip()

    def set(self, value: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)


class SearchBox(tk.Frame):
    def __init__(self, parent, placeholder: str = "搜索…", width: int = 24,
                 command: Callable[[str], None] | None = None) -> None:
        super().__init__(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"], bd=0)
        tk.Label(self, text="⌕", bg=C["card"], fg=C["muted"],
                 font=font(13)).pack(side="left", padx=(px(8), px(3)))
        self.var = tk.StringVar()
        self.entry = tk.Entry(self, textvariable=self.var, width=width, relief="flat",
                              bd=0, bg=C["card"], fg=C["text"], font=font(9),
                              insertbackground=C["cyan_deep"])
        self.entry.pack(side="left", fill="x", expand=True, ipady=px(7))
        self.placeholder = placeholder
        self._placeholder_on = False
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<KeyRelease>", lambda _e: command and command(self.get()))
        self.entry.bind("<Return>", lambda _e: command and command(self.get()))

    def _show_placeholder(self) -> None:
        if not self.var.get():
            self._placeholder_on = True
            self.entry.configure(fg=C["muted"])
            self.var.set(self.placeholder)

    def _focus_in(self, _=None) -> None:
        if self._placeholder_on:
            self.var.set("")
            self.entry.configure(fg=C["text"])
            self._placeholder_on = False

    def _focus_out(self, _=None) -> None:
        if not self.var.get().strip():
            self._show_placeholder()

    def get(self) -> str:
        return "" if self._placeholder_on else self.var.get().strip()

    def clear(self) -> None:
        self._placeholder_on = False
        self.var.set("")
        self._show_placeholder()


class SectionHeading(tk.Frame):
    def __init__(self, parent, title: str, subtitle: str = "", *,
                 action: str = "", command: Callable | None = None,
                 bg: str | None = None) -> None:
        bg = bg or _widget_bg(parent)
        super().__init__(parent, bg=bg)
        left = tk.Frame(self, bg=bg)
        left.pack(side="left", fill="x", expand=True)
        line = tk.Frame(left, bg=C["cyan"], width=px(3), height=px(18))
        line.pack(side="left", padx=(0, px(8)))
        tk.Label(left, text=title, bg=bg, fg=C["text"],
                 font=font(11, "bold")).pack(side="left")
        if subtitle:
            tk.Label(left, text="  " + subtitle, bg=bg, fg=C["muted"],
                     font=font(8)).pack(side="left", padx=(px(3), 0))
        if action:
            link = tk.Label(self, text=action, bg=bg, fg=C["cyan_deep"],
                            font=font(9, "bold"), cursor="hand2")
            link.pack(side="right")
            if command:
                link.bind("<Button-1>", lambda _e: command())


class ToggleRow(tk.Frame):
    def __init__(self, parent, title: str, description: str = "", value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 bg: str | None = None) -> None:
        bg = bg or _widget_bg(parent)
        super().__init__(parent, bg=bg)
        text = tk.Frame(self, bg=bg)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, bg=bg, fg=C["text"], font=font(10, "bold"),
                 anchor="w").pack(fill="x")
        if description:
            tk.Label(text, text=description, bg=bg, fg=C["muted"], font=font(8),
                     anchor="w", justify="left", wraplength=px(560)).pack(fill="x",
                                                                          pady=(px(2), 0))
        self.switch = ToggleSwitch(self, value=value, command=command)
        self.switch.pack(side="right", padx=(px(16), 0))

    def get(self) -> bool:
        return self.switch.get()


class Divider(tk.Frame):
    def __init__(self, parent, bg: str | None = None, color: str | None = None) -> None:
        super().__init__(parent, bg=bg or _widget_bg(parent), height=1)
        tk.Frame(self, bg=color or C["line"], height=1).pack(fill="x")


class ToastManager:
    def __init__(self, host: tk.Frame) -> None:
        self.host = host
        self.frame: tk.Frame | None = None
        self._after_id = None

    def show(self, message: str, level: str = "info", duration: int = 4200) -> None:
        self.hide()
        colors = {
            "info": (C["sidebar"], "white"),
            "success": ("#116B55", "white"),
            "warn": ("#8A6411", "white"),
            "error": ("#8E2838", "white"),
        }
        bg, fg = colors.get(level, colors["info"])
        self.frame = tk.Frame(self.host, bg=bg, highlightthickness=0)
        icon = {"info": "i", "success": "✓", "warn": "!", "error": "×"}.get(level, "i")
        tk.Label(self.frame, text=icon, bg=bg, fg=C["cyan"] if level == "info" else "white",
                 font=font(12, "bold")).pack(side="left", padx=(px(14), px(8)), pady=px(10))
        tk.Label(self.frame, text=message, bg=bg, fg=fg, font=font(9),
                 wraplength=px(430), justify="left").pack(side="left", padx=(0, px(16)),
                                                         pady=px(10))
        self.frame.place(relx=1.0, rely=1.0, x=-px(24), y=-px(20), anchor="se")
        self.frame.lift()
        self._after_id = self.host.after(duration, self.hide)

    def __call__(self, message: str, level: str = "info",
                 duration: int = 4200) -> None:
        """Allow both app.toast(...) and app.toast.show(...) call styles."""
        self.show(message, level, duration)

    def hide(self) -> None:
        if self._after_id:
            try:
                self.host.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self.frame is not None:
            self.frame.destroy()
            self.frame = None


class BaseDialog(tk.Toplevel):
    def __init__(self, parent, title: str, subtitle: str = "", *,
                 width: int = 620, height: int = 620, icon=None) -> None:
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(px(min(width, 540)), px(min(height, 420)))
        if icon:
            try:
                self.iconphoto(False, icon)
            except Exception:
                pass
        self._parent = parent
        screen_w = max(800, self.winfo_screenwidth())
        screen_h = max(600, self.winfo_screenheight())
        self._dialog_w = min(px(width), screen_w - px(70))
        self._dialog_h = min(px(height), screen_h - px(95))
        shell = tk.Frame(self, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line_strong"])
        shell.pack(fill="both", expand=True, padx=px(8), pady=px(8))
        header = tk.Frame(shell, bg=C["sidebar"], height=px(72))
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=C["cyan"], width=px(6)).pack(side="left", fill="y")
        head_text = tk.Frame(header, bg=C["sidebar"])
        head_text.pack(side="left", fill="both", expand=True, padx=px(16), pady=px(11))
        tk.Label(head_text, text=title, bg=C["sidebar"], fg="white",
                 font=font(15, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(head_text, text=subtitle, bg=C["sidebar"], fg=C["nav_text"],
                     font=font(8)).pack(anchor="w", pady=(px(2), 0))
        close = tk.Label(header, text="×", bg=C["sidebar"], fg=C["nav_text"],
                         font=font(18), cursor="hand2", padx=px(16))
        close.pack(side="right", fill="y")
        close.bind("<Button-1>", lambda _e: self.close())
        # Reserve the footer before the expanding body.  Packing the body
        # first could starve the footer when a dialog grows taller than its
        # requested height, leaving the save/cancel buttons as thin bars.
        self.footer = tk.Frame(shell, bg=C["card_soft"], height=px(62))
        self.footer.pack(side="bottom", fill="x")
        self.body = tk.Frame(shell, bg=C["card"])
        self.body.pack(fill="both", expand=True)
        tk.Frame(self.footer, bg=C["line"], height=1).pack(fill="x", side="top")
        self._footer_inner = tk.Frame(self.footer, bg=C["card_soft"])
        self._footer_inner.pack(fill="both", expand=True, padx=px(16), pady=px(12))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

    def add_footer(self, text: str, command: Callable, style: str = "primary",
                   width: int = 120) -> RoundedButton:
        btn = RoundedButton(self._footer_inner, text=text, command=command, style=style,
                            width=width, height=36, bg=C["card_soft"])
        btn.pack(side="right", padx=(px(8), 0))
        return btn

    def show(self, grab: bool = True) -> None:
        self.update_idletasks()
        try:
            self._parent.update_idletasks()
            px_x = self._parent.winfo_rootx()
            px_y = self._parent.winfo_rooty()
            pw = self._parent.winfo_width()
            ph = self._parent.winfo_height()
            x = px_x + max(0, (pw - self._dialog_w) // 2)
            y = px_y + max(0, (ph - self._dialog_h) // 2)
        except Exception:
            x, y = 200, 120
        self.geometry(f"{self._dialog_w}x{self._dialog_h}+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if grab:
            try:
                self.grab_set()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class ConfirmDialog(BaseDialog):
    def __init__(self, parent, title: str, message: str, *, ok_text: str = "确定",
                 cancel_text: str = "取消", danger: bool = False, icon=None) -> None:
        super().__init__(parent, title, "请确认这次操作", width=470, height=300, icon=icon)
        wrap = tk.Frame(self.body, bg=C["card"])
        wrap.pack(fill="both", expand=True, padx=px(24), pady=px(22))
        tk.Label(wrap, text="!" if danger else "?", bg=C["pink_pale"] if danger else C["cyan_pale"],
                 fg=C["pink_deep"] if danger else C["cyan_deep"],
                 font=font(20, "bold"), width=2, height=1).pack(anchor="w", pady=(px(4), px(12)))
        tk.Label(wrap, text=message, bg=C["card"], fg=C["text"], font=font(10),
                 justify="left", wraplength=px(390), anchor="w").pack(fill="x")
        self.result = False
        self.add_footer(cancel_text, self.close, "secondary", 100)
        self.add_footer(ok_text, self._ok, "danger" if danger else "primary", 110)

    def _ok(self) -> None:
        self.result = True
        self.close()

    def ask(self) -> bool:
        self.show()
        self.wait_window()
        return self.result


class InfoDialog(BaseDialog):
    def __init__(self, parent, title: str, message: str, *, subtitle: str = "",
                 icon=None) -> None:
        self._message_height = max(300, min(620, 260 + message.count("\n") * 18))
        super().__init__(parent, title, subtitle, width=560, height=min(620, self._message_height),
                         icon=icon)
        wrap = tk.Frame(self.body, bg=C["card"])
        wrap.pack(fill="both", expand=True, padx=px(24), pady=px(20))
        text = tk.Text(wrap, bg=C["card_soft"], fg=C["text"], font=font(9),
                       relief="flat", bd=0, wrap="word", padx=px(14), pady=px(12),
                       highlightthickness=1, highlightbackground=C["line"])
        text.pack(fill="both", expand=True)
        text.insert("1.0", message)
        text.configure(state="disabled")
        self.add_footer("知道了", self.close, "primary", 110)


def confirm(parent, title: str, message: str, *, ok_text: str = "确定",
            danger: bool = False, icon=None) -> bool:
    return ConfirmDialog(parent, title, message, ok_text=ok_text, danger=danger,
                         icon=icon).ask()


def info(parent, title: str, message: str, *, subtitle: str = "", icon=None) -> None:
    dialog = InfoDialog(parent, title, message, subtitle=subtitle, icon=icon)
    dialog.show()
    parent.wait_window(dialog)


def setup_ttk(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Arona.Vertical.TScrollbar",
        background=C["line_strong"], troughcolor=C["bg"], bordercolor=C["bg"],
        arrowcolor=C["muted"], lightcolor=C["line_strong"], darkcolor=C["line_strong"],
        width=px(8), arrowsize=px(8),
    )
    style.map("Arona.Vertical.TScrollbar",
              background=[("active", C["cyan"]), ("pressed", C["cyan_deep"])])
    style.configure(
        "Arona.TCombobox",
        fieldbackground=C["card_soft"], background=C["card_soft"], foreground=C["text"],
        bordercolor=C["line"], lightcolor=C["line"], darkcolor=C["line"],
        arrowcolor=C["cyan_deep"], padding=px(6), font=font(9),
    )
    style.map(
        "Arona.TCombobox",
        fieldbackground=[("readonly", C["card_soft"])],
        bordercolor=[("focus", C["cyan"])],
        arrowcolor=[("active", C["pink_deep"])],
    )
    root.option_add("*TCombobox*Listbox.background", C["card"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["cyan_pale"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["text"])
    root.option_add("*TCombobox*Listbox.font", font(9))


class ToolTip:
    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None) -> None:
        if self.tip:
            return
        x = self.widget.winfo_rootx() + px(12)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(6)
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=C["sidebar"], fg="white", font=font(8),
                 padx=px(8), pady=px(4)).pack()

    def _hide(self, _=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None
