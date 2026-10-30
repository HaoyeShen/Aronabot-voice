"""Shared page base class."""

from __future__ import annotations

import tkinter as tk

from ..theme import C


class BasePage(tk.Frame):
    key = ""
    title = ""
    subtitle = ""

    def __init__(self, parent, app) -> None:
        super().__init__(parent, bg=C["bg"])
        self.app = app
        self.service = app.service
        self.config = app.service.config
        self.personas = app.service.personas
        self.stickers = app.service.stickers
        self.memory = app.service.memory
        self.training = app.service.training
        self.bank = app.bank
        self.icon = app.icon

    def on_show(self) -> None:
        pass

    def on_event(self, kind: str, data: dict) -> None:
        pass

    def refresh(self) -> None:
        pass
