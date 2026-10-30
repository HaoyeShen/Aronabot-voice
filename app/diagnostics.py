"""One-click diagnostics for the bot, integrations, and privacy guarantees."""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .model_client import make_client
from .paths import DATA, LOGS, STICKER_DIR
from .wechat_adapter import detect_wechat_backends

log = logging.getLogger(__name__)

Progress = Optional[Callable[[int, str], None]]


def _check(title: str, status: str, detail: str, hint: str = "") -> dict:
    return {"title": title, "status": status, "detail": detail, "hint": hint}


def _integrity_level() -> str:
    """Return low/medium/high based on whoami's mandatory label SID."""
    try:
        result = subprocess.run(
            ["whoami", "/groups"], capture_output=True, text=True,
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = result.stdout or ""
    except Exception:
        return ""
    if "S-1-16-4096" in text:
        return "Low"
    if "S-1-16-8192" in text:
        return "Medium"
    if "S-1-16-12288" in text:
        return "High"
    return ""


def _process_names() -> set[str]:
    if sys.platform != "win32":
        return set()
    names: set[str] = set()
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_bool
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return names
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                names.add(str(entry.szExeFile).lower())
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def _wechat_version() -> str:
    """Read the desktop client's file version without running it."""
    if sys.platform != "win32":
        return ""
    candidates = [
        Path(r"C:\Program Files\Tencent\Weixin\Weixin.exe"),
        Path(r"C:\Program Files\Tencent\WeChat\WeChat.exe"),
        Path(r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    ]
    try:
        import winreg
        for root, key_name in ((winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin")):
            try:
                with winreg.OpenKey(root, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallPath")
                    candidates.append(Path(value) / "Weixin.exe")
            except OSError:
                pass
    except Exception:
        pass
    version_dll = ctypes.windll.version
    for path in candidates:
        if not path.is_file():
            continue
        try:
            size = version_dll.GetFileVersionInfoSizeW(str(path), None)
            if not size:
                continue
            buffer = ctypes.create_string_buffer(size)
            if not version_dll.GetFileVersionInfoW(str(path), 0, size, buffer):
                continue
            pointer = ctypes.c_void_p()
            length = ctypes.c_uint()
            if not version_dll.VerQueryValueW(buffer, "\\", ctypes.byref(pointer),
                                              ctypes.byref(length)):
                continue

            class VS_FIXEDFILEINFO(ctypes.Structure):
                _fields_ = [
                    ("dwSignature", ctypes.c_uint32),
                    ("dwStrucVersion", ctypes.c_uint32),
                    ("dwFileVersionMS", ctypes.c_uint32),
                    ("dwFileVersionLS", ctypes.c_uint32),
                    ("dwProductVersionMS", ctypes.c_uint32),
                    ("dwProductVersionLS", ctypes.c_uint32),
                    ("dwFileFlagsMask", ctypes.c_uint32),
                    ("dwFileFlags", ctypes.c_uint32),
                    ("dwFileOS", ctypes.c_uint32),
                    ("dwFileType", ctypes.c_uint32),
                    ("dwFileSubtype", ctypes.c_uint32),
                    ("dwFileDateMS", ctypes.c_uint32),
                    ("dwFileDateLS", ctypes.c_uint32),
                ]
            info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
            return (f"{info.dwFileVersionMS >> 16}.{info.dwFileVersionMS & 0xFFFF}."
                    f"{info.dwFileVersionLS >> 16}.{info.dwFileVersionLS & 0xFFFF}")
        except Exception:
            continue
    return ""


def _window_process_name(hwnd) -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _wechat_windows() -> list[str]:
    if sys.platform != "win32":
        return []
    titles: list[str] = []
    user32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        process_name = _window_process_name(hwnd)
        is_wechat_process = process_name.startswith(("wechat", "weixin"))
        # Do not match unrelated windows that merely contain the word “微信”
        # (for example this assistant's own window).
        if is_wechat_process or title in ("微信", "WeChat", "Weixin"):
            if "ARONA" not in title.upper():
                titles.append(title)
        return True

    try:
        user32.EnumWindows(EnumProc(callback), 0)
    except Exception:
        pass
    return titles


def _writable(folder: Path) -> tuple[bool, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(folder)
    except OSError as exc:
        return False, str(exc)


def run_diagnostics(service, run_model: bool = True, run_chat_test: bool = False,
                    progress: Progress = None) -> dict:
    checks: list[dict] = []
    total = 14 if run_chat_test else 13

    def step(index: int, name: str) -> None:
        if progress:
            try:
                progress(int(index / total * 100), name)
            except Exception:
                pass

    # 1. Runtime ---------------------------------------------------------
    step(1, "检查运行环境")
    py_ok = sys.version_info >= (3, 9)
    bits = platform.architecture()[0]
    checks.append(_check(
        "Python 运行环境",
        "ok" if py_ok and bits == "64bit" else ("warn" if py_ok else "fail"),
        f"Python {platform.python_version()} · {bits} · {platform.system()}",
        "" if py_ok and bits == "64bit" else "建议使用 64 位 Python 3.10–3.12",
    ))

    level = _integrity_level()
    if level == "Low":
        checks.append(_check(
            "Python 运行完整性", "fail", "当前 Python 处于低完整性（Low）",
            "关闭 ARONA，重新运行「启动 ARONA.bat」会自动修复；也可执行："
            "icacls .venv\\Scripts\\python.exe /setintegritylevel Medium",
        ))
    elif level:
        checks.append(_check("Python 运行完整性", "ok", f"当前 Python 完整性：{level}"))
    else:
        checks.append(_check("Python 运行完整性", "warn", "无法读取完整性级别",
                             "如果启动后报未检测到 Weixin.exe，请重新运行启动脚本"))

    # 2. Filesystem ------------------------------------------------------
    step(2, "检查数据目录")
    write_results = {name: _writable(folder) for name, folder in
                     (("data", DATA), ("logs", LOGS), ("stickers", STICKER_DIR))}
    failed = [name for name, (ok, _) in write_results.items() if not ok]
    checks.append(_check(
        "数据目录写入",
        "ok" if not failed else "fail",
        "data / logs / stickers 均可写入" if not failed else f"不可写：{', '.join(failed)}",
        "请检查文件夹权限，或以普通用户身份重新解压程序" if failed else "",
    ))

    # 3. Configuration ---------------------------------------------------
    step(3, "校验配置")
    cfg = service.config.data
    try:
        cfg = service.config.reload()
        context_limit = int(cfg["bot"]["context_limit"])
        profile = service.config.active_profile()
        if context_limit < 20:
            raise ValueError("上下文条数小于 20")
        ok = bool(profile) and bool(cfg.get("active_persona_id"))
        checks.append(_check(
            "配置与上下文设置",
            "ok" if ok else "warn",
            f"上下文 {context_limit} 条 · 当前模型 {profile.get('model', '未选')}",
            "" if ok else "请在设置中至少保留 20 条上下文",
        ))
    except Exception as exc:
        checks.append(_check("配置与上下文设置", "fail", f"配置读取失败：{exc}",
                             "打开设置重新保存，或备份后删除 data/config.json"))

    # 4. Database --------------------------------------------------------
    step(4, "检查本地记忆库")
    db_ok, db_detail = service.memory.integrity()
    stats = service.memory.stats()
    checks.append(_check(
        "本地上下文数据库",
        "ok" if db_ok else "fail",
        f"SQLite {db_detail} · 已保存 {stats['total']} 条消息",
        "" if db_ok else "关闭程序后备份并删除 data/arona.db，再重新启动",
    ))

    try:
        train_stats = service.training.stats()
        train_enabled = bool(cfg.get("training", {}).get("enabled", True))
        if not train_stats["docs"]:
            checks.append(_check("本地训练知识库", "warn", "尚未上传训练资料",
                                 "打开「本地训练」上传 txt/md/docx/pdf 等资料"))
        elif not train_enabled:
            checks.append(_check("本地训练知识库", "warn",
                                 f"{train_stats['docs']} 个文件 · 训练开关已关闭",
                                 "到「本地训练」启用本地知识训练"))
        else:
            checks.append(_check(
                "本地训练知识库", "ok",
                f"{train_stats['docs']} 个文件 · {train_stats['chunks']} 个片段 · 已启用",
            ))
    except Exception as exc:
        checks.append(_check("本地训练知识库", "warn", f"读取失败：{exc}",
                             "到「本地训练」重建索引，或删除 data/training.db 后重试"))

    # 5. Personas --------------------------------------------------------
    step(5, "检查人格设定")
    persona = service.personas.get(cfg.get("active_persona_id"))
    has_core = bool(persona.get("name")) and bool(persona.get("system_prompt") or persona.get("description"))
    checks.append(_check(
        "人格与语气配置",
        "ok" if has_core else "warn",
        f"当前人格：{persona.get('name', '未设置')} · {persona.get('title', '')}",
        "" if has_core else "请补充人格的核心设定或角色描述",
    ))

    # 6. Stickers --------------------------------------------------------
    step(6, "检查表情包")
    stickers = service.stickers.enabled()
    all_stickers = service.stickers.list()
    missing = [s for s in all_stickers if not service.stickers.path_for(s).is_file()]
    if stickers:
        status = "warn" if missing else "ok"
        detail = f"{len(stickers)} 张可用表情包"
        if missing:
            detail += f" · {len(missing)} 个文件缺失"
        checks.append(_check("表情包资源", status, detail,
                             "删除缺失文件的表情包条目或重新导入" if missing else ""))
    else:
        checks.append(_check("表情包资源", "warn", "没有启用的表情包",
                             "在表情包页导入 PNG/GIF，或重新扫描示例表情包"))

    # 7. Privacy ---------------------------------------------------------
    step(7, "验证隐私模式")
    forbidden = [name for name in ("pyautogui", "mss", "PIL.ImageGrab", "dxcam")
                 if name in sys.modules]
    codex = [name for name in ("codex", "openai_codex", "openai") if name in sys.modules]
    privacy_ok = not forbidden and not codex
    checks.append(_check(
        "隐私与依赖边界",
        "ok" if privacy_ok else "warn",
        "数据库监听 + Windows UI Automation · 无截图/录屏 · 无 Codex / OpenAI SDK"
        if privacy_ok else f"检测到模块：{', '.join(forbidden + codex)}",
        "" if privacy_ok else "请移除相关依赖后再运行，本项目不调用截屏或 Codex 库",
    ))

    # 8. Model config ----------------------------------------------------
    step(8, "检查模型配置")
    profile = service.config.active_profile()
    provider = str(profile.get("provider", ""))
    configured = provider == "mock" or (bool(profile.get("model")) and
                                        (provider == "ollama" or bool(profile.get("base_url"))))
    checks.append(_check(
        "模型配置",
        "ok" if configured else "fail",
        f"{profile.get('name', '未命名')} · {provider} · {profile.get('model', '未选模型')}",
        "" if configured else "在「模型与 API」页填写地址和模型名",
    ))

    # 9. Model connectivity ---------------------------------------------
    step(9, "连接模型服务")
    if not run_model:
        checks.append(_check("模型连接", "warn", "已跳过在线检测", "点击“检测模型”单独测试"))
    else:
        started = time.perf_counter()
        try:
            health = make_client(profile).health()
            checks.append(_check(
                "模型连接",
                "ok" if health.ok else "fail",
                (health.message + (f" · {health.detail}" if health.detail else ""))[:300],
                "" if health.ok else
                ("启动 Ollama：ollama serve；并确认模型已 pull" if provider == "ollama"
                 else "检查 API 地址、Key、余额与网络代理"),
            ))
        except Exception as exc:
            checks.append(_check("模型连接", "fail", f"检测异常：{exc}", "检查模型配置"))

    # 10. WeChat automation component ----------------------------------
    step(10, "检查微信自动化组件")
    backends = detect_wechat_backends()
    if backends.get("wechatauto"):
        checks.append(_check("微信自动化组件", "ok",
                             "DB-UIA 兼容后端已安装（wechatauto-replica 1.2.8；4.1.15）",
                             "该实验后端会在微信进程中激活 Qt 无障碍位，请仅在本人设备上使用"))
    else:
        checks.append(_check("微信自动化组件", "fail", "未安装 4.1.15 兼容后端",
                             "运行「安装 4.1.15 后端.bat」安装本地数据库 + UIA 后端"))

    # 11. WeChat desktop process ---------------------------------------
    step(11, "检查微信桌面端")
    processes = _process_names()
    wechat_processes = sorted(name for name in processes
                              if name.startswith(("wechat", "weixin")))
    titles = _wechat_windows()
    client_version = _wechat_version()
    version_note = f" · 客户端 {client_version}" if client_version else ""
    version_warning = False
    if client_version:
        try:
            parts = [int(p) for p in client_version.split(".")]
            version_warning = tuple(parts[:3]) < (4, 1, 12)
        except (ValueError, IndexError):
            pass
    if wechat_processes:
        status = "warn" if version_warning else "ok"
        hint = ""
        if not titles:
            hint = "若监听异常，请把微信主窗口打开并保持登录"
        if version_warning:
            hint = "wechatauto 后端主要适配微信 4.1.12+，当前客户端版本可能过旧"
        checks.append(_check(
            "微信桌面端",
            status,
            f"进程在线：{', '.join(wechat_processes)}{version_note}" +
            (f" · 窗口：{titles[0]}" if titles else " · 主窗口未显示"),
            hint,
        ))
    else:
        checks.append(_check("微信桌面端", "fail",
                             "未发现微信进程" + version_note,
                             "先启动并登录 Windows 微信，再启动机器人"))

    # 12. Live connection ----------------------------------------------
    step(12, "检查实时监听")
    if service.running and service.adapter:
        ok, message = service.adapter.probe()
        checks.append(_check(
            "实时监听状态",
            "ok" if ok else "warn",
            message + f" · {service.adapter.backend_label} · 监听 "
            f"{len(service.config.enabled_targets())} 个对象",
            "" if ok else "点击「重启机器人」重新建立 UI Automation 连接",
        ))
    else:
        checks.append(_check("实时监听状态", "warn", "机器人当前未启动",
                             "在控制台点击「启动机器人」后再次检测"))

    # 13. Target whitelist ---------------------------------------------
    step(13, "检查监听白名单")
    targets = service.config.enabled_targets()
    checks.append(_check(
        "测试对象白名单",
        "ok" if targets else "warn",
        f"{len(targets)} 个启用对象：" + "、".join(t["name"] for t in targets[:5])
        if targets else "尚未添加任何测试对象",
        "" if targets else "在「测试对象」页添加好友或群聊名称；未加入白名单的会话不会收到回复",
    ))

    # 14. End-to-end conversation test ---------------------------------
    if run_chat_test:
        step(14, "运行上下文对话测试")
        test_chat = "自动调试::上下文测试"
        try:
            service.memory.clear(test_chat)
            first = service.debug_chat("你好，请用一句话向老师打招呼。", chat_name=test_chat)
            service.debug_chat("记住一个口令：蓝色档案。", chat_name=test_chat)
            third = service.debug_chat("老师刚才让你记住的口令是什么？只回答口令。",
                                       chat_name=test_chat)
            remembered = "蓝色" in third.get("text", "") or "档案" in third.get("text", "")
            got_sticker = bool(first.get("sticker") or third.get("sticker"))
            if remembered:
                status = "ok"
                detail = "三轮上下文测试通过，模型记住了前文口令"
            else:
                status = "warn"
                detail = "对话链路已通，但模型未稳定复述前文口令：" + third.get("text", "")[:80]
            if not got_sticker:
                detail += " · 本轮未触发表情包（可接受）"
            checks.append(_check("自动对话与上下文", status, detail,
                                 "" if remembered else "上下文窗口较小或模型指令跟随较弱，可尝试更大的上下文设置"))
        except Exception as exc:
            checks.append(_check("自动对话与上下文", "fail", f"对话测试失败：{exc}",
                                 "检查模型服务与 API 配置；详细错误见运行日志"))
        finally:
            try:
                service.memory.clear(test_chat)
            except Exception:
                pass

    passed = sum(1 for c in checks if c["status"] == "ok")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")
    overall = "fail" if failed else ("warn" if warnings else "ok")
    result = {
        "overall": overall,
        "passed": passed,
        "warnings": warnings,
        "failed": failed,
        "total": len(checks),
        "checks": checks,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": __version__,
    }
    log.info("自动调试完成：%d 通过 / %d 警告 / %d 失败", passed, warnings, failed)
    if progress:
        try:
            progress(100, "调试完成")
        except Exception:
            pass
    return result
