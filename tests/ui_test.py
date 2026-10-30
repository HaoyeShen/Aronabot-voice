"""Automated construction test for every page and dialog (no network)."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.logging_setup import setup_logging
from app.memory import MemoryStore
from app.paths import ASSETS
from app.service import BotService


def main() -> int:
    setup_logging()
    config = ConfigStore()
    original_profile = config.data.get("active_profile_id")
    # The test must not depend on Ollama being installed.
    config.set_active_profile("demo-local")
    personas = PersonaStore()
    stickers = StickerStore()
    memory = MemoryStore()
    service = BotService(config, personas, stickers, memory)
    import tkinter as tk
    from app.ui.main_window import MainWindow
    from app.ui.dialogs import DiagnosticsDialog, ReplyDetailDialog
    from app.ui.pages.models import ModelDialog
    from app.ui.pages.personas import PersonaDialog
    from app.ui.pages.stickers import StickerDialog
    from app.ui.pages.targets import TargetDialog

    root = tk.Tk()
    root._arona_no_autostart = True
    root.withdraw()
    icon = tk.PhotoImage(file=str(ASSETS / "app_icon.png"))
    root._arona_icon = icon
    app = MainWindow(root, service, icon)
    errors: list[str] = []
    try:
        app.toast("界面提示测试", "info")  # exercises the callable ToastManager
        app.toast.hide()
        # A busy flag must be cleared when a start/stop callback returns.
        app._bot_action_busy = True
        app.topbar.action.set_busy(True, "连接中")
        app._bot_started(True)
        assert not app.topbar.action._busy, "start must re-enable the topbar button"
        app._bot_action_busy = True
        app.topbar.action.set_busy(True, "停止中")
        app._bot_stopped()
        assert not app.topbar.action._busy, "stop must re-enable the topbar button"
        app.topbar.action.set_busy(True, "测试")
        app._bot_action_busy = False
        app.refresh_header()
        assert not app.topbar.action._busy, "refresh_header must clear stale busy state"
        for key in ("dashboard", "debug", "monitor", "stickers", "targets", "listen_reply", "personas",
                    "models", "training", "logs", "settings"):
            app.show_page(key)
            root.update_idletasks()
            print("page ok:", key, app.pages[key].winfo_reqwidth(),
                  app.pages[key].winfo_reqheight())
        page_targets = app.pages["targets"]
        sample_target = {"id": "test", "name": "文件传输助手", "type": "friend",
                         "enabled": True, "persona_id": "arona", "require_at": True,
                         "context_limit": 30, "reply_probability": 1.0, "note": ""}
        dialogs = [
            ("sticker", lambda: StickerDialog(root, app.pages["stickers"], None)),
            ("persona", lambda: PersonaDialog(root, app.pages["personas"], None)),
            ("model", lambda: ModelDialog(root, app.pages["models"], None)),
            ("target", lambda: TargetDialog(root, page_targets, sample_target)),
            ("diagnostics", lambda: DiagnosticsDialog(
                root,
                {"overall": "warn", "passed": 9, "warnings": 3, "failed": 0,
                 "finished_at": "2026-10-25 14:00:00",
                 "checks": [{"title": "示例检查", "status": "ok", "detail": "ok",
                             "hint": ""}]},
                icon=icon, bank=app.bank)),
            ("reply", lambda: ReplyDetailDialog(
                root,
                {"chat": "文件传输助手", "created_iso": "2026-10-25T14:00:00",
                 "profile_name": "mock:arona-demo", "latency_ms": 200,
                 "user_content": "你好", "content": "老师，阿洛娜在哦。",
                 "sticker_name": "挥手问候",
                 "sticker_path": str(ROOT / "data/stickers/arona_hello.png")},
                bank=app.bank, icon=icon)),
        ]
        for name, factory in dialogs:
            dialog = factory()
            dialog.update_idletasks()
            print("dialog ok:", name, dialog.winfo_reqwidth(), dialog.winfo_reqheight())
            dialog.destroy()
        root.update_idletasks()
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        try:
            service.shutdown()
            root.destroy()
        except Exception:
            pass
        if original_profile:
            config.set_active_profile(original_profile)
    if errors:
        print("\n".join(errors))
        return 1
    print("ALL UI CONSTRUCTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
