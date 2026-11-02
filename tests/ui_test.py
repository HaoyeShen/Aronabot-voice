"""Automated construction test for every page and dialog (no network)."""

from __future__ import annotations

import sys
import traceback
import tempfile
import time
import threading
from unittest.mock import patch
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
        # Observe the transition time rather than sampling just before four
        # seconds: a delayed update() can legitimately cross that boundary.
        reopened = []
        set_closed = home._set_closed
        def observe_eyes(value):
            if not value:
                reopened.append(time.monotonic())
            set_closed(value)
        with patch.object(home, '_set_closed', side_effect=observe_eyes):
            started = time.monotonic()
            home.pet_head()
            assert home._closed
            deadline = started + 8
            while not reopened and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert reopened, 'pet must reopen automatically'
            assert reopened[0] - started >= 3.98, 'repeated pet must restart the four-second hold'
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
        assert page_targets.news_test_chat.get() == 'test'
        page_targets.news_test_chat.set('')
        with patch.object(service, 'send_test_news') as send:
            page_targets.send_test_news()
            send.assert_not_called()
        entered_news, release_news = threading.Event(), threading.Event()
        def fake_news(chat):
            assert chat == '可设置的新闻测试群'
            entered_news.set()
            release_news.wait(5)
            return {'ok': True, 'message': '新闻测试已完成'}
        page_targets.news_test_chat.set('可设置的新闻测试群')
        with patch.object(service, 'send_test_news', side_effect=fake_news) as send:
            page_targets.send_test_news()
            assert entered_news.wait(3) and page_targets.news_test_btn._busy
            page_targets.send_test_news()
            assert send.call_count == 1
            app.show_page('home')
            release_news.set()
            deadline = time.monotonic() + 5
            while page_targets._news_test_busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert not page_targets._news_test_busy and not page_targets.news_test_btn._busy
            assert page_targets.news_test_status.cget('text') == '新闻测试已完成'
        assert ConfigStore(config.path).data['news']['test_chat'] == '可设置的新闻测试群'
        # The manual test stays responsive and completes after navigating away.
        reply_page = app.pages['listen_reply']
        assert reply_page.idle_chat_enabled.get()
        assert float(reply_page.idle_chat_probability.get()) == 3
        assert reply_page.idle_chat_minutes.get() == '30'
        assert reply_page.idle_video_enabled.get()
        assert reply_page.idle_video_topics.get() == '蔚蓝档案'
        assert float(reply_page.idle_video_probability.get()) == 100
        reply_page.idle_video_topics.set('蔚蓝档案，明日方舟,蔚蓝档案')
        reply_page.idle_video_probability.set('65')
        reply_page.idle_chat_probability.set('7.5')
        reply_page.idle_chat_minutes.set('45')
        reply_page.save()
        saved_bot = ConfigStore(config.path).bot
        assert saved_bot['idle_chat_probability'] == .075 and saved_bot['idle_chat_minutes'] == 45
        assert saved_bot['idle_video_topics'] == ['蔚蓝档案', '明日方舟']
        assert saved_bot['idle_video_probability'] == .65
        reply_page.idle_chat_probability.set('0')
        reply_page.save()
        assert ConfigStore(config.path).bot['idle_chat_probability'] == 0
        assert reply_page.followup_timeout.get() == '5'
        reply_page.followup_timeout.set('8')
        reply_page.save()
        assert ConfigStore(config.path).bot['followup_timeout_minutes'] == 8
        assert reply_page.voice_test_chat.get() == 'test'
        entered, release = threading.Event(), threading.Event()
        def fake_voice(chat, preset_id=''):
            assert chat == '可设置的测试群' and preset_id == 'plana'
            entered.set()
            release.wait(5)
            return {'ok': True, 'message': '测试已完成'}
        reply_page.voice_test_chat.set('')
        with patch.object(service, 'send_test_voice') as send:
            reply_page.send_test_voice()
            send.assert_not_called()
        reply_page.voice_test_chat.set('可设置的测试群')
        reply_page.voice_preset.set(reply_page._voice_id_map['plana'])
        with patch.object(service, 'send_test_voice', side_effect=fake_voice) as send:
            reply_page.send_test_voice()
            assert entered.wait(3) and reply_page.voice_test_btn._busy
            reply_page.send_test_voice()
            assert send.call_count == 1
            app.show_page('home')
            release.set()
            deadline = time.monotonic() + 5
            while reply_page._voice_test_busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert not reply_page._voice_test_busy and not reply_page.voice_test_btn._busy
            assert reply_page.voice_test_status.cget('text') == '测试已完成'
        assert ConfigStore(config.path).bot['voice_test_chat'] == '可设置的测试群'
        group_a_id = config.add_target('主题界面群A', 'group', 'arona')['id']
        group_b_id = config.add_target('主题界面群B', 'group', 'arona')['id']
        for name, topics in [('主题界面群A', 'Python，蔚蓝档案'), ('主题界面群B', '明日方舟')]:
            dialog = TargetDialog(root, page_targets, config.target_by_name(name))
            assert dialog.idle_video_topics.winfo_manager() == 'pack'
            dialog.idle_video_topics.set(topics)
            with patch.object(app, 'reload_listening'):
                dialog._save()
        saved = ConfigStore(config.path)
        assert saved.target_by_name('主题界面群A')['idle_video_topics'] == ['Python', '蔚蓝档案']
        assert saved.target_by_name('主题界面群B')['idle_video_topics'] == ['明日方舟']
        dialog = TargetDialog(root, page_targets, config.target_by_name('主题界面群A'))
        assert dialog.idle_video_topics.get() == 'Python，蔚蓝档案'
        dialog.idle_video_topics.set('')
        with patch.object(app, 'reload_listening'):
            dialog._save()
        assert ConfigStore(config.path).target_by_name('主题界面群A')['idle_video_topics'] == []
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
            if name == 'target':
                assert not dialog.idle_video_topics.winfo_manager()
                dialog.kind.set('群聊')
                dialog.kind.event_generate('<<ComboboxSelected>>')
                root.update_idletasks()
                assert dialog.idle_video_topics.winfo_manager() == 'pack'
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
