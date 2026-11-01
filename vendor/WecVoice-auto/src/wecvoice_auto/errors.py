"""Exception types used by WecVoice-auto."""

from __future__ import annotations


class WecVoiceError(Exception):
    """Base class for every error raised by this package."""


class TTSError(WecVoiceError):
    """Text-to-speech synthesis failed."""


class AudioDeviceError(WecVoiceError):
    """A required audio endpoint (virtual cable or speaker) was not found."""


class AudioRoutingError(WecVoiceError):
    """The Windows default recording device could not be changed."""


class WeChatNotRunningError(WecVoiceError):
    """No WeChat main window could be found."""


class ChatMismatchError(WecVoiceError):
    """The currently open conversation is not the requested one."""


class VoiceTooLongError(WecVoiceError):
    """The clip would exceed WeChat's 60 second voice message limit."""


class VoiceSendError(WecVoiceError):
    """The voice message could not be sent."""
