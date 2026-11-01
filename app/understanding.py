"""Cheap message understanding used before local-training retrieval.

This is deliberately deterministic and local: it resolves short/pronoun-heavy
messages against the recent conversation, classifies the likely intent and
emotion, and builds a better retrieval query.  It adds no extra model call and
therefore does not double CPU/API latency or token cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LABEL = re.compile(r"^\s*\[(?:老师|阿洛娜|群友[^\]]*)\]\s*")
_WXID = re.compile(r"^\s*wxid_[A-Za-z0-9_]+:\s*")
_AT = re.compile(r"@(?:阿洛娜|Arona|ARONA)\s*", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_STOPWORDS = {
    "就是", "然后", "这个", "那个", "什么", "怎么", "为什么", "可以", "一个",
    "我们", "你们", "他们", "自己", "现在", "今天", "感觉", "觉得", "还是",
    "但是", "因为", "所以", "如果", "已经", "没有", "不是", "真的", "有点",
    "the", "and", "you", "that", "this", "with", "for", "are", "was",
}


@dataclass
class Understanding:
    text: str
    query: str
    intent: str = "chat"
    emotion: str = "neutral"
    keywords: list[str] = field(default_factory=list)
    context_used: bool = False


def _clean_content(content: object) -> str:
    text = str(content or "").strip()
    text = _LABEL.sub("", text)
    text = _WXID.sub("", text)
    text = _AT.sub("", text)
    return _SPACE.sub(" ", text).strip()


def _keywords(text: str) -> list[str]:
    values: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,}", text):
        low = token.lower()
        if low in _STOPWORDS or token in _STOPWORDS:
            continue
        if token not in values:
            values.append(token)
    return values[:10]


_TEXT_REPLY_REQUEST = re.compile(
    r"(?:不要|别|不用|不想|无需|不需要|禁止)[^，,。；;！？!?]{0,8}(?:语音|声音|朗读|念(?:一下|一遍|出来|给我听)|喵|汪|叫一声)|"
    r"(?:只|改用|改成|请用|用)文字(?:回复|回答|说|就好|就行)"
)


def _voice_request_text(content: str) -> str:
    text = _WXID.sub("", _LABEL.sub("", str(content or ""))).strip()
    if "<appmsg" in text:
        return ""
    return re.sub(r'''“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"|'[^']*' ''', '', text, flags=re.X)


def voice_reply_forbidden(content: str) -> bool:
    """An explicit text-only request also vetoes random automatic voice."""
    return bool(_TEXT_REPLY_REQUEST.search(_voice_request_text(content)))


def voice_reply_requested(content: str) -> bool:
    """Recognise explicit audible-output requests, not quoted/discussed ones."""
    text = _voice_request_text(content)
    if _TEXT_REPLY_REQUEST.search(text):
        return False
    if re.search(r"是什么意思|什么意思|有什么(?:用|好处|区别)|(?:怎么|如何)(?:实现|设置|开启)", text):
        return False
    request = re.search(
        r"(?:用|以)(?:语音|声音)(?:来)?(?:回复|回答|回我|说|读|念)|"
        r"(?:语音|声音)(?:回复|回答|回我)|(?:发|发送|回)(?:我|给我)?(?:一|个|条|段|一条|一段)?语音|"
        r"说给我听|读给我听|念给我听|(?:朗读|念一下|读一下)|"
        r"(?:想听|让我听听|给我听听)(?:你|阿洛娜)?的?声音|"
        r"(?:喵|汪|叫|喊|笑|哼)(?:一|个|两|几)声|(?:学|模仿)(?:一下)?(?:猫|狗)叫", text)
    if not request:
        return False
    prefix = text[:request.start()]
    if (re.fullmatch(r"(?:我|他|她|我们|他们|有人|别人|小明|小红)(?:刚才|刚刚|昨天|已经|曾经|之前|想|要|打算|准备|会|也|在)*", prefix.strip())
            and not request.group().startswith(("想听", "让我听听", "给我听听"))):
        return False
    reported = re.search(r"(?:昨天|刚才|之前|上次|他|她|别人|有人|小明|小红).{0,12}(?:说|问|让|要求)|转述|听说|提到", prefix)
    return not reported or bool(re.search(r"现在请|这次请|我想听|我希望", prefix[reported.end():]))


def understand_message(text: str, history: list[dict] | None = None,
                       target_type: str = "friend") -> Understanding:
    clean = _clean_content(text)
    low = clean.lower()
    recent_users: list[str] = []
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        content = _clean_content(item.get("content"))
        if content and content != clean:
            recent_users.append(content)
        if len(recent_users) >= 3:
            break
    recent_users.reverse()

    question_words = ("?", "？", "吗", "么", "什么", "怎么", "为什么", "为啥",
                      "哪", "谁", "多少", "如何", "能不能", "可不可以", "是否")
    if any(word in low for word in question_words):
        intent = "question"
    elif any(word in low for word in ("帮我", "请帮", "能不能帮", "麻烦", "帮我看看")):
        intent = "request"
    elif any(word in low for word in ("在吗", "你好", "嗨", "早上好", "晚上好", "早安", "晚安")):
        intent = "greeting"
    elif any(word in low for word in ("哈哈", "笑死", "好耶", "开心", "太好了", "喜欢")):
        intent = "positive"
    elif any(word in low for word in ("难过", "想哭", "累", "烦", "生气", "害怕", "焦虑", "不开心")):
        intent = "support"
    else:
        intent = "chat"

    if any(word in low for word in ("难过", "想哭", "累", "烦", "生气", "害怕", "焦虑", "不开心", "委屈")):
        emotion = "low"
    elif any(word in low for word in ("哈哈", "笑死", "好耶", "开心", "太好了", "喜欢", "可爱")):
        emotion = "happy"
    elif "?" in clean or "？" in clean or "吗" in clean:
        emotion = "curious"
    else:
        emotion = "neutral"

    pronouns = ("它", "他", "她", "这个", "那个", "上面", "刚才", "继续", "然后",
                "还有", "那个呢", "怎么办", "为什么")
    context_needed = (len(clean) <= 10
                      or any(word in low for word in pronouns))
    parts = [clean]
    if context_needed and recent_users:
        parts.extend(recent_users[-2:])
    query = " ".join(part for part in parts if part).strip()
    return Understanding(text=clean, query=query or clean, intent=intent,
                         emotion=emotion, keywords=_keywords(clean),
                         context_used=len(parts) > 1)
