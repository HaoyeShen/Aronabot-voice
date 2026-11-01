"""Audio device discovery and PCM playback.

Playback targets the *render* side of a virtual cable (``CABLE Input`` for
VB-Audio Cable), while WeChat records from the *capture* side
(``CABLE Output``).  Keeping the two names straight is the usual source of
"no sound" reports, so the defaults in :class:`Config` encode them explicitly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from .errors import AudioDeviceError
from .tts import AudioClip

log = logging.getLogger("wecvoice.audio")

DeviceRef = Union[int, str, None]


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    kind: str
    channels: int
    sample_rate: float
    hostapi: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "kind": self.kind,
            "channels": self.channels,
            "default_samplerate": self.sample_rate,
            "hostapi": self.hostapi,
        }


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - platform packaging
        raise AudioDeviceError(f"Failed to load sounddevice: {exc}") from exc
    return sd


def list_devices(kind: Optional[str] = None) -> List[DeviceInfo]:
    """List input/output endpoints visible to PortAudio."""
    sd = _sounddevice()
    result: List[DeviceInfo] = []
    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioDeviceError(f"Failed to enumerate audio devices: {exc}") from exc
    for index, item in enumerate(devices):
        inputs = int(item.get("max_input_channels", 0))
        outputs = int(item.get("max_output_channels", 0))
        if kind in (None, "input") and inputs > 0:
            result.append(
                DeviceInfo(index, str(item.get("name", "")), "input", inputs,
                           float(item.get("default_samplerate", 0)), int(item.get("hostapi", 0)))
            )
        if kind in (None, "output") and outputs > 0:
            result.append(
                DeviceInfo(index, str(item.get("name", "")), "output", outputs,
                           float(item.get("default_samplerate", 0)), int(item.get("hostapi", 0)))
            )
    return result


def _match_score(name: str, needle: str) -> int:
    name_low = name.lower()
    needle_low = needle.lower()
    if name_low == needle_low:
        return 0
    if name_low.startswith(needle_low):
        return 1
    if needle_low in name_low:
        return 2
    return 99


def find_device(needle: str, kind: str) -> DeviceInfo:
    """Find the best device whose name contains ``needle``."""
    if kind not in ("input", "output"):
        raise ValueError("kind must be 'input' or 'output'")
    candidates = [item for item in list_devices(kind) if _match_score(item.name, needle) < 99]
    if not candidates:
        available = "; ".join(f"[{d.index}] {d.name}" for d in list_devices(kind)) or "(none)"
        raise AudioDeviceError(
            f"{'Input' if kind == 'input' else 'Output'} device {needle!r} not found. "
            f"Available: {available}\n"
            "Hint: install VB-Audio Virtual Cable and confirm the device name in Windows sound settings."
        )
    candidates.sort(key=lambda item: (_match_score(item.name, needle), len(item.name), item.index))
    return candidates[0]


def resolve_output(ref: DeviceRef = None) -> DeviceInfo:
    sd = _sounddevice()
    if isinstance(ref, int):
        info = sd.query_devices(ref)
        if int(info.get("max_output_channels", 0)) <= 0:
            raise AudioDeviceError(f"Device {ref} is not an output device")
        return DeviceInfo(ref, str(info.get("name", "")), "output",
                          int(info["max_output_channels"]), float(info.get("default_samplerate", 0)),
                          int(info.get("hostapi", 0)))
    if isinstance(ref, str):
        return find_device(ref, "output")
    try:
        info = sd.query_devices(kind="output")
    except Exception as exc:  # noqa: BLE001
        raise AudioDeviceError(f"No default output device: {exc}") from exc
    return DeviceInfo(-1, str(info.get("name", "")), "output",
                      int(info.get("max_output_channels", 0)),
                      float(info.get("default_samplerate", 0)),
                      int(info.get("hostapi", 0)))


def default_input() -> Optional[DeviceInfo]:
    sd = _sounddevice()
    try:
        info = sd.query_devices(kind="input")
    except Exception:  # noqa: BLE001
        return None
    return DeviceInfo(-1, str(info.get("name", "")), "input",
                      int(info.get("max_input_channels", 0)),
                      float(info.get("default_samplerate", 0)),
                      int(info.get("hostapi", 0)))


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if source_rate == target_rate or data.size == 0:
        return data
    duration = data.size / float(source_rate)
    target_size = max(1, int(round(duration * target_rate)))
    source_positions = np.arange(data.size, dtype=np.float64) / float(source_rate)
    target_positions = np.arange(target_size, dtype=np.float64) / float(target_rate)
    return np.interp(target_positions, source_positions, data).astype(np.float32)


def _prepare_channels(samples: np.ndarray, channels: int) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    if channels <= 1:
        return data
    return np.column_stack([data] * channels)


def play_clip(
    clip: AudioClip,
    device: DeviceRef = None,
    *,
    gain: float = 1.0,
    blocking: bool = True,
) -> None:
    """Play ``clip`` to ``device`` (a name, index, or the system default)."""
    sd = _sounddevice()
    target = resolve_output(device)
    data = np.clip(np.asarray(clip.samples, dtype=np.float32) * float(gain), -1.0, 1.0)
    channels = max(1, min(2, int(target.channels) or 1))
    try:
        sd.play(
            _prepare_channels(data, channels),
            int(clip.sample_rate),
            device=target.index if target.index >= 0 else None,
            blocking=blocking,
        )
    except Exception as first_error:  # noqa: BLE001 - retry at device rate
        fallback_rate = int(round(target.sample_rate)) or 44100
        if fallback_rate == int(clip.sample_rate):
            raise AudioDeviceError(
                f"Playback to {target.name!r} failed: {first_error}\n"
                "Make sure the device is not exclusive and its sample rate is 48000 Hz."
            ) from first_error
        log.warning("Device %s rejected %d Hz; retrying at %d Hz", target.name, clip.sample_rate, fallback_rate)
        resampled = resample_linear(data, int(clip.sample_rate), fallback_rate)
        try:
            sd.play(
                _prepare_channels(resampled, channels),
                fallback_rate,
                device=target.index if target.index >= 0 else None,
                blocking=blocking,
            )
        except Exception as second_error:  # noqa: BLE001
            raise AudioDeviceError(
                f"Playback to {target.name!r} failed: {second_error}\n"
                "Make sure the device exists, is not exclusive, and supports its default sample rate."
            ) from second_error
    finally:
        try:
            sd.stop()
        except Exception:  # noqa: BLE001
            pass


def probe_device(name: str, kind: str) -> Dict[str, Any]:
    info = find_device(name, kind)
    return info.to_dict()


def format_device_table(kind: Optional[str] = None) -> str:
    rows = ["idx  kind    ch  rate     name"]
    for item in list_devices(kind):
        rows.append(
            f"{item.index:>3}  {item.kind:<6}  {item.channels:>2}  {int(item.sample_rate):>6}  {item.name}"
        )
    return "\n".join(rows)
