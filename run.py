"""ARONA launcher.

Usage:
    python run.py             Start the desktop application.
    python run.py --check     Print a diagnostic report and exit.
    python run.py --ui-smoke  Build and close the window (automated testing).
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def current_integrity() -> str:
    """Return Low/Medium/High for this process (Windows mandatory label)."""
    try:
        result = subprocess.run(
            ["whoami", "/groups"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = result.stdout or ""
    except Exception:
        return ""
    for sid, label in (("S-1-16-4096", "Low"),
                       ("S-1-16-8192", "Medium"),
                       ("S-1-16-12288", "High")):
        if sid in text:
            return label
    return ""


def configure_local_temp() -> None:
    """Keep pywin32/COM generated caches inside the app's writable data dir.

    Some locked-down Windows accounts cannot create %TEMP%\\gen_py.  wxauto4
    imports win32com, so using a local temp directory avoids a startup crash.
    """
    try:
        local = ROOT / "data" / "temp"
        local.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = str(local)
        os.environ["TMP"] = str(local)
        os.environ["TMPDIR"] = str(local)
        tempfile.tempdir = str(local)
    except Exception:
        pass


def set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def show_fatal(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "ARONA 启动失败", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def build_services():
    from app.config_store import ConfigStore, PersonaStore, StickerStore
    from app.memory import MemoryStore
    from app.paths import ensure_dirs
    from app.service import BotService
    from app.training import TrainingStore

    ensure_dirs()
    config = ConfigStore()
    personas = PersonaStore()
    stickers = StickerStore()
    memory = MemoryStore()
    training = TrainingStore()
    return BotService(config, personas, stickers, memory, training)


def run_check() -> int:
    from app.logging_setup import setup_logging

    setup_logging()
    service = build_services()
    try:
        state = service.state()
        print("ARONA 自动诊断")
        print("=" * 64)
        print(f"模型：{state.get('model')} · 人格：{state.get('persona')}")
        print(f"监听对象：{state.get('target_count')} · 表情包：{state.get('sticker_count')}")
        print("-" * 64)
        result = service.diagnostics(run_model=True, run_chat_test=False)
        for check in result["checks"]:
            mark = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}.get(
                check["status"], "[INFO]")
            print(f"{mark} {check['title']}: {check['detail']}")
            if check.get("hint"):
                print(f"       → {check['hint']}")
        print("-" * 64)
        print(f"{result['passed']} 通过 / {result['warnings']} 提示 / {result['failed']} 失败")
        return 0
    finally:
        service.shutdown()


def run_ui_smoke(timeout_ms: int = 1800) -> int:
    from app.config_store import ConfigStore, PersonaStore, StickerStore
    from app.logging_setup import setup_logging
    from app.memory import MemoryStore
    from app.paths import ASSETS
    from app.service import BotService

    setup_logging()
    config = ConfigStore()
    personas = PersonaStore()
    stickers = StickerStore()
    memory = MemoryStore()
    service = BotService(config, personas, stickers, memory)
    import tkinter as tk
    from app.ui.main_window import MainWindow

    root = tk.Tk()
    root._arona_no_autostart = True
    icon = tk.PhotoImage(file=str(ASSETS / "app_icon.png"))
    root._arona_icon = icon
    app = MainWindow(root, service, icon)
    pages = list(app.pages.keys())

    def inspect():
        try:
            pages_now = list(app.pages.keys())
            state = service.state()
            print(f"UI smoke: window={root.winfo_width()}x{root.winfo_height()} "
                  f"page={app._current_page} pages={pages_now} targets={state['target_count']}")
            for key in pages_now:
                app.show_page(key)
                root.update_idletasks()
            print("UI smoke: all pages rendered")
        except Exception:
            traceback.print_exc()
            service.shutdown()
            root.destroy()
            raise SystemExit(1)
        finally:
            try:
                service.shutdown()
                root.destroy()
            except Exception:
                pass

    root.after(timeout_ms, inspect)
    root.mainloop()
    print("UI smoke: done")
    return 0


def main() -> int:
    configure_local_temp()
    parser = argparse.ArgumentParser(description="ARONA WeChat assistant")
    parser.add_argument("--check", action="store_true", help="run diagnostics and exit")
    parser.add_argument("--ui-smoke", action="store_true", help="build UI and close")
    args = parser.parse_args()
    if args.check:
        return run_check()
    if args.ui_smoke:
        return run_ui_smoke()

    if current_integrity() == "Low":
        show_fatal(
            "ARONA 当前以低完整性（Low）运行，无法读取微信进程。\n\n"
            "请关闭本窗口，改为从桌面/文件资源管理器双击「启动 ARONA.bat」，\n"
            "或运行「创建桌面快捷方式.bat」后从桌面启动。\n\n"
            "启动脚本会把 .venv 的 Python 启动器修复为 Medium 完整性。"
        )
        return 1

    set_dpi_awareness()
    if os.environ.get("ARONA_DEBUG_THREADS"):
        try:
            import faulthandler
            debug_file = open(ROOT / "logs" / "threads.txt", "w", encoding="utf-8")
            faulthandler.dump_traceback_later(20, repeat=True, file=debug_file)
        except Exception:
            pass
    from app.logging_setup import setup_logging

    setup_logging()
    logging.getLogger("arona").info("ARONA 启动 · 数据库 + UIA · 无 wxauto · 无截图/录屏")
    try:
        service = build_services()
    except Exception:
        detail = traceback.format_exc()
        logging.getLogger("arona").critical("初始化失败：%s", detail)
        show_fatal("初始化失败，请查看 logs/arona.log。\n\n" + detail[-1500:])
        return 1

    try:
        import tkinter as tk
        from app.paths import ASSETS
        from app.ui.main_window import MainWindow

        root = tk.Tk()
        icon = tk.PhotoImage(file=str(ASSETS / "app_icon.png"))
        root._arona_icon = icon
        window = MainWindow(root, service, icon)

        def report_callback_exception(exc_type, exc_value, exc_tb):
            detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            logging.getLogger("arona.ui").error("界面异常：%s", detail)
            try:
                from tkinter import messagebox
                messagebox.showerror("ARONA 界面异常",
                                     "界面发生异常，详细信息已写入 logs/arona.log。\n\n"
                                     + str(exc_value)[:500])
            except Exception:
                pass

        root.report_callback_exception = report_callback_exception
        root.mainloop()
        return 0
    except Exception:
        detail = traceback.format_exc()
        logging.getLogger("arona").critical("启动 GUI 失败：%s", detail)
        show_fatal("启动界面失败，请查看 logs/arona.log。\n\n" + detail[-1500:])
        try:
            service.shutdown()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
