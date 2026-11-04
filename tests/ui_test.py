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
            'dashboard', 'debug', 'agent', 'monitor', 'stickers', 'targets', 'listen_reply',
            'personas', 'models', 'training', 'logs', 'settings'))
        assert len(home._portrait_photos) == 2, f'both portrait frames must load: {home.canvas.winfo_width()}x{home.canvas.winfo_height()}'
        for tile in home.tiles.values():
            assert tile.winfo_x() >= 0 and tile.winfo_y() >= 0
            assert tile.winfo_x()+tile.winfo_width() <= home.canvas.winfo_width()
            assert tile.winfo_y()+tile.winfo_height() <= home.canvas.winfo_height()
        from app.agent_runtime import RUNTIME
        from app.model_audit import model_call
        from types import SimpleNamespace
        agent_fixture=SimpleNamespace(model='ui-fixture',api_key='',chat=lambda *a,**k:SimpleNamespace(text='A群的回答'))
        with RUNTIME.task('UI 测试任务','独立聊天A','ui_agent_a'):
            model_call(agent_fixture,'chat','消息回复',[{'role':'user','content':'A群输入'}])
        with RUNTIME.task('A群后续新闻','独立聊天A','ui_agent_a'):
            RUNTIME.note('agent','同一会话中的新闻结果')
        with RUNTIME.task('另一个聊天','独立聊天B','ui_agent_b'):
            RUNTIME.note('agent','B群不可串入A群的内容')
        app.show_page('agent')
        agent_page=app.pages['agent']
        agent_page.agent_select.set(next(k for k in agent_page._agent_ids if k.startswith('独立聊天A')))
        agent_page.refresh()
        def visible_text(widget):
            values=[]
            if isinstance(widget,tk.Label):values.append(str(widget.cget('text')))
            for child in widget.winfo_children():values.extend(visible_text(child))
            return values
        shown='\n'.join(visible_text(agent_page.bubbles))
        assert 'A群输入' in shown and 'A群的回答' in shown
        assert '同一会话中的新闻结果' in shown and agent_page._selected=='__all__'
        assert 'B群不可串入A群' not in shown
        agent_page.tasks.selection_clear(0,'end')
        agent_page.tasks.selection_set(1)
        agent_page.select_task()
        assert 'A群输入' not in '\n'.join(visible_text(agent_page.bubbles))
        agent_page.follow_latest()
        assert 'A群输入' in '\n'.join(visible_text(agent_page.bubbles))
        agent_page.clear_view()
        assert not agent_page._ids
        assert RUNTIME.call_snapshot('独立聊天A','ui_agent_a'), 'clearing the view must retain model diagnostics'
        service.clear_context('独立聊天A')
        assert not RUNTIME.session_snapshot('独立聊天A','ui_agent_a')
        app.show_page('home')
        root.update()  # Map the canvas before generating a real click event.
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
        assert reply_page.web_search_enabled.get()
        assert reply_page.agent_enabled.get()
        assert reply_page.baidu_baike_enabled.get()
        assert reply_page.agent_max_steps.get() == '4'
        assert reply_page.agent_timeout.get() == '90'
        assert reply_page.web_test_query.get() == 'Python 装饰器'
        reply_page.web_test_query.set('')
        with patch.object(service, 'test_web_search') as probe:
            reply_page.test_web_search()
            probe.assert_not_called()
        entered_web, release_web = threading.Event(), threading.Event()
        def fake_web(query):
            assert query == 'Python 装饰器'
            entered_web.set()
            release_web.wait(5)
            return {'ok': True, 'message': '已读取网页并验证入库'}
        reply_page.web_test_query.set('Python 装饰器')
        with patch.object(service, 'test_web_search', side_effect=fake_web) as probe:
            reply_page.test_web_search()
            assert entered_web.wait(3) and reply_page.web_test_btn._busy
            reply_page.test_web_search()
            assert probe.call_count == 1
            app.show_page('home')
            release_web.set()
            deadline = time.monotonic() + 5
            while reply_page._web_test_busy and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            assert not reply_page._web_test_busy and not reply_page.web_test_btn._busy
            assert reply_page.web_test_status.cget('text') == '已读取网页并验证入库'
        assert reply_page.idle_chat_enabled.get()
        assert float(reply_page.idle_chat_probability.get()) == 3
        assert reply_page.idle_chat_minutes.get() == '30'
        assert reply_page.idle_video_enabled.get()
        assert reply_page.idle_video_topics.get() == '蔚蓝档案'
        assert float(reply_page.idle_video_probability.get()) == 100
        assert reply_page.idle_video_manual_chat.get() == 'test'
        reply_page.idle_video_manual_chat.set('')
        with patch.object(service, 'send_manual_idle_video') as send:
            reply_page.send_manual_idle_video()
            send.assert_not_called()
        entered_video, release_video = threading.Event(), threading.Event()
        def fake_video(chat):
            assert chat == '可设置的视频推送群'
            entered_video.set()
            release_video.wait(5)
            return {'ok': True, 'message': '视频推送已完成'}
        reply_page.idle_video_manual_chat.set('可设置的视频推送群')
        with patch.object(service, 'send_manual_idle_video', side_effect=fake_video) as send:
            reply_page.send_manual_idle_video()
            assert entered_video.wait(3) and reply_page.manual_video_btn._busy
            reply_page.send_manual_idle_video()
            assert send.call_count == 1
            app.show_page('home')
            release_video.set()
            deadline = time.monotonic() + 5
            while reply_page._manual_video_busy and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            assert not reply_page._manual_video_busy and not reply_page.manual_video_btn._busy
            assert reply_page.manual_video_status.cget('text') == '视频推送已完成'
        assert ConfigStore(config.path).bot['idle_video_manual_chat'] == '可设置的视频推送群'
        reply_page.idle_video_topics.set('蔚蓝档案，明日方舟,蔚蓝档案')
        reply_page.idle_video_probability.set('65')
        reply_page.idle_chat_probability.set('7.5')
        reply_page.idle_chat_minutes.set('45')
        reply_page.save()
        saved_bot = ConfigStore(config.path).bot
        assert saved_bot['idle_chat_probability'] == .075 and saved_bot['idle_chat_minutes'] == 45
        assert saved_bot['idle_video_topics'] == ['蔚蓝档案', '明日方舟']
        assert saved_bot['idle_video_probability'] == .65
        assert saved_bot['web_search_enabled'] and saved_bot['web_search_test_query'] == 'Python 装饰器'
        assert saved_bot['agent_enabled'] and saved_bot['agent_max_steps'] == 4
        reply_page.baidu_baike_enabled.switch._toggle()
        reply_page.save()
        assert not ConfigStore(config.path).bot['baidu_baike_enabled']
        reply_page.baidu_baike_enabled.switch._toggle()
        reply_page.save()
        assert ConfigStore(config.path).bot['baidu_baike_enabled']
        assert saved_bot['agent_timeout_seconds'] == 90
        reply_page.web_search_enabled.switch._toggle()
        reply_page.save()
        assert ConfigStore(config.path).bot['web_search_enabled'] is False
        reply_page.web_search_enabled.switch._toggle()
        reply_page.save()
        assert ConfigStore(config.path).bot['web_search_enabled'] is True
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
        reply_page.idle_video_manual_chat.set('主题界面群A')
        reply_page.idle_video_manual_chat.event_generate('<<ComboboxSelected>>')
        assert reply_page.manual_video_topics.get() == ''
        reply_page.manual_video_topics.set('人工智能与计算机')
        reply_page.save()
        assert ConfigStore(config.path).target_by_name('主题界面群A')['idle_video_topics'] == ['人工智能与计算机']
        reply_page.idle_video_manual_chat.set('主题界面群B')
        reply_page.idle_video_manual_chat.event_generate('<<ComboboxSelected>>')
        assert reply_page.manual_video_topics.get() == '明日方舟'
        reply_page.manual_video_topics.set('AI和人工智能')
        reply_page.idle_video_topics.set('Python；计算机')
        with patch.object(service, 'send_manual_idle_video', return_value={'ok': True, 'message': '按本群主题推送完成'}) as push:
            reply_page.send_manual_idle_video()
            deadline = time.monotonic() + 3
            while reply_page._manual_video_busy and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            push.assert_called_once_with('主题界面群B')
            assert not reply_page._manual_video_busy
        saved = ConfigStore(config.path)
        assert saved.target_by_name('主题界面群A')['idle_video_topics'] == ['人工智能与计算机']
        assert saved.target_by_name('主题界面群B')['idle_video_topics'] == ['AI和人工智能']
        assert saved.bot['idle_video_topics'] == ['Python', '计算机']
        sample_target = {"id": "test", "name": "文件传输助手", "type": "friend",
                         "enabled": True, "persona_id": "arona", "require_at": True,
                         "context_limit": 30, "reply_probability": 1.0, "note": ""}
        global_voice = config.bot['voice_name']
        persona_dialog = PersonaDialog(root, app.pages['personas'], personas.get('plana'))
        persona_dialog.voice_preset.set(next(k for k, v in persona_dialog._voice_choices.items() if v == 'plana'))
        persona_dialog.voice_rate.set('-12%')
        persona_dialog.voice_volume.set('-4')
        persona_dialog.address_template.set('{nickname}博士')
        persona_dialog._save()
        saved_persona = PersonaStore(personas.path).get('plana')
        assert saved_persona['voice_preset_id'] == 'plana'
        assert saved_persona['voice_rate'] == '-12%' and saved_persona['voice_volume'] == '-4%'
        assert saved_persona['address_template'] == '{nickname}博士'
        assert config.bot['voice_name'] == global_voice
        for identity in ('same_a', 'same_b'):
            personas.upsert({'id':identity,'name':'同名角色','system_prompt':'自然回复。'})
        page_targets._refresh_personas()
        page_targets.persona_combo.current(page_targets._persona_ids.index('same_b'))
        page_targets._refresh_personas()
        assert page_targets._persona_ids[page_targets.persona_combo.current()] == 'same_b'
        duplicate_target = config.add_target('同名角色选择验证', 'group', persona_id='same_b')
        duplicate_dialog = TargetDialog(root, page_targets, duplicate_target)
        assert duplicate_dialog._persona_ids[duplicate_dialog.persona.current()] == 'same_b'
        assert len(set(duplicate_dialog.persona['values'])) == len(duplicate_dialog.persona['values'])
        duplicate_dialog._save()
        assert config.target_by_name('同名角色选择验证')['persona_id'] == 'same_b'
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
        role_saved = []
        role_dialog = VoicePresetDialog(root, {
            **service.voice_presets.get('plana'), 'engine': 'gpt_sovits',
            'rate': '+0%', 'pitch': '+0Hz', 'volume': '+0%',
        }, role_saved.append)
        assert not role_dialog.voice.winfo_manager() and not role_dialog.pitch.winfo_manager()
        role_dialog.rate.set('-6%')
        role_dialog.save()
        assert role_saved and role_saved[0]['engine'] == 'gpt_sovits'
        assert role_saved[0]['rate'] == '-6%'
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
