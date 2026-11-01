"""Free Edge TTS synthesis and PCM decoding.

Edge TTS returns MP3.  We decode it with ``miniaudio`` (a small, dependency
free decoder shipped as a wheel) into 48 kHz mono float32 samples, which is
what the virtual cable and WeChat's recorder expect.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .errors import TTSError
from .voices import VoiceProfile

log = logging.getLogger("wecvoice.tts")


@dataclass
class AudioClip:
    """Mono float32 PCM in [-1, 1]."""

    samples: np.ndarray
    sample_rate: int = 48000
    text: str = ""

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(len(self.samples)) / float(self.sample_rate)

    def __post_init__(self) -> None:
        data = np.asarray(self.samples, dtype=np.float32).reshape(-1)
        self.samples = np.clip(data, -1.0, 1.0)

    def to_wav(self, path: str | Path) -> Path:
        """Write a 16-bit PCM WAV file for previews and bug reports."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        pcm16 = (np.clip(self.samples, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(self.sample_rate))
            handle.writeframes(pcm16.tobytes())
        return target


def trim_silence(
    samples: np.ndarray,
    *,
    threshold: float = 0.002,
    pad_ms: float = 80.0,
    sample_rate: int = 48000,
) -> np.ndarray:
    """Trim leading/trailing silence but keep a short natural pad."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if data.size == 0:
        return data
    loud = np.flatnonzero(np.abs(data) > threshold)
    if loud.size == 0:
        return data
    pad = max(0, int(sample_rate * pad_ms / 1000.0))
    start = max(0, int(loud[0]) - pad)
    end = min(data.size, int(loud[-1]) + pad + 1)
    return data[start:end]


def _run_async(coro):
    """Run a coroutine even if the caller already owns an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Dict[str, Any] = {}

    def worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - forwarded to caller
            result["error"] = exc

    thread = threading.Thread(target=worker, name="wecvoice-edge-tts", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class EdgeTTS:
    """Free Microsoft Edge read-aloud voices, no API key required."""

    def __init__(
        self,
        *,
        sample_rate: int = 48000,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))

    def synthesize(self, text: str, profile: VoiceProfile) -> AudioClip:
        clean = (text or "").strip()
        if not clean:
            raise TTSError("Text to synthesize is empty")
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.retries + 1):
            try:
                started = time.perf_counter()
                mp3 = _run_async(self._mp3_async(clean, profile))
                clip = decode_mp3(mp3, sample_rate=self.sample_rate, text=clean)
                log.debug(
                    "Edge TTS %s synthesized %d chars, audio %.2fs, elapsed %.2fs",
                    profile.id,
                    len(clean),
                    clip.duration,
                    time.perf_counter() - started,
                )
                return clip
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                log.warning("Edge TTS attempt %d/%d failed: %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(0.8 * attempt)
        raise TTSError(f"Edge TTS synthesis failed (network required): {last_error}") from last_error

    async def _mp3_async(self, text: str, profile: VoiceProfile) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            profile.voice,
            rate=profile.rate,
            pitch=profile.pitch,
            volume=profile.volume,
        )
        chunks: List[bytes] = []

        async def collect() -> None:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])

        await asyncio.wait_for(collect(), timeout=self.timeout)
        if not chunks:
            raise TTSError("Edge TTS returned no audio (the text may be only punctuation)")
        return b"".join(chunks)


def decode_mp3(data: bytes, *, sample_rate: int = 48000, text: str = "") -> AudioClip:
    """Decode MP3 bytes to a mono float32 clip at ``sample_rate``."""
    if not data:
        raise TTSError("Audio data is empty")
    try:
        import miniaudio
    except ImportError as exc:  # pragma: no cover - packaging error
        raise TTSError("miniaudio is missing; install it with 'pip install miniaudio'") from exc

    try:
        decoded = miniaudio.decode(
            data,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
            sample_rate=int(sample_rate),
        )
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"MP3 decoding failed: {exc}") from exc

    samples = np.asarray(decoded.samples, dtype=np.float32).reshape(-1)
    samples = trim_silence(samples, sample_rate=int(sample_rate))
    return AudioClip(samples=samples, sample_rate=int(sample_rate), text=text)


async def _list_voices_async() -> List[Dict[str, Any]]:
    import edge_tts

    return await edge_tts.list_voices()


def list_edge_voices(locale_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return Edge's voice catalogue (requires network)."""
    try:
        voices = _run_async(_list_voices_async())
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"Failed to fetch the Edge voice list (network required): {exc}") from exc
    if locale_prefix:
        prefix = locale_prefix.lower()
        voices = [v for v in voices if str(v.get("Locale", "")).lower().startswith(prefix)]
    return sorted(voices, key=lambda item: (str(item.get("Locale", "")), str(item.get("ShortName", ""))))
