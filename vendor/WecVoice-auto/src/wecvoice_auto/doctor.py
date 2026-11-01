"""Environment diagnostics for ``wecvoice doctor``."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import List, Optional

from .audio import default_input, find_device, list_devices
from .config import Config
from .devices import check_microphone_permission, get_default_capture
from .wechat import WeChatWindow


@dataclass
class Check:
    level: str  # "ok" | "warn" | "fail"
    title: str
    detail: str = ""

    @property
    def icon(self) -> str:
        return {"ok": "[OK]", "warn": "[!]", "fail": "[X]"}.get(self.level, "[?]")


def _wechat_version() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in candidates:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        try:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    sub_name = winreg.EnumKey(root, index)
                    sub = winreg.OpenKey(root, sub_name)
                    try:
                        name, _ = winreg.QueryValueEx(sub, "DisplayName")
                    except OSError:
                        continue
                    if "微信" in str(name) or "WeChat" in str(name):
                        try:
                            version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                            return str(version)
                        except OSError:
                            return "installed"
                except OSError:
                    continue
        finally:
            try:
                winreg.CloseKey(root)
            except OSError:
                pass
    return ""


def run_checks(config: Optional[Config] = None) -> List[Check]:
    cfg = config or Config.load()
    checks: List[Check] = []

    checks.append(Check("ok", "Python", f"{sys.version.split()[0]} ({platform.architecture()[0]})"))
    if sys.platform != "win32":
        checks.append(Check("fail", "OS", "This library only supports Windows 10/11."))
    else:
        checks.append(Check("ok", "OS", f"{platform.system()} {platform.release()}"))

    try:
        import edge_tts  # noqa: F401
        checks.append(Check("ok", "Edge TTS", "installed (free, requires network)"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("fail", "Edge TTS", f"not installed: {exc}"))

    try:
        devices = list_devices()
        outputs = [d for d in devices if d.kind == "output"]
        inputs = [d for d in devices if d.kind == "input"]
        checks.append(Check("ok", "Audio devices", f"{len(outputs)} output / {len(inputs)} input"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("fail", "Audio devices", str(exc)))

    try:
        output = find_device(cfg.output_device, "output")
        checks.append(Check("ok", "Virtual output", f"[{output.index}] {output.name}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check(
            "fail", "Virtual output",
            f"{cfg.output_device!r} not found: {exc}\n"
            "Install VB-Audio Virtual Cable: https://vb-audio.com/Cable/ (reboot after installation)",
        ))

    try:
        capture = find_device(cfg.input_device, "input")
        checks.append(Check("ok", "Virtual input", f"[{capture.index}] {capture.name}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check(
            "fail", "Virtual input",
            f"{cfg.input_device!r} not found: {exc}\n"
            "VB-CABLE creates both CABLE Input and CABLE Output.",
        ))

    try:
        current = default_input()
        name = current.name if current else "(unknown)"
        if cfg.input_device.lower() in name.lower():
            checks.append(Check("ok", "Default recording device", name))
        elif cfg.switch_default_input:
            checks.append(Check(
                "warn", "Default recording device",
                f"currently {name!r}; it will switch to {cfg.input_device!r} before sending. "
                "If WeChat records silence, set CABLE Output as the default and restart WeChat.",
            ))
        else:
            checks.append(Check(
                "fail", "Default recording device",
                f"currently {name!r}, but switch_default_input=False; WeChat will record the real microphone.",
            ))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("warn", "Default recording device", str(exc)))

    try:
        current_capture = get_default_capture()
        if current_capture:
            checks.append(Check("ok", "Windows default microphone", current_capture.name))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("warn", "Windows default microphone", f"failed to read: {exc}"))

    allowed, detail = check_microphone_permission()
    if allowed is False:
        checks.append(Check(
            "fail", "WeChat microphone permission",
            f"{detail}; allow desktop apps in Windows Settings > Privacy > Microphone.",
        ))
    elif allowed is True:
        checks.append(Check("ok", "WeChat microphone permission", detail))
    else:
        checks.append(Check("warn", "WeChat microphone permission", detail))

    try:
        window = WeChatWindow()
        if window.find():
            checks.append(Check("ok", "WeChat window", f"{window.title!r}, hwnd {window.hwnd}"))
        else:
            checks.append(Check(
                "fail", "WeChat window",
                "WeChat main window not found; sign in to WeChat 4.1.9+ and open the main window.",
            ))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("warn", "WeChat window", str(exc)))

    version = _wechat_version()
    if version:
        checks.append(Check("ok", "WeChat version", version))
    else:
        checks.append(Check("warn", "WeChat version", "not found in the registry; 4.1.9+ is required for native voice messages"))

    if cfg.trigger == "alt":
        checks.append(Check("ok", "Trigger", "Hold RIGHT Alt (AltGr) to talk (recommended)"))
    else:
        checks.append(Check("warn", "Trigger", "Hold the on-screen voice button (requires the UIA tree)"))

    return checks


def format_report(checks: List[Check]) -> str:
    lines = ["WecVoice-auto environment check", "=" * 30]
    for check in checks:
        lines.append(f"{check.icon} {check.title}: {check.detail}")
    failures = sum(1 for item in checks if item.level == "fail")
    warnings = sum(1 for item in checks if item.level == "warn")
    lines.append("-" * 30)
    lines.append(f"Result: {len(checks)} checks, {failures} failed, {warnings} warnings")
    return "\n".join(lines)
