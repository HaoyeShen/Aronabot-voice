"""Automated construction test for every page and dialog (no network)."""

from __future__ import annotations

import sys
import traceback
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.logging_setup import setup_logging
from app.memory import MemoryStore
from app.paths import ASSETS
from app.service import BotService
from app.training import TrainingStore


def main() -> int:
    setup_logging()
    sandbox = tempfile.TemporaryDirectory()
    base = Path(sandbox.name)
    config = ConfigStore(base / 'config.json')
    original_profile = config.data.get("active_profile_id")
    # The test must not depend on Ollama being installed.
    config.set_active_profile("demo-local")
    personas = PersonaStore(base / 'personas.json')
    stickers = StickerStore(base / 'stickers.json')
    memory = MemoryStore(base / 'memory.db')
    training = TrainingStore(base / 'training.db', base / 'files')
    service = BotService(config, personas, stickers, memory, training,
                         admin_state_path=base / 'admin.json')
    import tkinter as tk
    from app.ui.main_window import MainWindow
    from app.ui.dialogs import DiagnosticsDialog, ReplyDetailDialog
    from app.ui.pages.models import ModelDialog
    from app.ui.pages.personas import PersonaDialog
    from app.ui.pages.stickers import StickerDialog
    from app.ui.pages.targets import TargetDialog
    from app.ui.pages.listen_reply import VoicePresetDialog

    root = tk.Tk()
    root._arona_no_autostart = True
    root.withdraw()
    icon = tk.PhotoImage(file=str(ASSETS / "app_icon.png"))
    root._arona_icon = icon
    app = MainWindow(root, service, icon)
    errors: list[str] = []
    root.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
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
        assert app._current_page == 'home'
        home = app.pages['home']
        root.geometry(f'{root.minsize()[0]}x{root.minsize()[1]}')
        root.update()
        home._draw()
        assert set(home.tiles) == set((
            'dashboard', 'debug', 'monitor', 'stickers', 'targets', 'listen_reply',
            'personas', 'models', 'training', 'logs', 'settings'))
        assert len(home._portrait_photos) == 2, f'both portrait frames must load: {home.canvas.winfo_width()}x{home.canvas.winfo_height()}'
        for tile in home.tiles.values():
            assert tile.winfo_x() >= 0 and tile.winfo_y() >= 0
            assert tile.winfo_x()+tile.winfo_width() <= home.canvas.winfo_width()
            assert tile.winfo_y()+tile.winfo_height() <= home.canvas.winfo_height()
        header_frames = app.topbar.winfo_children()[:2]
        assert header_frames[0].winfo_rootx()+header_frames[0].winfo_width() <= header_frames[1].winfo_rootx(), 'header must not overlap at minimum size'
        x1, y1, x2, y2 = home._head_box
        home.canvas.event_generate('<Button-1>', x=int((x1+x2)/2), y=int((y1+y2)/2))
        root.update()
        assert home._closed, 'head hit area must close the eye'
        # Check real Tk timers and restarting the four-second hold.
        deadline = time.monotonic()+.3
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        home.pet_head()
        deadline = time.monotonic()+3.85
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        assert home._closed, 'pet must keep the eye closed for four seconds'
        deadline = time.monotonic()+.3
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        assert not home._closed, 'pet must reopen automatically'
        home._blink()
        assert home._closed
        home._finish_blink()
        assert not home._closed
        for key, tile in home.tiles.items():
            tile.invoke()
            assert app._current_page == key, f'{key} card must open the original feature'
            app.show_page('home')
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
            ("voice", lambda: VoicePresetDialog(root, service.voice_presets.get('arona'), lambda value: None)),
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
        sandbox.cleanup()
    if errors:
        print("\n".join(errors))
        return 1
    print("ALL UI CONSTRUCTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
