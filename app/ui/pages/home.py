"""Schale lobby: all original pages, with a locally animated Arona portrait."""
from __future__ import annotations

import logging
import random
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from ...paths import ASSETS
from ..shell import NAV_ITEMS
from ..theme import C, display_font, font, mix, px
from .base import BasePage

log = logging.getLogger(__name__)

DESCRIPTIONS = {
    'dashboard': '状态总览 / 启动与停止', 'listen_reply': '触发规则 / 引用 / 语音',
    'targets': '群与好友 / 上下文 / 新闻', 'stickers': '收集每一个小心情',
    'personas': '阿洛娜与普拉娜', 'training': '资料与记忆，一点点积累',
    'models': '让每一次对话都接得上', 'debug': '检查连接 / 本地试聊',
    'monitor': '每一条消息，都有迹可循', 'logs': '把运行中的每一步留下',
    'settings': '把什亭之匣调成喜欢的样子',
}


class FeatureTile(tk.Canvas):
    """A keyboard accessible slanted menu tile over the classroom backdrop."""
    def __init__(self, parent, key, label, code, command):
        super().__init__(parent, highlightthickness=0, bd=0, takefocus=1, cursor='hand2')
        self.key, self.label, self.code, self.command = key, label, code, command
        self.hover = False
        self.major = False
        self.compact = False
        self._photo = None
        self.bind('<Enter>', lambda e: self._hover(True))
        self.bind('<Leave>', lambda e: self._hover(False))
        self.bind('<FocusIn>', lambda e: self._hover(True))
        self.bind('<FocusOut>', lambda e: self._hover(False))
        self.bind('<Button-1>', lambda e: self.invoke())
        self.bind('<Return>', lambda e: self.invoke())
        self.bind('<space>', lambda e: self.invoke())

    def invoke(self):
        self.command(self.key)

    def _hover(self, value):
        self.hover = value
        self.draw()

    def set_background(self, backdrop, box):
        self._photo = ImageTk.PhotoImage(backdrop.crop(box), master=self)
        self.draw()

    def draw(self):
        self.delete('all')
        w, h = int(self['width']), int(self['height'])
        if w < 10 or h < 10:
            return
        if self._photo:
            self.create_image(0, 0, image=self._photo, anchor='nw')
        skew = min(px(19), w // 12)
        top = 1 if self.hover else 4
        self.create_polygon(skew+1, top+4, w-2, top+4, w-skew-2, h-1, 1, h-1,
                            fill='#729BB8', outline='')
        base = '#D9EDFF' if not self.hover else '#EEF8FF'
        end = '#8BC6ED'
        if self.key == 'stickers':
            end = '#BFC6F1'
        elif self.key == 'models':
            end = '#8FBEDB'
        for row in range(top, h-5):
            t = (row-top) / max(1, h-5-top)
            left = skew * (1-t)
            right = w-2-skew*t
            self.create_line(left, row, right, row, fill=mix(base, end, t), width=1)
        self.create_polygon(skew, top, w-3, top, w-skew-3, h-6, 0, h-6,
                            fill='', outline='#EAF7FF', width=2 if self.hover else 1)
        x = skew + px(7)
        if self.compact:
            self.create_text(x, h/2, text=self.label, anchor='w', fill='#365B7E', font=font(10, 'bold'))
            self.create_text(w-skew-px(12), h/2, text='↗', fill='#5B8EAD', font=font(13))
            return
        if self.major:
            badge = '#FFA9C9' if self.key == 'listen_reply' else '#FFD979'
            self.create_polygon(x, top, x+px(123), top, x+px(119), top+px(20), x-px(3), top+px(20), fill=badge, outline='')
            self.create_text(x+px(5), top+px(10), text=self.code, anchor='w', fill='#526C86', font=display_font(7))
        ty = top + px(38 if self.major else 22)
        self.create_text(x, ty, text=self.label, anchor='w', fill='#355B85',
                         font=font(23 if self.major else 15, 'bold', slant='italic'))
        self.create_text(x, ty+px(27 if self.major else 23), text=DESCRIPTIONS[self.key],
                         anchor='w', fill='#567C9C', font=font(8), width=max(20, w-x-px(14)))
        # Geometric postcard illustration, kept behind the labels.
        for i in range(8):
            xx = w*.48+i*px(12)
            if xx < w-skew-px(10):
                height = px(12+(i*11)%35)
                self.create_rectangle(xx, h-px(22)-height, xx+px(8), h-px(22),
                                      fill=mix('#6AB3D6', '#B9E5F6', i/8), outline='#D8F0FB')
        self.create_text(x, h-px(17), text=self.code, anchor='w', fill='#527F9D', font=display_font(7))
        self.create_text(w-skew-px(12), h-px(18), text='↗', fill='#4F83A7', font=font(15))


class HomePage(BasePage):
    key, title, subtitle = 'home', '什亭之匣', '今天，也一起加油吧。'

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.canvas = tk.Canvas(self, bg=C['bg'], highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.tiles = {key: FeatureTile(self.canvas, key, label, code, app.show_page)
                      for key, label, _icon, code in NAV_ITEMS}
        self._resize_job = self._blink_job = self._open_job = self._pet_job = None
        self._active = False
        self._closed = False
        self._pet_until = 0.0
        self._photos = []
        self._portrait_photos = []
        self._head_box = None
        self._portrait_id = None
        self._dialog_id = None
        self._default_dialogue = '老师，要从哪一项工作开始呢？'
        self._background = self._portrait = self._closed_portrait = None
        try:
            self._background = Image.open(ASSETS/'lobby_classroom.jpg').convert('RGB')
            self._portrait = Image.open(ASSETS/'arona_standing.png').convert('RGBA')
            closed = Image.open(ASSETS/'arona_closed.png').convert('RGBA').resize(self._portrait.size, Image.Resampling.LANCZOS)
            # Only blend the eye region, so the original body/hair never jumps.
            mask = Image.new('L', self._portrait.size, 0)
            draw = ImageDraw.Draw(mask)
            w, h = self._portrait.size
            draw.ellipse((w*.357, h*.167, w*.497, h*.247), fill=255)
            from PIL import ImageFilter
            mask = mask.filter(ImageFilter.GaussianBlur(1.5))
            self._closed_portrait = Image.composite(closed, self._portrait, mask)
        except Exception as exc:
            log.warning('首页插画加载失败，保留全部功能入口：%s', exc)
        self.canvas.bind('<Configure>', self._resize)
        self.canvas.bind('<Button-1>', self._click)
        self.canvas.bind('<Motion>', self._motion)
        self.canvas.bind('<Return>', lambda e: self.pet_head())
        self.canvas.bind('<space>', lambda e: self.pet_head())
        self.canvas.configure(takefocus=1)
        self.bind('<Destroy>', self._destroy, add='+')

    def _cancel(self, name):
        job = getattr(self, name)
        if job:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, name, None)

    def _resize(self, _event=None):
        self._cancel('_resize_job')
        self._resize_job = self.after(70, self._draw)

    def _draw(self):
        self._resize_job = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 200 or h < 200:
            return
        self.canvas.delete('all')
        self._photos.clear()
        if self._background:
            backdrop = ImageOps.fit(self._background, (w, h), method=Image.Resampling.LANCZOS)
            backdrop = Image.blend(backdrop, Image.new('RGB', (w, h), '#8BC8EE'), .16)
        else:
            backdrop = Image.new('RGB', (w, h), C['bg'])
        photo = ImageTk.PhotoImage(backdrop, master=self.canvas)
        self._photos.append(photo)
        self.canvas.create_image(0, 0, image=photo, anchor='nw')
        self.canvas.create_text(w*.04, px(22), text='WELCOME TO SCHALE', anchor='w', fill='#347C9E', font=display_font(8))
        self.canvas.create_text(w*.04, px(50), text='今天，也一起加油吧。', anchor='w', fill='#2F587D', font=font(18, 'bold'))
        self.canvas.create_text(w*.04, px(77), text='什亭之匣，连接老师的每一天。', anchor='w', fill='#567E9E', font=font(9))
        # Crop at the waist without modifying the user's source PNG.
        if self._portrait is not None and self._closed_portrait is not None:
            area_w, top = int(w*.35), px(103)
            area_h = max(1, h-top)
            portrait_w = int(area_w*1.48)
            portrait_h = round(self._portrait.height*portrait_w/self._portrait.width)
            offset = -int(area_w*.24)
            self._portrait_photos.clear()
            for source in (self._portrait, self._closed_portrait):
                image = source.resize((portrait_w, portrait_h), Image.Resampling.LANCZOS)
                layer = backdrop.crop((0, top, area_w, h)).convert('RGBA')
                layer.alpha_composite(image, (offset, 0))
                self._portrait_photos.append(ImageTk.PhotoImage(layer, master=self.canvas))
            self._portrait_id = self.canvas.create_image(0, top, anchor='nw', image=self._portrait_photos[int(self._closed)])
            self._head_box = (offset+portrait_w*.28, top+portrait_h*.08,
                              offset+portrait_w*.70, min(h, top+portrait_h*.27))
        # Native focusable widgets preserve mouse and keyboard navigation.
        x0, right, gap = int(w*.36), w-px(28), px(13)
        usable = right-x0
        major_h = int(h*.285)
        minor_h = int(h*.20)
        y = max(px(26), int(h*.055))
        rows = [('dashboard', 'listen_reply'), ('targets', 'stickers', 'personas'),
                ('training', 'models', 'debug'), ('monitor', 'agent', 'logs', 'settings')]
        for ri, keys in enumerate(rows):
            th = major_h if ri == 0 else (px(49) if ri == 3 else minor_h)
            tw = (usable-gap*(len(keys)-1))//len(keys)
            for col, key in enumerate(keys):
                tile = self.tiles[key]
                tile.major, tile.compact = ri == 0, ri == 3
                x = x0+col*(tw+gap)
                tile.configure(width=tw, height=th)
                tile.place(x=x, y=y, width=tw, height=th)
                tile.set_background(backdrop, (x, y, x+tw, y+th))
            y += th+gap
        self.canvas.create_rectangle(w*.03, h-px(80), w*.33, h-px(25), fill='#E4F2FF', outline='#C1DDF1')
        self.canvas.create_rectangle(w*.04, h-px(93), w*.12, h-px(72), fill='#347FB3', outline='')
        self.canvas.create_text(w*.08, h-px(82), text='阿洛娜', fill='white', font=font(9, 'bold'))
        self._dialog_id = self.canvas.create_text(w*.05, h-px(53), text=self._default_dialogue,
                                                  width=w*.26, anchor='w', fill='#365D7C', font=font(10))
        if self._pet_until > time.monotonic():
            self._set_closed(True)
            self.canvas.itemconfigure(self._dialog_id, text='唔…老师的手，好温暖。')

    def _motion(self, event):
        self.canvas.configure(cursor='hand2' if self._hit_head(event.x, event.y) else 'arrow')

    def _hit_head(self, x, y):
        if self._head_box is None:
            return False
        x1, y1, x2, y2 = self._head_box
        return x1 <= x <= x2 and y1 <= y <= y2

    def _click(self, event):
        if self._hit_head(event.x, event.y):
            self.canvas.focus_set()
            self.pet_head()

    def _set_closed(self, value):
        self._closed = value
        if self._portrait_id and len(self._portrait_photos) == 2:
            self.canvas.itemconfigure(self._portrait_id, image=self._portrait_photos[int(value)])

    def _schedule_blink(self):
        self._cancel('_blink_job')
        if self._active and not self._pet_until:
            self._blink_job = self.after(random.randint(3800, 7000), self._blink)

    def _blink(self):
        self._blink_job = None
        if not self._active or self._pet_until:
            return
        self._set_closed(True)
        self._open_job = self.after(160, self._finish_blink)

    def _finish_blink(self):
        self._open_job = None
        if not self._pet_until:
            self._set_closed(False)
            self._schedule_blink()

    def pet_head(self):
        if not self._active:
            return
        for name in ('_blink_job', '_open_job', '_pet_job'):
            self._cancel(name)
        self._pet_until = time.monotonic()+4.0
        self._set_closed(True)
        if self._dialog_id:
            self.canvas.itemconfigure(self._dialog_id, text='唔…老师的手，好温暖。')
        self._pet_job = self.after(4000, self._finish_pet)

    def _finish_pet(self):
        self._pet_job = None
        remaining = self._pet_until-time.monotonic()
        if remaining > 0:
            self._pet_job = self.after(max(1, int(remaining*1000)+1), self._finish_pet)
            return
        self._pet_until = 0.0
        self._set_closed(False)
        if self._dialog_id:
            self.canvas.itemconfigure(self._dialog_id, text=self._default_dialogue)
        self._schedule_blink()

    def on_show(self):
        self._active = True
        self._resize()
        self._schedule_blink()

    def on_hide(self):
        self._active = False
        for name in ('_blink_job', '_open_job', '_pet_job', '_resize_job'):
            self._cancel(name)
        self._pet_until = 0.0
        self._set_closed(False)

    def _destroy(self, event):
        if event.widget is self:
            # Child canvases have already been destroyed at this point.
            self._active = False
            for name in ('_blink_job', '_open_job', '_pet_job', '_resize_job'):
                self._cancel(name)
