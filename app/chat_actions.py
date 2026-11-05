"""Authenticated private-chat actions with explicit recipients and durable receipts."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager

from .agent_runtime import RUNTIME
from .chat_bubbles import split_chat_bubbles
from .model_audit import redact
from .reply_control import message_fresh, reply_current, reply_guard
from .tool_protocol import internal_output


def private_admin_identity(service, incoming):
    return bool(not incoming.is_group and incoming.direction == 'in' and not incoming.is_self
                and service._admin_allowed(incoming))


def admin_authorized(service, incoming):
    with service._admin_lock:
        session = service._admin_sessions.get(service._admin_key(incoming))
        return bool(private_admin_identity(service, incoming) and not (session or {}).get('pending_end')
                    and service.config.data.get('admin', {}).get('cross_chat_enabled', True))


def action_requested(text):
    text = str(text or '')
    if re.search(r'(?:不要|别|不许|禁止|取消|不用).{0,12}(?:发|通知|告诉|转告)', text):
        return False
    return bool(re.search(r'(?:给|向|到|在).{1,80}(?:发|发送|说|通知)|(?:告诉|通知|转告).{1,80}', text))


class ChatActionExecutor:
    def __init__(self, service, incoming):
        self.service, self.incoming = service, incoming
        with service._admin_lock:
            self.session = service._admin_sessions.get(service._admin_key(incoming))
        with self._connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS chat_action_receipts (
                action_key TEXT PRIMARY KEY, status TEXT NOT NULL, target TEXT NOT NULL,
                sent_count INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(str(self.service.memory.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def allowed(self, deadline):
        s, msg = self.service, self.incoming
        with s._admin_lock:
            same_session = s._admin_sessions.get(s._admin_key(msg)) is self.session
        return bool(same_session and admin_authorized(s, msg) and s.running
                    and s.config.bot.get('agent_enabled', True) and s.config.bot.get('auto_reply', True)
                    and not s._stop_event.is_set() and reply_current()
                    and message_fresh(msg, s.config.bot) and RUNTIME.session_current()
                    and time.monotonic() < deadline)

    def execute(self, request, remaining):
        deadline = time.monotonic() + max(0, remaining)
        s, msg = self.service, self.incoming
        if not self.allowed(deadline) or not action_requested(msg.content):
            return json.dumps({'status': 'denied', 'reason': '需要名单内管理员的当次私聊明确发送要求'}, ensure_ascii=False)
        try:
            payload = json.loads(request)
        except (ValueError, TypeError):
            return '{"status":"denied","reason":"参数必须是含chat和text的JSON字符串"}'
        if not isinstance(payload, dict) or set(payload) != {'chat', 'text'}:
            return '{"status":"denied","reason":"需要唯一的chat和明确text"}'
        chat, text = payload['chat'], payload['text']
        if (not isinstance(chat, str) or not isinstance(text, str) or not chat.strip() or not text.strip()
                or len(chat) > 100 or len(text) > 400 or internal_output(text)):
            return '{"status":"denied","reason":"目标或正文无效"}'
        chat, text = chat.strip(), text.strip()
        named = re.search(r'(?:给|向|到|在|告诉|通知|转告)\s*[「“\"《]?'
                          + re.escape(chat) + r'[」”\"》]?(?:群聊|群|里|中|内|私聊|发|说|[，,:：\s])', msg.content)
        # The model may not turn retrieved pages/history into permission, or
        # invent the outgoing body. Ask for concrete wording when unspecified.
        compact = lambda value: re.sub(r'\s+', '', value)
        password = s.config.data.get('admin', {}).get('password', '')
        if (not named or compact(text) not in compact(msg.content) or redact(text) != text
                or (password and password in text) or text.startswith('/')):
            return json.dumps({'status': 'denied', 'reason': '当前指令必须明确指定该目标及完整发送原文；不发送凭据或管理指令'}, ensure_ascii=False)
        adapter = s.adapter
        target = adapter.resolve_action_target(chat) if adapter else None
        if not target:
            return json.dumps({'status': 'denied', 'reason': '未能唯一匹配联系人或群聊，请提供完整名称或备注'}, ensure_ascii=False)
        limit = min(int(s.config.bot.get('max_reply_chars', 25)), int(s.config.bot.get('max_sentence_chars', 25)))
        bubbles = split_chat_bubbles(text, limit, int(s.config.bot.get('max_reply_bubbles', 20)))
        if not bubbles:
            return '{"status":"denied","reason":"正文无法在当前气泡限制内完整发送，请缩短原文"}'
        seed = [msg.chat, s._admin_key(msg)[1], msg.external_id or str(msg.received_at), target['identity']]
        key = hashlib.sha256(json.dumps(seed, ensure_ascii=False).encode()).hexdigest()
        with self._connection() as db:
            claimed = db.execute('INSERT OR IGNORE INTO chat_action_receipts VALUES(?,?,?,?,?)',
                                 (key, 'pending', target['chat'], 0, time.time())).rowcount
            if not claimed:
                old = db.execute('SELECT status,sent_count FROM chat_action_receipts WHERE action_key=?', (key,)).fetchone()
                return json.dumps({'status': old[0], 'sent_count': old[1], 'repeated': True,
                                   'note': '已有执行收据，不重复提交'}, ensure_ascii=False)
        sent = 0
        status = 'unknown'
        try:
            with reply_guard(lambda: self.allowed_without_reply_guard(deadline, adapter)):
                for bubble in bubbles:
                    if not reply_current():
                        status = 'partial' if sent else 'cancelled'
                        break
                    result = adapter.send_text(target['chat'], bubble)
                    if not result.ok:
                        status = 'partial' if sent else 'failed'
                        break
                    sent += 1
                    s._remember_bot_sent(target['chat'], bubble)
                    s.memory.add_assistant(target['chat'], bubble, profile_name='管理员跨聊天发送',
                                           meta={'cross_chat_action': True, 'action_key': key})
                    with self._connection() as db:
                        db.execute('UPDATE chat_action_receipts SET sent_count=?,updated=? WHERE action_key=?',
                                   (sent, time.time(), key))
                else:
                    status = 'sent'
        except Exception:
            status = 'unknown'
        with self._connection() as db:
            db.execute('UPDATE chat_action_receipts SET status=?,sent_count=?,updated=? WHERE action_key=?',
                       (status, sent, time.time(), key))
        s.emit('admin_action', {'chat': msg.chat, 'target': target['chat'], 'status': status, 'sent_count': sent})
        return json.dumps({'status': status, 'target': chat, 'sent_count': sent,
                           'note': '仅sent表示已提交全部气泡；失败或未知不得自行重发，不得声称成功'}, ensure_ascii=False)

    def allowed_without_reply_guard(self, deadline, adapter):
        # The surrounding reply_guard already checks its parent freshness guard.
        s, msg = self.service, self.incoming
        with s._admin_lock:
            same_session = s._admin_sessions.get(s._admin_key(msg)) is self.session
        return bool(same_session and admin_authorized(s, msg) and s.running and s.adapter is adapter
                    and s.config.bot.get('agent_enabled', True) and s.config.bot.get('auto_reply', True)
                    and not s._stop_event.is_set() and message_fresh(msg, s.config.bot)
                    and RUNTIME.session_current() and time.monotonic() < deadline)
