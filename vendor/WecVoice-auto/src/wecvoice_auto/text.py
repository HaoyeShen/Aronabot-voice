"""Helpers that turn model output into something worth speaking.

Chat models love to leave ``[sticker:开心]`` tags, markdown asterisks and
URLs in their replies.  None of that should be read aloud, and WeChat caps a
single voice message at 60 seconds, so long replies have to be split.
"""

from __future__ import annotations

import re
from typing import Iterable, List

_STICKER_TAG = re.compile(
    r"[\[【]\s*(?:sticker|表情包?|贴纸|表情)\s*[:：]\s*([^\]】\n]{1,40})\s*[\]】]",
    re.IGNORECASE,
)
_VOICE_TAG = re.compile(r"[\[【]\s*(?:voice|语音)\s*[\]】]", re.IGNORECASE)
_MARKDOWN = re.compile(r"(\*\*|__|~~|`)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_INLINE_SPACE = re.compile(r"[ \t\u3000]+")
_MULTI_NEWLINE = re.compile(r"\n{2,}")
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;…])")


def clean_text(text: str, *, keep_urls: bool = False) -> str:
    """Strip chat-only decoration from ``text``.

    Parameters
    ----------
    text:
        Raw model output.
    keep_urls:
        Keep URLs instead of dropping them.  Dropped by default because a
        spoken URL is rarely useful and makes the clip much longer.
    """
    if text is None:
        return ""
    value = str(text)
    value = _STICKER_TAG.sub("", value)
    value = _VOICE_TAG.sub("", value)
    if not keep_urls:
        value = _URL.sub("", value)
    value = _MARKDOWN.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _INLINE_SPACE.sub(" ", value)
    value = _MULTI_NEWLINE.sub("\n", value)
    return value.strip()


def _sentences(text: str) -> List[str]:
    """Split ``text`` after Chinese/English sentence terminators."""
    chunks: List[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        piece = text[start:match.end()].strip()
        if piece:
            chunks.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        chunks.append(tail)
    if not chunks:
        return [text] if text else []
    return chunks


def split_text(text: str, max_chars: int = 120) -> List[str]:
    """Split a reply into voice-sized chunks of at most ``max_chars``.

    Splitting respects sentence boundaries where possible.  A single very long
    sentence is hard-wrapped so that one absurd reply cannot create a 90 second
    voice message.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    cleaned = clean_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: List[str] = []
    buffer = ""
    for sentence in _sentences(cleaned):
        if len(sentence) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            for start in range(0, len(sentence), max_chars):
                part = sentence[start:start + max_chars].strip()
                if part:
                    chunks.append(part)
            continue
        if not buffer:
            buffer = sentence
        elif len(buffer) + len(sentence) <= max_chars:
            buffer += sentence
        else:
            chunks.append(buffer)
            buffer = sentence
    if buffer:
        chunks.append(buffer)
    return chunks


def normalize_voice_text(text: str) -> str:
    """Collapse whitespace and drop empty lines for TTS input."""
    value = clean_text(text)
    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def estimate_duration(text: str, chars_per_second: float = 4.2) -> float:
    """Rough duration estimate used before synthesis."""
    return len(clean_text(text)) / max(0.1, chars_per_second) + 0.6


def join_chunks(chunks: Iterable[str]) -> str:
    return " ".join(chunk for chunk in chunks if chunk)
