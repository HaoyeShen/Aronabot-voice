"""Ollama and OpenAI-compatible chat clients.

Only the Python standard library is used for HTTP.  Nothing from Codex or an
OpenAI SDK is imported, and no API key is ever written to the logs.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import socket
import threading
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .secretstore import unprotect

log = logging.getLogger(__name__)

USD_CNY_RATE = 7.2

# A local CPU model can only generate one request at a time efficiently.  The
# reply worker and the debug console otherwise compete for the same model and
# both appear to time out.  Remote API providers stay concurrent.
_LOCAL_CHAT_LOCK = threading.Lock()


class ModelError(RuntimeError):
    """A user-presentable model error."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    latency_ms: int
    usage: dict = field(default_factory=dict)


@dataclass
class HealthResult:
    ok: bool
    message: str
    detail: str = ""
    models: list[str] = field(default_factory=list)
    latency_ms: int = 0


def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def openai_chat_url(base_url: str) -> str:
    base = _base(base_url) or "https://api.openai.com/v1"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or base.endswith("/v3") or base.endswith("/openai"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def openai_models_url(base_url: str) -> str:
    base = _base(base_url)
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1") or base.endswith("/v3") or base.endswith("/openai"):
        return base + "/models"
    return base + "/v1/models"


def _headers(api_key: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "ARONA-WeChat-Assistant/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    try:
        data = json.loads(body)
        error = data.get("error", data)
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or str(error)
        else:
            message = str(error)
    except json.JSONDecodeError:
        message = body[:600] or str(exc)
    return f"HTTP {exc.code}: {message}"


class ModelClient:
    def __init__(self, profile: dict) -> None:
        self.profile = dict(profile)
        self.provider = str(profile.get("provider", "ollama")).lower()
        self.base_url = _base(str(profile.get("base_url", "")))
        self.model = str(profile.get("model", "")).strip()
        self.api_key = unprotect(str(profile.get("api_key_enc", "")))
        self.temperature = float(profile.get("temperature", 0.8))
        self.max_tokens = int(profile.get("max_tokens", 500))
        self.timeout = max(10, min(300, int(profile.get("timeout", 90))))
        self.supports_vision = bool(profile.get("supports_vision", False))
        try:
            self.num_ctx = max(1024, min(131072, int(profile.get("num_ctx", 4096))))
        except (TypeError, ValueError):
            self.num_ctx = 4096
        self.think = profile.get("think", None)

    # -- HTTP helpers -----------------------------------------------------
    def _opener(self) -> urllib.request.OpenerDirector:
        # Avoid a system HTTP proxy for local Ollama.
        if self.provider == "ollama" and self.base_url.startswith(("http://127.0.0.1",
                                                                   "http://localhost")):
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))

        # Windows/Python can retain a stale system proxy (for example a local
        # Clash/V2Ray port that is no longer listening).  Chrome and curl then
        # work while urllib fails with WinError 10061.  Only use proxies whose
        # TCP endpoint is actually reachable; otherwise go direct.
        try:
            configured = urllib.request.getproxies()
            usable = {}
            for scheme, value in configured.items():
                if scheme == "no" or not value:
                    continue
                try:
                    parsed = urllib.parse.urlparse(str(value))
                    host = parsed.hostname or ""
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    if not host:
                        continue
                    with socket.create_connection((host, port), timeout=0.35):
                        usable[scheme] = str(value)
                except OSError:
                    log.debug("忽略不可用的系统代理：%s=%s", scheme, value)
            if usable:
                return urllib.request.build_opener(urllib.request.ProxyHandler(usable))
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        except Exception as exc:
            log.debug("读取系统代理失败，改为直连：%s", exc)
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, url: str, method: str = "GET", payload: dict | None = None,
                 timeout: int | None = None) -> Any:
        if not url:
            raise ModelError("尚未填写 API 地址")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                                         headers=_headers(self.api_key))
        try:
            with self._opener().open(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
            if not body.strip():
                return {}
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            if exc.code in (401, 403):
                raise ModelError("API 鉴权失败，请检查 Key 是否正确。", detail) from exc
            if exc.code == 404:
                raise ModelError("接口或模型不存在，请检查 API 地址与模型名。", detail) from exc
            if exc.code == 429:
                raise ModelError("请求过于频繁或额度不足，请稍后再试。", detail) from exc
            raise ModelError(f"模型服务返回错误（HTTP {exc.code}）。", detail) from exc
        except socket.timeout as exc:
            raise ModelError(
                f"模型响应超时（{timeout or self.timeout}s）。本地 CPU 生成较慢，"
                "请降低最大回复 tokens、关闭其他占内存程序，或稍后重试。",
                f"timeout={timeout or self.timeout}s",
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise ModelError("无法连接模型服务，请检查服务地址与网络。", str(reason)) from exc
        except (json.JSONDecodeError, ssl.SSLError) as exc:
            raise ModelError("模型服务返回了无法解析的数据。", str(exc)) from exc

    def _chat_endpoint(self) -> str:
        if self.provider == "ollama":
            return self.base_url.rstrip("/") + "/api/chat"
        return openai_chat_url(self.base_url)

    def _list_endpoint(self) -> str:
        if self.provider == "ollama":
            return self.base_url.rstrip("/") + "/api/tags"
        return openai_models_url(self.base_url)

    # -- Public API -------------------------------------------------------
    def chat(self, messages: list[dict], system: str = "") -> LLMResult:
        if self.provider == "mock":
            return self._mock_chat(messages)
        if not self.model:
            raise ModelError("尚未选择模型")
        started = time.perf_counter()
        if self.provider == "ollama":
            log.info("本地模型请求 · %s · messages=%d · system=%d chars · num_ctx=%d · num_predict=%d · timeout=%ds",
                     self.model, len(messages), len(system or ""), self.num_ctx,
                     self.max_tokens, self.timeout)
            api_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
            # Qwen3's Ollama template injects an opening <think> tag for the
            # final assistant turn.  A prefilled assistant turn lets the model
            # finish with a normal message instead of returning only a long
            # reasoning trace.  This prefill is request-only and never stored.
            if ("qwen3" in self.model.lower() and self.think is not False
                    and (not api_messages or api_messages[-1].get("role") != "assistant")):
                api_messages.append({"role": "assistant", "content": " "})
            payload = {
                "model": self.model,
                "messages": api_messages,
                "stream": False,
                "options": {"temperature": self.temperature, "num_predict": self.max_tokens,
                            "num_ctx": self.num_ctx},
            }
            if isinstance(self.think, bool):
                payload["think"] = self.think
            with _LOCAL_CHAT_LOCK:
                data, attempts = self._with_retry(self._chat_endpoint(), payload)
            message = data.get("message") or {}
            text = message.get("content") if isinstance(message, dict) else ""
            thinking = message.get("thinking", "") if isinstance(message, dict) else ""
            if not text and thinking:
                raise ModelError(
                    "模型只输出了思考过程，没有给出最终回复。",
                    "Qwen3 会先消耗 token 思考；请提高“最大回复 tokens”，"
                    "或在模型配置里关闭思考模式，或换用非思考模型。",
                )
            usage = {
                "prompt": data.get("prompt_eval_count", 0),
                "completion": data.get("eval_count", 0),
            }
        elif self.provider in ("openai", "openai_compatible", "api"):
            payload_messages = ([{"role": "system", "content": system}] if system else []) + messages
            payload = {
                "model": self.model,
                "messages": payload_messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
            }
            data, attempts = self._with_retry(self._chat_endpoint(), payload)
            choices = data.get("choices") or []
            if not choices:
                raise ModelError("模型没有返回回复内容。", str(data)[:500])
            content = (choices[0].get("message") or {}).get("content", "")
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                                  for part in content)
            text = content or ""
            usage_raw = data.get("usage") or {}
            usage = {"prompt": usage_raw.get("prompt_tokens", 0),
                     "completion": usage_raw.get("completion_tokens", 0)}
        else:
            raise ModelError(f"不支持的模型提供方：{self.provider}")

        text = (text or "").strip()
        if not text:
            raise ModelError("模型返回了空回复。", "empty content")
        latency = int((time.perf_counter() - started) * 1000)
        log.info("模型回复成功 · %s · %d ms · 尝试 %d 次", self.model, latency, attempts)
        return LLMResult(text=text, model=self.model, provider=self.provider,
                         latency_ms=latency, usage=usage)

    def _with_retry(self, url: str, payload: dict) -> tuple[Any, int]:
        last: ModelError | None = None
        for attempt in range(1, 3):
            try:
                return self._request(url, "POST", payload), attempt
            except ModelError as exc:
                last = exc
                # Do not retry local model timeouts: Qwen3 on CPU can take
                # minutes, and a second attempt would double the user's wait.
                retryable = self.provider != "ollama" and any(
                    key in exc.detail for key in
                    ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
                     "Connection", "连接")
                )
                if attempt >= 2 or not retryable:
                    raise
                time.sleep(1.2 * attempt)
        raise last or ModelError("模型请求失败")

    def _probe_chat(self, timeout: int = 20) -> str:
        """Make a one-token chat request to verify the model alias."""
        if self.provider == "ollama":
            endpoint = self._chat_endpoint()
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 1, "num_ctx": 512},
            }
            if isinstance(self.think, bool):
                payload["think"] = self.think
        else:
            endpoint = self._chat_endpoint()
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "temperature": 0,
                "max_tokens": 1,
                "stream": False,
            }
        data = self._request(endpoint, "POST", payload, timeout=timeout)
        if not isinstance(data, dict):
            raise ModelError("模型聊天接口返回了异常数据")
        message = data.get("message")
        if isinstance(message, dict) and message.get("content") is not None:
            return str(message.get("content") or "")
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            content = (choices[0].get("message") or {}).get("content", "")
            return str(content or "")
        raise ModelError("模型聊天接口没有返回内容", str(data)[:400])

    def deepseek_balance(self, timeout: int = 15) -> dict:
        """Read DeepSeek account balance and convert it to CNY for display."""
        if self.provider != "openai":
            raise ModelError("只有 OpenAI 兼容 API 配置可以查询 DeepSeek 余额")
        parsed = urllib.parse.urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if "deepseek.com" not in host:
            raise ModelError("当前 API 地址不是 DeepSeek，无法查询余额")
        url = f"{parsed.scheme or 'https'}://{parsed.netloc}/user/balance"
        data = self._request(url, "GET", timeout=timeout)
        infos = data.get("balance_infos") if isinstance(data, dict) else None
        if not isinstance(infos, list) or not infos:
            raise ModelError("DeepSeek 没有返回余额信息", str(data)[:500])
        total_cny = 0.0
        parts = []
        for info in infos:
            if not isinstance(info, dict):
                continue
            currency = str(info.get("currency") or "CNY").upper()
            try:
                amount = float(info.get("total_balance") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if currency == "CNY":
                cny = amount
                parts.append(f"¥{amount:.2f}")
            elif currency == "USD":
                cny = amount * USD_CNY_RATE
                parts.append(f"${amount:.2f} ≈ ¥{cny:.2f}")
            else:
                cny = amount
                parts.append(f"{amount:.2f} {currency}")
            total_cny += cny
        if not parts:
            raise ModelError("DeepSeek 余额格式无法识别")
        return {
            "ok": True,
            "available": bool(data.get("is_available", True)),
            "currency": "CNY",
            "total_cny": round(total_cny, 2),
            "text": " · ".join(parts),
            "note": f"USD 按 1 USD = {USD_CNY_RATE:.2f} CNY 换算",
        }

    def describe_image(self, path, prompt: str = "", timeout: int = 90) -> LLMResult:
        """Ask a vision-capable model to describe a local image."""
        if not self.supports_vision:
            raise ModelError("当前模型未启用图片理解",
                             "请在模型配置中勾选“支持图片理解（视觉模型）”")
        from PIL import Image, ImageOps

        image = Image.open(path)
        try:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=85)
        finally:
            try:
                image.close()
            except Exception:
                pass
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        prompt = prompt or ("请用中文简洁描述这张图片里看得见的内容，30字以内；"
                            "只描述确定可见的东西，不猜背景，不编造。")
        started = time.perf_counter()
        if self.provider == "ollama":
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt, "images": [encoded]}],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 100, "num_ctx": 4096},
            }
            with _LOCAL_CHAT_LOCK:
                data, attempts = self._with_retry(self._chat_endpoint(), payload)
            message = data.get("message") or {}
            text = message.get("content", "") if isinstance(message, dict) else ""
            usage = {"prompt": data.get("prompt_eval_count", 0),
                     "completion": data.get("eval_count", 0)}
        elif self.provider in ("openai", "openai_compatible", "api"):
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64," + encoded}},
                ]}],
                "temperature": 0.3,
                "max_tokens": 120,
                "stream": False,
            }
            data, attempts = self._with_retry(self._chat_endpoint(), payload)
            choices = data.get("choices") or []
            if not choices:
                raise ModelError("图片理解没有返回内容", str(data)[:500])
            content = (choices[0].get("message") or {}).get("content", "")
            text = content if isinstance(content, str) else ""
            raw_usage = data.get("usage") or {}
            usage = {"prompt": raw_usage.get("prompt_tokens", 0),
                     "completion": raw_usage.get("completion_tokens", 0)}
        else:
            raise ModelError("当前提供方不支持图片理解")
        text = (text or "").strip()
        if not text:
            raise ModelError("视觉模型没有返回图片描述")
        latency = int((time.perf_counter() - started) * 1000)
        log.info("图片理解成功 · %s · %d ms · %s", self.model, latency, text[:80])
        return LLMResult(text=text, model=self.model, provider=self.provider,
                         latency_ms=latency, usage=usage)

    def health(self, timeout: int = 12) -> HealthResult:
        if self.provider == "mock":
            return HealthResult(True, "离线演示模型可用（仅用于界面测试）", models=[self.model])
        if not self.base_url:
            return HealthResult(False, "尚未填写 API 地址")
        started = time.perf_counter()
        try:
            data = self._request(self._list_endpoint(), "GET", timeout=timeout)
            latency = int((time.perf_counter() - started) * 1000)
            models = self._extract_models(data)
            if self.model and models and self.model not in models:
                # /models is advisory.  Some OpenAI-compatible gateways keep
                # aliases such as deepseek-chat working while only listing
                # newer names.  Verify the actual chat endpoint before
                # declaring the profile broken.
                if self.provider != "ollama":
                    try:
                        self._probe_chat(timeout=min(30, max(8, timeout)))
                        latency = int((time.perf_counter() - started) * 1000)
                        return HealthResult(
                            True,
                            f"模型服务连接正常 · {latency} ms · 聊天接口已验证",
                            detail=f"模型列表未包含 “{self.model}”，但该别名可以正常回复。"
                                   "可用列表：" + "、".join(models[:8]),
                            models=models, latency_ms=latency,
                        )
                    except Exception as probe_exc:
                        detail = str(getattr(probe_exc, "detail", "") or probe_exc)[:500]
                        return HealthResult(
                            False,
                            f"模型 “{self.model}” 不在列表中，聊天接口也不可用",
                            detail=("可用模型：" + "、".join(models[:8]) +
                                    ((" · " + detail) if detail else "")),
                            models=models, latency_ms=latency,
                        )
                return HealthResult(
                    False,
                    f"服务可访问，但模型 “{self.model}” 不在列表中",
                    detail="可用模型：" + "、".join(models[:8]),
                    models=models, latency_ms=latency,
                )
            suffix = f" · {len(models)} 个模型" if models else ""
            return HealthResult(True, f"模型服务连接正常 · {latency} ms{suffix}",
                                models=models, latency_ms=latency)
        except ModelError as exc:
            # A gateway may not expose /models at all while chat works.
            if self.provider != "ollama" and self.model:
                try:
                    self._probe_chat(timeout=min(30, max(8, timeout)))
                    latency = int((time.perf_counter() - started) * 1000)
                    return HealthResult(True, f"模型服务连接正常 · {latency} ms · 聊天接口已验证",
                                        detail="模型列表不可用，已直接验证聊天接口。",
                                        latency_ms=latency)
                except Exception:
                    pass
            return HealthResult(False, exc.message, detail=exc.detail)
        except Exception as exc:  # defensive: health checks must never crash the UI
            return HealthResult(False, "模型健康检查失败", detail=str(exc))

    @staticmethod
    def _extract_models(data: Any) -> list[str]:
        if not isinstance(data, dict):
            return []
        if isinstance(data.get("models"), list):
            result = []
            for item in data["models"]:
                if isinstance(item, dict):
                    result.append(str(item.get("name") or item.get("model") or ""))
            return [m for m in result if m]
        if isinstance(data.get("data"), list):
            return [str(item.get("id")) for item in data["data"]
                    if isinstance(item, dict) and item.get("id")]
        return []

    def list_models(self, timeout: int = 15) -> list[str]:
        if self.provider == "mock":
            return [self.model]
        data = self._request(self._list_endpoint(), "GET", timeout=timeout)
        return self._extract_models(data)

    # -- Offline demo -----------------------------------------------------
    def _mock_chat(self, messages: list[dict]) -> LLMResult:
        started = time.perf_counter()
        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = str(message.get("content", ""))
                break
        history = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
        text = self._mock_reply(user_text, history)
        latency = int((time.perf_counter() - started) * 1000) + 180
        time.sleep(0.18)
        return LLMResult(text=text, model="arona-demo", provider="mock",
                         latency_ms=latency, usage={"prompt": len(user_text), "completion": len(text)})

    @staticmethod
    def _mock_reply(text: str, history: list[str]) -> str:
        import re
        raw = text.strip()
        # Strip the "[sender] " prefix used for group context.
        clean = re.sub(r"^\[[^\]]+\]\s*", "", raw)
        if any(w in clean for w in ("在吗", "你好", "早上好", "晚上好", "嗨", "hello", "hi")):
            return "老师，阿洛娜在哦。今天也要一起加油吗？嘿嘿。"
        if "口令" in clean and any(w in clean for w in ("什么", "刚才", "记住的")):
            for previous in reversed(history[:-1]):
                match = re.search(r"口令\s*[:：]\s*([^，。！？\n]{1,30})", previous)
                if match:
                    return f"是{match.group(1).strip()}呀，阿洛娜有好好记住。"
        if "记住" in clean and "口令" in clean:
            match = re.search(r"口令\s*[:：]\s*([^，。！？\n]{1,30})", clean)
            if match:
                return f"记住啦，口令是{match.group(1).strip()}。"
        if any(w in clean for w in ("累", "难受", "不开心", "压力", "烦")):
            return "老师辛苦啦，先休息一下。 [sticker:小小委屈]"
        if any(w in clean for w in ("表情", "贴纸")):
            return "给老师一张！今天也要开开心心的。 [sticker:开心转圈]"
        if "喜欢" in clean and ("什么" in clean or "吗" in clean):
            found = []
            for previous in history[:-1]:
                match = re.search(r"我(?:最)?(?:喜欢|爱)(?:喝|吃)?([^，。！？\n]{1,24})", previous)
                if match:
                    found.append(match.group(1).strip())
            if found:
                return f"老师刚才说过，最喜欢的是{found[-1]}呀。阿洛娜有认真记住哦。"
        if any(w in clean for w in ("你是谁", "介绍", "名字")):
            return "我是阿洛娜，什亭之匣的主控 AI，也是老师的导航员。请多指教，老师！"
        if any(w in clean for w in ("晚安", "睡觉", "困")):
            return "老师晚安。今天已经很努力了，剩下的交给明天吧。阿洛娜会守着什亭之匣的。 [sticker:困困打盹]"
        return "老师，阿洛娜在听，能再多说一点吗？"


def make_client(profile: dict) -> ModelClient:
    return ModelClient(profile)
