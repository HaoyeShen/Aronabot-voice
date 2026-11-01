"""WecVoice-auto — send real WeChat voice messages on Windows.

The public surface is intentionally small::

    from wecvoice_auto import VoiceSender
    VoiceSender().send("老师，早上好！", chat="文件传输助手")

Under the hood the library turns text into speech with the free Edge TTS
service, plays it into a virtual audio cable, and holds WeChat's native
voice shortcut (Alt) for exactly as long as the audio lasts.
"""

from __future__ import annotations

from .config import Config
from .errors import (
    AudioDeviceError,
    AudioRoutingError,
    ChatMismatchError,
    TTSError,
    VoiceSendError,
    VoiceTooLongError,
    WeChatNotRunningError,
    WecVoiceError,
)
from .sender import SendResult, VoiceSender
from .voices import DEFAULT_VOICE, PRESETS, VoiceProfile, list_presets, resolve_voice

__version__ = "0.0.2"
__all__ = [
    "AudioDeviceError",
    "AudioRoutingError",
    "ChatMismatchError",
    "Config",
    "DEFAULT_VOICE",
    "PRESETS",
    "SendResult",
    "TTSError",
    "VoiceProfile",
    "VoiceSendError",
    "VoiceSender",
    "VoiceTooLongError",
    "WeChatNotRunningError",
    "WecVoiceError",
    "list_presets",
    "resolve_voice",
    "send_voice",
    "__version__",
]


def send_voice(text: str, **kwargs):
    """One-shot convenience wrapper around :class:`VoiceSender`."""
    return VoiceSender().send(text, **kwargs)
