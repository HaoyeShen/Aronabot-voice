"""Local model I/O audit, with correlation IDs and credential redaction."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import json
import logging
import re
import uuid

log = logging.getLogger(__name__)
_purpose = ContextVar('model_purpose', default='')
_call_id = ContextVar('model_call_id', default='')
_secrets: set[str] = set()


def register_secret(value):
    if value:
        _secrets.add(str(value))


def redact(value, secrets=()):
    """Copy for logging only; never modify the request or model response."""
    if isinstance(value, dict):
        return {k: ('[已隐藏]' if re.search(r'api.?key|password|密码|authorization|token_enc', str(k), re.I)
                    else ([f'[图片编码已省略，{len(str(item))}字符]' for item in v] if k == 'images' and isinstance(v, list) else redact(v, secrets))) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r'data:([^;,]+);base64,[A-Za-z0-9+/=\s]+',
                   lambda m: f'[内嵌媒体 {m[1]}，编码已省略，{len(m[0])}字符]', value)
    for secret in sorted(set(secrets) | _secrets, key=len, reverse=True):
        if secret:
            value = value.replace(secret, '[已隐藏]')
    value = re.sub(r'(?i)\b(?:sk-[\w-]{8,}|gh[pousr]_[\w]{8,}|github_pat_[\w]+|dpapi:[\w+/=]+)', '[已隐藏]', value)
    value = re.sub(r'(?i)(Bearer\s+)\S+', r'\1[已隐藏]', value)
    value = re.sub(r'''(?i)((?:api[_ -]?key|password|密码|密钥)\s*[=:：]\s*["']?)[^\s,，;；"'}]+''', r'\1[已隐藏]', value)
    return value


def audit(stage, value, *, secrets=()):
    from .agent_runtime import RUNTIME, current_task_id
    log.info('模型%s · 用途=%s · 调用=%s · 任务=%s · %s', stage, _purpose.get() or '模型接口调用',
             _call_id.get() or '独立请求', current_task_id(),
             json.dumps(redact(value, secrets), ensure_ascii=False, default=str))
    if current_task_id() and stage in ('接口输入','接口错误输出'):
        RUNTIME.note('request' if stage == '接口输入' else 'error', value,
                     purpose=_purpose.get(), secrets=secrets)
    elif current_task_id() and stage == '接口原始输出':
        # Show returned answer parts, including empty/invalid outputs, without
        # exposing provider-internal reasoning fields in the conversation UI.
        content = value
        if isinstance(value,dict):
            content = (value.get('message') or {}).get('content')
            if value.get('choices'):
                content = (value['choices'][0].get('message') or {}).get('content')
            if content is None:
                content = {'status':'未返回回答内容','fields':list(value)}
        RUNTIME.note('raw_output',content,purpose=_purpose.get(),secrets=secrets)


def audited(default_purpose):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            from .agent_runtime import RUNTIME, inside_model
            if not inside_model():
                return RUNTIME.invoke(self,fn.__name__,_purpose.get() or default_purpose,args,kwargs,
                                      lambda call_args,call_kwargs: wrapped(self,*call_args,**call_kwargs))
            purpose = _purpose.set(_purpose.get() or default_purpose)
            call_id = _call_id.set(uuid.uuid4().hex[:12])
            secrets = (getattr(self, 'api_key', ''),)
            try:
                audit('调用输入', {'method': fn.__name__, 'model': self.model,
                                  'args': args, 'kwargs': kwargs}, secrets=secrets)
                result = fn(self, *args, **kwargs)
                audit('调用输出', getattr(result, 'text', result), secrets=secrets)
                return result
            except Exception as exc:
                audit('调用失败', {'type': type(exc).__name__, 'message': str(exc)}, secrets=secrets)
                raise
            finally:
                _call_id.reset(call_id)
                _purpose.reset(purpose)
        return wrapped
    return decorate


def model_call(client, method, purpose, *args, **kwargs):
    from .reply_control import require_current_reply
    require_current_reply()
    token = _purpose.set(purpose)
    try:
        from .agent_runtime import RUNTIME
        return RUNTIME.invoke(client,method,purpose,args,kwargs,
                              lambda call_args,call_kwargs: getattr(client, method)(*call_args, **call_kwargs))
    finally:
        _purpose.reset(token)
