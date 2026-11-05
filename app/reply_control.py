"""Freshness follows a live reply through tools, model calls and transport."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import logging
import math
import re
import time

_current = ContextVar('wechat_reply_freshness', default=None)
log = logging.getLogger(__name__)


class ReplyExpired(RuntimeError):
    pass


def message_fresh(incoming, bot, now=None):
    try:
        stamp = float(incoming.received_at)
        if stamp > 1e11:stamp /= 1000
        minutes = max(1,min(1440,int(bot.get('stale_reply_minutes',10))))
        age = (time.time() if now is None else now) - stamp
        return math.isfinite(stamp) and stamp > 0 and -60 <= age <= minutes * 60
    except (ValueError,TypeError,OverflowError):
        return False


def reply_current():
    guard = _current.get()
    return guard is None or guard()


def require_current_reply():
    if not reply_current():raise ReplyExpired('原消息已超过回复时效，取消旧回复')


@contextmanager
def reply_guard(allowed):
    previous = _current.get()
    token = _current.set(lambda: (previous is None or previous()) and allowed())
    try:
        yield
    finally:
        _current.reset(token)


def fresh_reply(function):
    @wraps(function)
    def call(service, incoming, *args, **kwargs):
        guard = lambda:message_fresh(incoming,service.config.bot)
        if not guard():
            log.info('原消息已过期，保留上下文，取消生成：%s',incoming.chat)
            return
        token = _current.set(guard)
        try:return function(service,incoming,*args,**kwargs)
        finally:_current.reset(token)
    return call


def fresh_send(function):
    @wraps(function)
    def call(*args,**kwargs):
        if function.__name__ in ('send_text','send_quoted_text'):
            from .tool_protocol import internal_output
            if internal_output(args[2] if len(args)>2 else kwargs.get('text','')):
                from .wechat_adapter import SendResult
                return SendResult(False,'内部工具指令已拦截，未发送微信')
        if not reply_current():
            from .wechat_adapter import SendResult
            return SendResult(False,'原消息已过期，取消回复','expired')
        return function(*args,**kwargs)
    return call


def repeat_requested(text):
    return bool(re.search(r'再(?:说|发|讲|读|念).{0,4}(?:一遍|一次)|(?:重复|连说|连发)(?:一下|[一二两三四五十\d]+遍|[一二两三四五十\d]+次)|(?:说|读|念).{0,6}(?:两遍|二遍|三遍|\d+遍)|原样再',str(text or '')))
