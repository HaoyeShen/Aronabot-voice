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
