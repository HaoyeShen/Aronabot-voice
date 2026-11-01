"""Compact Schale header and persistent access to every feature."""
from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import ttk

from .. import __version__
from ..paths import ASSETS
from .shell import NAV_ITEMS
from .theme import C, display_font, font, px, truncate
from .widgets import Pill, RoundedButton


class TopBar(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C['bg'], height=px(88))
        self.pack_propagate(False)
        self.app = app
        left = tk.Frame(self, bg=C['bg'])
        left.pack(side='left', fill='y', padx=(px(20), px(10)), pady=px(12))
        photo = app.bank.photo(ASSETS/'app_icon.png', (px(38), px(38)))
        if photo:
            icon = tk.Label(left, image=photo, bg=C['bg'])
            icon.image = photo
            icon.pack(side='left', padx=(0, px(10)))
        brand = tk.Frame(left, bg=C['bg'])
        brand.pack(side='left')
        self.title = tk.Label(brand, text='什亭之匣', bg=C['bg'], fg='#36577B', font=font(19, 'bold', slant='italic'))
        self.title.pack(anchor='w')
        self.breadcrumb = tk.Label(brand, text='S.C.H.A.L.E. / ARONA', bg=C['bg'], fg='#5B8BAA', font=display_font(7))
        self.breadcrumb.pack(anchor='w')
        self.subtitle = tk.Label(brand, text='', bg=C['bg'], fg=C['muted'], font=font(7))
        # Home and the picker stay available inside every original page.
        right = tk.Frame(self, bg=C['bg'])
        right.pack(side='right', padx=px(20), pady=px(17))
        self.home = RoundedButton(right, text='⌂ 主页', command=lambda: app.show_page('home'),
                                  style='secondary', width=75, height=34, bg=C['bg'], font_size=9)
        self.home.pack(side='right', padx=(px(8), 0))
        self.action = RoundedButton(right, text='启动机器人', command=app.toggle_bot,
                                    width=110, height=34, bg=C['bg'], font_size=9)
        self.action.pack(side='right', padx=(px(8), 0))
        self._labels = {key: label for key, label, _icon, _code in NAV_ITEMS}
        self.picker = ttk.Combobox(right, state='readonly', width=13, values=list(self._labels.values()),
                                   font=font(9), style='Arona.TCombobox')
        self.picker.set('功能导航')
        self.picker.pack(side='right', padx=px(10), ipady=px(4))
        self.picker.bind('<<ComboboxSelected>>', self._navigate)
        self.status_pill = Pill(right, text='待机', fg=C['muted'], bg='#E7F2FC', width=98, height=27, dot=True, font_size=8)
        self.status_pill.pack(side='right', padx=px(5))
        self.model_pill = Pill(right, text='MODEL …', fg=C['blue'], bg='#C7E3FA', width=175, height=27, font_size=8)
        self.model_pill.pack(side='right', padx=px(6))
        self.clock = tk.Label(right, bg=C['bg'], fg=C['text_soft'], font=display_font(9))
        self.clock.pack(side='right', padx=px(6))
        self.date_label = tk.Label(right, bg=C['bg'], fg=C['muted'], font=font(7))
        self._optional_visible = True
        self.bind('<Configure>', self._fit_header)
        self._tick()

    def _fit_header(self, event):
        visible = event.width >= px(1150)
        if visible == self._optional_visible:
            return
        self._optional_visible = visible
        if visible:
            self.model_pill.pack(side='right', padx=px(6))
            self.clock.pack(side='right', padx=px(6))
        else:
            self.model_pill.pack_forget()
            self.clock.pack_forget()

    def _navigate(self, _event=None):
        key = next((key for key, label in self._labels.items() if label == self.picker.get()), 'home')
        self.app.show_page(key)

    def set_active(self, key):
        self.picker.set(self._labels.get(key, '功能导航'))

    def set_page(self, title, subtitle, code):
        self.breadcrumb.configure(text='S.C.H.A.L.E. / '+('ARONA' if code == 'HOME' else code))

    def _tick(self):
        if not self.winfo_exists():
            return
        now = datetime.now()
        self.clock.configure(text=now.strftime('%H:%M'))
        self.date_label.configure(text=now.strftime('%m.%d'))
        self._clock_job = self.after(1000, self._tick)

    def update_state(self, state):
        starting, running, online = state.get('starting'), state.get('running'), state.get('wechat_connected')
        if starting:
            self.status_pill.set('连接中…', C['yellow_deep'], '#FFF0CB')
            self.action.set_text('连接中')
            self.action.set_enabled(False)
        elif running:
            self.status_pill.set('微信在线' if online else '等待微信', '#23876D' if online else C['yellow_deep'], '#D9F3EE' if online else '#FFF0CB')
            self.action.set_text('停止机器人')
            self.action.set_enabled(True)
        else:
            self.status_pill.set('待机', '#628199', '#D8EAF9')
            self.action.set_text('启动机器人')
            self.action.set_enabled(True)
        self.model_pill.set(truncate('MODEL · '+str(state.get('model') or '未配置'), 22), C['blue'], '#C7E3FA')
