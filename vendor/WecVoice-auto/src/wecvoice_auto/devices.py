"""Windows default-recording-device switching via IPolicyConfig.

Windows has no supported public API for "make this microphone the default".
The undocumented ``IPolicyConfig`` COM interface used by the Sound control
panel is stable across Windows 10 and 11 and is what most audio tools use.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .audio import DeviceInfo, find_device
from .config import Config
from .errors import AudioRoutingError

log = logging.getLogger("wecvoice.devices")


@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name}


def _pycaw():
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception as exc:  # pragma: no cover - optional Windows dependency
        raise AudioRoutingError(
            "Failed to load pycaw; install it with 'pip install pycaw'. "
            "Set switch_default_input=False to skip automatic device switching."
        ) from exc
    return AudioUtilities


def _endpoint_id(device) -> str:
    for attr in ("id", "Id"):
        value = getattr(device, attr, None)
        if isinstance(value, str) and value:
            return value
    try:
        return str(device._dev.GetId())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return ""


def list_endpoints() -> List[Endpoint]:
    """List active audio endpoints known to the Windows audio service."""
    AudioUtilities = _pycaw()
    result: List[Endpoint] = []
    seen = set()
    try:
        devices = AudioUtilities.GetAllDevices()
    except Exception as exc:  # noqa: BLE001
        raise AudioRoutingError(f"Failed to enumerate Windows audio endpoints: {exc}") from exc
    for device in devices:
        try:
            name = str(device.FriendlyName or "").strip()
        except Exception:  # noqa: BLE001
            continue
        state = getattr(device, "state", None)
        # AudioDeviceState.Active == 1; skip unplugged/disabled endpoints.
        if state is not None and int(getattr(state, "value", state)) != 1:
            continue
        endpoint = _endpoint_id(device)
        if not name or not endpoint or endpoint in seen:
            continue
        seen.add(endpoint)
        result.append(Endpoint(endpoint, name))
    return result


def _match_endpoint(needle: str) -> Optional[Endpoint]:
    wanted = needle.lower()
    matches = [item for item in list_endpoints() if wanted in item.name.lower()]
    if not matches:
        names = "; ".join(item.name for item in list_endpoints()) or "(none)"
        raise AudioRoutingError(
            f"Capture endpoint {needle!r} not found. Current endpoints: {names}\n"
            "Hint: VB-CABLE creates \"CABLE Output (VB-Audio Virtual Cable)\"."
        )
    matches.sort(key=lambda item: (0 if item.name.lower().startswith(wanted) else 1, len(item.name)))
    return matches[0]


def get_default_capture() -> Optional[Endpoint]:
    AudioUtilities = _pycaw()
    try:
        device = AudioUtilities.GetMicrophone()
    except Exception:  # noqa: BLE001
        return None
    if device is None:
        return None
    # pycaw versions differ: some return AudioDevice, newer ones return a raw
    # IMMDevice pointer.  In both cases GetId() is available and is the only
    # value that matters for SetDefaultEndpoint.
    try:
        raw_id = device.GetId() if hasattr(device, "GetId") else _endpoint_id(device)
    except Exception:  # noqa: BLE001
        raw_id = _endpoint_id(device)
    raw_id = str(raw_id or "")
    if not raw_id:
        return None
    try:
        name = str(device.FriendlyName or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    if not name:
        for item in list_endpoints():
            if item.id == raw_id:
                name = item.name
                break
    return Endpoint(raw_id, name)


def _apply_default_endpoint(endpoint_id: str) -> None:
    """Set the default endpoint for console, multimedia and communications.

    ``pycaw`` wraps the undocumented PolicyConfig COM object; its
    ``SetDefaultDevice`` helper is the maintained path.  A direct COM fallback
    keeps older pycaw releases working.
    """
    AudioUtilities = _pycaw()
    try:
        from pycaw.constants import ERole
    except Exception:  # noqa: BLE001 - very old pycaw
        ERole = None
    roles = (
        [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
        if ERole is not None
        else [0, 1, 2]
    )
    try:
        AudioUtilities.SetDefaultDevice(endpoint_id, roles)
        return
    except Exception as first_error:  # noqa: BLE001
        log.debug("pycaw.SetDefaultDevice failed; calling IPolicyConfig directly: %s", first_error)
    try:
        from comtypes import CLSCTX_ALL, CoCreateInstance

        from pycaw.api.policyconfig import IPolicyConfig
        from pycaw.constants import CLSID_CPolicyConfigClient

        policy = CoCreateInstance(CLSID_CPolicyConfigClient, IPolicyConfig, CLSCTX_ALL)
        for role in roles:
            value = getattr(role, "value", role)
            policy.SetDefaultEndpoint(endpoint_id, int(value))
    except Exception as second_error:  # noqa: BLE001
        raise AudioRoutingError(
            f"Failed to switch the default recording device: {second_error}\n"
            "Set CABLE Output as the default input manually, "
            "then set switch_default_input=False."
        ) from second_error


def set_default_capture(needle: str) -> Optional[Endpoint]:
    """Make the first capture endpoint matching ``needle`` the default.

    Returns the previous default endpoint so the caller can restore it.
    """
    target = _match_endpoint(needle)
    previous = get_default_capture()
    _apply_default_endpoint(target.id)
    time.sleep(0.25)
    current = get_default_capture()
    if current is None or current.id != target.id:
        log.warning(
            "Default recording device reads %s after switching (expected %s); a system policy or driver may override it",
            current.name if current else "unknown",
            target.name,
        )
    return previous


def restore_capture(previous: Optional[Endpoint]) -> None:
    if previous is None or not previous.id:
        return
    try:
        _apply_default_endpoint(previous.id)
        time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to restore the default recording device: %s", exc)


class AudioRouting:
    """Context manager that routes TTS into WeChat's microphone.

    On enter it locates the render endpoint (``CABLE Input``) and, when
    configured, switches the Windows default capture endpoint to
    ``CABLE Output``.  On exit it restores the previous microphone.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        output: Optional[DeviceInfo] = None,
        input_name: Optional[str] = None,
        switch: Optional[bool] = None,
        restore: Optional[bool] = None,
    ) -> None:
        self.config = config or Config()
        self.output = output or find_device(self.config.output_device, "output")
        self.input_name = input_name or self.config.input_device
        self.switch = self.config.switch_default_input if switch is None else bool(switch)
        self.restore = self.config.restore_default_input if restore is None else bool(restore)
        self._previous: Optional[Endpoint] = None
        self._switched = False

    def __enter__(self) -> "AudioRouting":
        if self.switch:
            self._previous = set_default_capture(self.input_name)
            self._switched = True
        else:
            current = get_default_capture()
            if current and self.input_name.lower() not in current.name.lower():
                log.warning(
                    "Default recording device is %r, not %r; switch it manually if WeChat records silence.",
                    current.name,
                    self.input_name,
                )
        time.sleep(0.35)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._switched and self.restore:
            restore_capture(self._previous)
        time.sleep(0.15)


def check_microphone_permission() -> Tuple[Optional[bool], str]:
    """Read the Windows packaged/non-packaged microphone consent state."""
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return None, "not a Windows environment"
    key_path = (
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
        r"\ConsentStore\microphone\NonPackaged"
    )
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
    except OSError:
        return None, "no microphone consent record yet (Windows will ask on first recording)"
    try:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            try:
                sub_name = winreg.EnumKey(root, index)
            except OSError:
                continue
            lower = sub_name.lower().replace("#", "\\")
            if "weixin.exe" not in lower and "wechat.exe" not in lower:
                continue
            try:
                sub = winreg.OpenKey(root, sub_name)
                value, _ = winreg.QueryValueEx(sub, "Value")
                winreg.CloseKey(sub)
            except OSError:
                continue
            allowed = str(value).lower() in ("allow", "1")
            return allowed, f"{sub_name} = {value}"
    finally:
        try:
            winreg.CloseKey(root)
        except OSError:
            pass
    return None, "WeChat microphone consent record not found"
