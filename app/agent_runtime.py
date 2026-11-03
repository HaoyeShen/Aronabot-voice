"""Shared Agent task scopes and a bounded, thread-safe conversation timeline.

One event owns one task across filters, models, tools and delivery. Task scopes
propagate through bounded workers; groups and subsequent events remain isolated.
"""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import json
import threading
import time
import uuid
from dataclasses import dataclass, field

_task = ContextVar('arona_agent_task', default=None)
_model_step = ContextVar('arona_agent_model_step', default='')


@dataclass
class ChatAgent:
    """Persistent in-process owner for one canonical WeChat conversation."""
    key: str
    chat: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tasks: deque = field(default_factory=lambda: deque(maxlen=200))
    updated: float = field(default_factory=time.time)


class AgentRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._events = deque()
        self._seq = self._bytes = 0
        self._agents = {}
        self._aliases = {}

    def agent_for(self, chat='', identity=''):
        identity = identity if isinstance(identity,str) else ''
        with self._lock:
            key = identity or self._aliases.get(chat) or ('chat:' + chat if chat else 'local')
            agent = self._agents.get(key)
            if agent is None and identity:
                old = self._agents.get(self._aliases.get(chat))
                if old is not None and old.key.startswith('chat:'):
                    previous = old.key
                    self._agents.pop(previous,None)
                    old.key = key
                    for alias, target in list(self._aliases.items()):
                        if target == previous:
                            self._aliases[alias] = key
                    agent = old
            if agent is None:
                agent = ChatAgent(key,chat or '本机任务')
            self._agents[key] = agent
            if chat:
                agent.chat = chat
                self._aliases[chat] = key
            agent.updated = time.time()
            return agent

    def agents(self):
        with self._lock:
            return [{'id':a.id,'chat':a.chat,'updated':a.updated,'tasks':len(a.tasks)}
                    for a in sorted(self._agents.values(),key=lambda a:a.updated,reverse=True)]

    def note(self, kind, content, *, purpose='', step='', secrets=()):
        from .model_audit import redact
        task = _task.get() or {}
        safe = json.loads(json.dumps(redact(content, secrets), ensure_ascii=False, default=str))
        event = {'kind':kind, 'task_id':task.get('id',''), 'label':task.get('label',purpose),
                 'agent_id':getattr(task.get('agent'),'id',''),
                 'chat':task.get('chat',''), 'purpose':purpose, 'step':step or _model_step.get(),
                 'time':time.time(), 'content':safe, 'late':bool(task.get('closed'))}
        size = len(json.dumps(event,ensure_ascii=False,default=str).encode())
        with self._lock:
            self._seq += 1
            event['seq'] = self._seq
            self._events.append((event,size)); self._bytes += size
            while len(self._events) > 1 and (len(self._events) > 2000 or self._bytes > 6*1024*1024):
                _, old = self._events.popleft(); self._bytes -= old
        return event

    def snapshot(self):
        with self._lock:
            return [dict(e) for e, _ in self._events]

    def bind(self, chat, identity=''):
        state = _task.get()
        if state is not None and chat:
            agent = self.agent_for(chat,identity)
            old = state.get('agent')
            if old is not None and old.key != 'local' and old.id != agent.id:
                raise ValueError('同一 Agent 任务不能跨聊天执行')
            state['chat'] = str(chat)
            state['agent'] = agent
            with self._lock:
                if state['id'] not in agent.tasks:
                    agent.tasks.append(state['id'])

    @contextmanager
    def task(self, label, chat='', identity=''):
        if _task.get() is not None:
            yield _task.get()
            return
        agent = self.agent_for(chat,identity)
        state = {'id':uuid.uuid4().hex,'label':label,'chat':chat,'agent':agent}
        with self._lock:
            agent.tasks.append(state['id'])
        token = _task.set(state)
        self.note('task_start', '任务开始')
        try:
            yield state
        except Exception as exc:
            self.note('task_error', {'type':type(exc).__name__})
            raise
        finally:
            self.note('task_end', '任务处理结束')
            state['closed'] = True
            _task.reset(token)

    def invoke(self, client, method, purpose, args, kwargs, execute):
        with self.task(purpose):
            step = uuid.uuid4().hex[:12]
            token = _model_step.set(step)
            secret = getattr(client,'api_key','')
            secrets = (secret,) if isinstance(secret,str) else ()
            self.note('program', {'method':method,'model':getattr(client,'model',''),
                                  'args':args,'kwargs':kwargs},purpose=purpose,secrets=secrets)
            try:
                result = execute()
                self.note('agent',getattr(result,'text',result),purpose=purpose,secrets=secrets)
                return result
            except Exception as exc:
                self.note('error',{'type':type(exc).__name__,'message':str(exc)},purpose=purpose,secrets=secrets)
                raise
            finally:
                _model_step.reset(token)


RUNTIME = AgentRuntime()


def current_task_id():
    return (_task.get() or {}).get('id','')


def inside_model():
    return bool(_model_step.get())


def task_entry(label):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            first = args[1] if len(args) > 1 else None
            chat = first if isinstance(first,str) else str(getattr(first,'chat','') or '')
            if fn.__name__ == 'debug_chat':
                chat = str(kwargs.get('chat_name') or '调试台')
            elif fn.__name__ == 'synthesize':
                chat = ''
            target = kwargs.get('target')
            if isinstance(target,dict):
                chat = str(target.get('name') or chat)
            adapter = getattr(args[0],'_adapter',None) if args else None
            identity = ''
            if adapter is not None and chat:
                try:
                    canonical = adapter.canonical_chat(chat)
                    if isinstance(canonical,str) and canonical.strip():
                        chat = canonical
                    candidate = adapter.chat_identity(chat)
                    identity = candidate if isinstance(candidate,str) else ''
                except Exception:
                    pass
            with RUNTIME.task(label,chat,identity):
                return fn(*args,**kwargs)
        return wrapped
    return decorate
