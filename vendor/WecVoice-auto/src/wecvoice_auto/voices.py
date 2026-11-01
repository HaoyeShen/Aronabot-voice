"""Voice presets and Edge TTS parameter handling.

The default preset is ``arona``: a free Microsoft Edge voice tuned to sound
like a cheerful, soft-spoken anime assistant.  It is an *approximation* of
Arona from Blue Archive, not an official voice model and not a clone of the
character's voice actress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional

DEFAULT_VOICE = "arona"

_RATE_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?%$")
_PITCH_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?Hz$")
_VOLUME_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?%$")
_EDGE_NAME_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,}(?:-[A-Za-z]+)*-[A-Za-z0-9]+Neural$")


@dataclass(frozen=True)
class VoiceProfile:
    """A complete recipe for one voice.

    ``voice`` is the Edge TTS short name (for example
    ``zh-CN-XiaoyiNeural``).  ``rate``/``pitch``/``volume`` use the same
    ``+10%`` / ``-5Hz`` syntax as Edge TTS.
    """

    id: str
    label: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    language: str = "zh-CN"
    description: str = ""
    engine: str = "edge"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "language": self.language,
            "description": self.description,
            "engine": self.engine,
        }


PRESETS: Dict[str, VoiceProfile] = {
    "arona": VoiceProfile(
        id="arona",
        label="Arona (approx.)",
        voice="zh-CN-XiaoyiNeural",
        rate="+5%",
        pitch="+12Hz",
        volume="+0%",
        description="Bright, slightly pitched-up girl voice; an Arona-style approximation, not the official voice.",
    ),
    "arona-happy": VoiceProfile(
        id="arona-happy",
        label="Arona · Energetic",
        voice="zh-CN-XiaoyiNeural",
        rate="+12%",
        pitch="+20Hz",
        volume="+5%",
        description="Faster and higher-pitched energetic variant.",
    ),
    "arona-soft": VoiceProfile(
        id="arona-soft",
        label="Arona · Soft",
        voice="zh-CN-XiaoyiNeural",
        rate="-4%",
        pitch="+8Hz",
        volume="-5%",
        description="Slower, quieter night variant.",
    ),
    "plana": VoiceProfile(
        id="plana",
        label="Plana (approx.)",
        voice="zh-CN-XiaoxiaoNeural",
        rate="-5%",
        pitch="+0Hz",
        description="Calm, soft delivery for a cooler character.",
    ),
    "xiaoyi": VoiceProfile(
        id="xiaoyi",
        label="Xiaoyi · Girl",
        voice="zh-CN-XiaoyiNeural",
        description="Edge's native young girl voice, no tuning.",
    ),
    "xiaoxiao": VoiceProfile(
        id="xiaoxiao",
        label="Xiaoxiao · Gentle",
        voice="zh-CN-XiaoxiaoNeural",
        rate="-2%",
        description="Edge's native gentle female voice.",
    ),
    "yunxi": VoiceProfile(
        id="yunxi",
        label="Yunxi · Boy",
        voice="zh-CN-YunxiNeural",
        language="zh-CN",
        description="Young male voice.",
    ),
    "yunjian": VoiceProfile(
        id="yunjian",
        label="Yunjian · Narrator",
        voice="zh-CN-YunjianNeural",
        description="Deep male voice for narration.",
    ),
    "xiaobei": VoiceProfile(
        id="xiaobei",
        label="Xiaobei · Northeastern",
        voice="zh-CN-liaoning-XiaobeiNeural",
        description="Female voice with a Northeastern accent.",
    ),
    "xiaoni": VoiceProfile(
        id="xiaoni",
        label="Xiaoni · Shaanxi",
        voice="zh-CN-shaanxi-XiaoniNeural",
        description="Female voice with a Shaanxi accent.",
    ),
    "nanami": VoiceProfile(
        id="nanami",
        label="Nanami · Japanese",
        voice="ja-JP-NanamiNeural",
        language="ja-JP",
        description="Japanese female voice; try it for Japanese lines.",
    ),
    "arona-jp": VoiceProfile(
        id="arona-jp",
        label="Arona · Japanese",
        voice="ja-JP-NanamiNeural",
        rate="+3%",
        pitch="+10Hz",
        language="ja-JP",
        description="Japanese girl voice, style approximation.",
    ),
}


def list_presets(language: Optional[str] = None) -> List[VoiceProfile]:
    """Return presets in declaration order, optionally filtered by language."""
    values = list(PRESETS.values())
    if language:
        values = [item for item in values if item.language == language]
    return values


def _validate(name: str, value: str, pattern: re.Pattern) -> str:
    text = str(value).strip()
    if not pattern.match(text):
        raise ValueError(
            f"Invalid {name} format: {text!r}; example: rate='+10%', pitch='+5Hz', volume='+0%'"
        )
    return text


def resolve_voice(
    voice: Optional[str] = None,
    *,
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
    volume: Optional[str] = None,
) -> VoiceProfile:
    """Resolve a preset id, or a raw Edge voice name, into a profile.

    Unknown preset ids that *look* like Edge voice names are accepted, so a
    user can pass ``zh-CN-XiaoxiaoNeural`` directly without editing the
    package.
    """
    reference = (voice or DEFAULT_VOICE).strip()
    if not reference:
        reference = DEFAULT_VOICE

    preset = PRESETS.get(reference)
    if preset is not None:
        profile = preset
    elif _EDGE_NAME_RE.match(reference):
        profile = VoiceProfile(
            id=reference,
            label=reference,
            voice=reference,
            description="Custom Edge TTS voice.",
        )
    else:
        known = "、".join(PRESETS)
        raise ValueError(f"Unknown voice: {reference!r}. Built-in voices: {known}")

    changes = {}
    if rate is not None:
        changes["rate"] = _validate("rate", rate, _RATE_RE)
    if pitch is not None:
        changes["pitch"] = _validate("pitch", pitch, _PITCH_RE)
    if volume is not None:
        changes["volume"] = _validate("volume", volume, _VOLUME_RE)
    if changes:
        profile = replace(profile, **changes)
    return profile


def iter_presets() -> Iterable[VoiceProfile]:
    return PRESETS.values()
