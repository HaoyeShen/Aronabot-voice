"""WeChat-like, read-only program/Agent task conversation viewer."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from ...agent_runtime import RUNTIME
from ...ui.theme import C, font, px
from ...ui.widgets import Card, ScrollFrame, RoundedButton, BaseDialog
from .base import BasePage


class AgentChatPage(BasePage):
    key, title = 'agent', 'Agent 对话'
    subtitle = '同一任务中的程序输入、Agent 回答和工具结果，按发生顺序展示。'

    def __init__(self, parent, app):
        super().__init__(parent,app)
        self._selected = None
        self._ids = []
        self._seq = -1
        self._after_id = None
        self._cleared_at = 0
        self._follow = True
        self._details = tk.BooleanVar(value=False)
        toolbar = tk.Frame(self,bg=C['bg']); toolbar.pack(fill='x',pady=(0,px(8)))
        self.agent_select = ttk.Combobox(toolbar,state='readonly',width=23,style='Arona.TCombobox',font=font(8))
        self.agent_select.pack(side='left',padx=(0,px(8)))
        self.agent_select.bind('<<ComboboxSelected>>',lambda _:self.refresh())
        self._agent_ids = {}
        RoundedButton(toolbar,text='本机试聊',command=lambda:app.show_page('debug'),
                      width=90,height=32,style='secondary',bg=C['bg']).pack(side='right')
        RoundedButton(toolbar,text='跟随最新',command=self.follow_latest,
                      width=90,height=32,style='secondary',bg=C['bg']).pack(side='right',padx=px(4))
        RoundedButton(toolbar,text='清空视图',command=self.clear_view,
                      width=90,height=32,style='ghost',bg=C['bg']).pack(side='right',padx=px(5))
        tk.Checkbutton(toolbar,text='显示接口细节',variable=self._details,command=self.refresh,
                       bg=C['bg'],fg=C['text'],selectcolor=C['card'],font=font(8)).pack(side='left')
        self.status = tk.Label(toolbar,text='等待 Agent 任务…',bg=C['bg'],fg=C['muted'],font=font(8))
        self.status.pack(side='left',padx=px(10))
        body=tk.Frame(self,bg=C['bg']);body.pack(fill='both',expand=True)
        left=Card(body,padding=8);left.pack(side='left',fill='y',padx=(0,px(10)))
        tk.Label(left.body,text='任务会话',bg=C['card'],fg=C['text'],font=font(10,'bold')).pack(anchor='w',pady=px(5))
        self.tasks=tk.Listbox(left.body,width=25,bg=C['card'],fg=C['text'],relief='flat',
                              selectbackground=C['cyan_deep'],selectforeground='white',font=font(8),exportselection=False)
        self.tasks.pack(fill='both',expand=True)
        self.tasks.bind('<<ListboxSelect>>',self.select_task)
        right=Card(body,padding=0);right.pack(side='left',fill='both',expand=True)
        self.scroll=ScrollFrame(right.body,bg=C['card']);self.scroll.pack(fill='both',expand=True)
        self.bubbles=tk.Frame(self.scroll.inner,bg=C['card']);self.bubbles.pack(fill='both',expand=True,padx=px(12),pady=px(10))
        self.bind('<Destroy>',self._destroy,add=True)
        self._poll()

    def _destroy(self,event):
        if event.widget is self and self._after_id:
            try:self.after_cancel(self._after_id)
            except tk.TclError:pass

    def _poll(self):
        if self.winfo_ismapped():
            events=RUNTIME.snapshot()
            seq=events[-1]['seq'] if events else 0
            if seq!=self._seq:
                self.refresh(events)
        self._after_id=self.after(700,self._poll)

    def on_show(self):
        self.refresh()

    def clear_view(self):
        events=RUNTIME.snapshot()
        self._cleared_at=events[-1]['seq'] if events else 0
        self._selected=None
        self.refresh()

    def select_task(self,_event=None):
        selected=self.tasks.curselection()
        if selected and selected[0]<len(self._ids):
            self._follow = False
            self._selected=self._ids[selected[0]]
            self.refresh()

    def follow_latest(self):
        self._follow = True
        self.refresh()

    def _full(self,text):
        dialog=BaseDialog(self.winfo_toplevel(),'完整 Agent 消息','本机内容；凭据已遮蔽',width=860,height=650,icon=self.icon)
        box=tk.Text(dialog.body,wrap='word',bg=C['card'],fg=C['text'],font=font(9),padx=px(12),pady=px(10))
        bar=tk.Scrollbar(dialog.body,command=box.yview);box.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');box.pack(fill='both',expand=True)
        box.insert('1.0',text);box.configure(state='disabled')
        dialog.add_footer('关闭',dialog.close,'secondary',90)
        dialog.show()

    def refresh(self,events=None):
        events=RUNTIME.snapshot() if events is None else events
        self._seq=events[-1]['seq'] if events else 0
        events=[e for e in events if e['seq']>self._cleared_at and e['task_id']]
        agents=RUNTIME.agents()
        previous=self.agent_select.get()
        self._agent_ids={'全部聊天 Agent':''}
        self._agent_ids.update({f"{a['chat']} · {a['id'][:6]}":a['id'] for a in agents})
        self.agent_select.configure(values=list(self._agent_ids))
        self.agent_select.set(previous if previous in self._agent_ids else '全部聊天 Agent')
        selected_agent=self._agent_ids[self.agent_select.get()]
        if selected_agent:
            task_ids={e['task_id'] for e in events if e.get('agent_id')==selected_agent}
            events=[e for e in events if e['task_id'] in task_ids]
        sessions={}
        for event in events:
            sessions[event['task_id']]=event
        self._ids=list(reversed(sessions))[:100]
        if self._follow or self._selected not in self._ids:
            self._selected=self._ids[0] if self._ids else None
        self.tasks.delete(0,'end')
        for identity in self._ids:
            e=sessions[identity];stamp=datetime.fromtimestamp(e['time']).strftime('%H:%M:%S')
            self.tasks.insert('end',f"{stamp}  {e['label']}\n{e['chat'] or '本机'} · {identity[:6]}")
        if self._selected:
            i=self._ids.index(self._selected);self.tasks.selection_set(i)
        for child in self.bubbles.winfo_children():child.destroy()
        selected=[e for e in events if e['task_id']==self._selected]
        self.status.configure(text=f'{len(self._ids)} 个近期任务 · 当前 {len(selected)} 个步骤 · 仅本机可见')
        for event in selected:
            kind=event['kind']
            if not self._details.get() and kind in ('request','raw_output'):
                continue
            content=event['content']
            text=content if isinstance(content,str) else json.dumps(content,ensure_ascii=False,indent=2,default=str)
            row=tk.Frame(self.bubbles,bg=C['card']);row.pack(fill='x',pady=px(5))
            program=kind in ('program','request','tool_result')
            bg=C['cyan_deep'] if program else C['card_blue']
            fg='white' if program else C['text']
            holder=tk.Frame(row,bg=C['card']);holder.pack(side='right' if program else 'left',anchor='e' if program else 'w')
            names={'program':'程序 → Agent','request':'实际接口输入','agent':'Agent → 程序',
                   'raw_output':'接口原始回答','error':'处理失败','tool_call':'Agent → 工具','tool_result':'工具 → Agent',
                   'task_start':'任务开始','task_end':'任务结束','task_error':'任务异常'}
            meta=names.get(kind,kind)+' · '+datetime.fromtimestamp(event['time']).strftime('%H:%M:%S')
            if event.get('purpose'):meta+=' · '+event['purpose']
            if event.get('late'):meta+='（迟到结果，未用于发送）'
            tk.Label(holder,text=meta,bg=C['card'],fg=C['muted'],font=font(7),wraplength=px(480),justify='left').pack(anchor='w')
            bubble=tk.Frame(holder,bg=bg,padx=px(12),pady=px(8));bubble.pack(anchor='e' if program else 'w')
            preview=text if len(text)<=650 else text[:650]+'\n…（点击查看完整内容）'
            tk.Label(bubble,text=preview,bg=bg,fg=fg,font=font(9),wraplength=px(480),justify='left',anchor='w').pack(anchor='w')
            if len(text)>650:
                RoundedButton(holder,text='查看完整内容',command=lambda t=text:self._full(t),style='ghost',
                              width=108,height=26,bg=C['card'],font_size=7).pack(anchor='e')
        if not selected:
            tk.Label(self.bubbles,text='等待程序向 Agent 发起任务。\n可在自动调试页面进行本机试聊。',
                     bg=C['card'],fg=C['muted'],font=font(10),justify='center').pack(pady=px(45))
        self.scroll.scroll_to_bottom()
