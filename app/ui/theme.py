"""Blue Archive inspired palette and shared visual helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"
FONT_MONO_FALLBACK = "Consolas"
FONT_DISPLAY = "Bahnschrift"

C = {
    "bg": "#EDF7FC",
    "bg_deep": "#DFF1FA",
    "card": "#FFFFFF",
    "card_soft": "#F5FBFF",
    "card_blue": "#EAF8FF",
    "sidebar": "#0D2948",
    "sidebar_deep": "#081D35",
    "sidebar_hover": "#153C62",
    "sidebar_active": "#1B4B75",
    "text": "#16344F",
    "text_soft": "#456B86",
    "muted": "#7A98AE",
    "line": "#D9EBF4",
    "line_strong": "#BEDDEB",
    "cyan": "#37C9EF",
    "cyan_deep": "#139DCE",
    "cyan_pale": "#D8F6FF",
    "blue": "#3186F6",
    "blue_deep": "#1E63C8",
    "pink": "#FF9DC4",
    "pink_deep": "#EF669D",
    "pink_pale": "#FFEAF3",
    "yellow": "#FFD66E",
    "yellow_deep": "#D9A21C",
    "green": "#2FC798",
    "green_pale": "#E1FBF2",
    "red": "#EF6A7E",
    "red_pale": "#FFECEF",
    "white": "#FFFFFF",
    "shadow": "#C9E2EF",
    "nav_text": "#BFD9EA",
    "nav_text_dim": "#7EA4BE",
    "hero": "#E8FAFF",
    "hero_2": "#BEEFFF",
}

STATUS_COLORS = {
    "ok": ("green", "green_pale"),
    "warn": ("yellow_deep", "#FFF6DA"),
    "fail": ("red", "red_pale"),
    "info": ("blue", "#E8F2FF"),
    "idle": ("muted", "#F0F6FA"),
}

_scale = 1.0


def init_scale(root: tk.Misc) -> None:
    global _scale
    try:
        dpi = root.winfo_fpixels("1i")
        _scale = max(0.9, min(1.6, dpi / 96.0))
    except Exception:
        _scale = 1.0


def px(value: int | float) -> int:
    return max(1, int(round(value * _scale)))


def font(size: int = 10, weight: str = "normal", family: str = FONT_UI,
         slant: str = "roman") -> tuple:
    return (family, max(7, int(round(size * _scale))), weight, slant)


def display_font(size: int = 11, weight: str = "bold") -> tuple:
    return (FONT_DISPLAY, max(7, int(round(size * _scale))), weight)


def mono_font(size: int = 9) -> tuple:
    family = FONT_MONO if FONT_MONO in tkfont.families() else FONT_MONO_FALLBACK
    return (family, max(7, int(round(size * _scale))), "normal")


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                 radius: float = 14, **kwargs) -> int:
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def chamfer_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                 cut: float = 10, **kwargs) -> int:
    points = [
        x1, y1,
        x2 - cut, y1,
        x2, y1 + cut,
        x2, y2,
        x1 + cut, y2,
        x1, y2 - cut,
    ]
    return canvas.create_polygon(points, **kwargs)


def truncate(text: str, length: int = 30) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= length else text[: max(1, length - 1)] + "…"


def mix(hex_a: str, hex_b: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    named = {"white": "#ffffff", "black": "#000000"}
    hex_a = named.get(str(hex_a).lower(), hex_a)
    hex_b = named.get(str(hex_b).lower(), hex_b)
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * ratio):02x}" for i in range(3))
