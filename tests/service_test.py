"""End-to-end service test with a fake WeChat transport and the demo model."""

from __future__ import annotations

import sys
import tempfile
import time
import threading
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.service as service_module
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import BaseWeChatAdapter, IncomingMessage, SendResult


class FakeAdapter(BaseWeChatAdapter):
    instances: list["FakeAdapter"] = []

    def __init__(self, config, on_message, on_status=None) -> None:
        super().__init__(config, on_message, on_status)
        self.sent: list[tuple[str, str]] = []
        self.files: list[str] = []
        self.backend_label = "fake · UI Automation"
        self.self_name = "阿洛娜"
        self.connected = True
        FakeAdapter.instances.append(self)

    def start(self, targets):
        self._targets = {t["name"]: t for t in targets}
        self.connected = True

    def send_text(self, chat, text):
        self.sent.append((chat, text))
        return SendResult(True, "fake send")

    def send_sticker(self, chat, path):
        self.files.append(str(path))
        return SendResult(True, "fake sticker")

    def fetch_recent(self, chat, limit):
        return []

    def probe(self):
        return True, "fake online"

    def stop(self):
        self.connected = False


def wait_event(service: BotService, kind: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event_kind, data = service.events.get(timeout=0.2)
        except Exception:
            continue
        if event_kind == kind:
            return data
    raise AssertionError(f"event {kind} not seen")


def test_idle_chat() -> None:
    """Deterministic idle scheduling, privacy gates, activity races and retries."""
    from app.idle_chat import conversation_starter
    from datetime import datetime
    from app.config_store import DEFAULT_CONFIG
    assert DEFAULT_CONFIG['bot']['idle_chat_probability'] == .03
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base / 'config.json')
        for value, expected in [('bad', .03), (None, .03), (float('nan'), .03),
                                (float('inf'), .03), (-1, 0), (2, 1), (0, 0), (.075, .075)]:
            config.bot['idle_chat_probability'] = value
            config.save()
            assert ConfigStore(config.path).bot['idle_chat_probability'] == expected
        for value, expected in [('bad', 30), (None, 30), (0, 1), (2000, 1440)]:
            config.bot['idle_chat_minutes'] = value
            config.save()
            assert ConfigStore(config.path).bot['idle_chat_minutes'] == expected
        config.bot.update(idle_chat_enabled=True, idle_chat_probability=.03, idle_chat_minutes=30,
                          global_cooldown=0, per_chat_cooldown=0, auto_reply=True, idle_video_enabled=False)
        group = config.add_target('test', 'group', 'arona')
        config.add_target('friend', 'friend', 'arona')
        config.add_target('disabled', 'group', 'arona')
        group = config.target_by_name('test')
        disabled = config.target_by_name('disabled')
        disabled['enabled'] = False
        memory = MemoryStore(base / 'memory.db')
        training = TrainingStore(base / 'training.db', base / 'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        adapter = FakeAdapter(config, service._on_incoming)
        service._adapter, service._running = adapter, True
        clock = [1000.0]
        epoch = time.time()
        def wall(): return epoch + clock[0] - 1000
        def incoming(text='大家好', **kwargs):
            return IncomingMessage('test', '群友', text, chat_type='group', received_at=wall(), **kwargs)
        try:
            with patch.object(service_module.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(service_module.time, 'time', side_effect=wall):
                service._idle_tick()
                assert set(service._idle_chats) == {'test'} and not adapter.sent
                clock[0] += 1799
                with patch.object(service_module.random, 'random') as draw:
                    service._idle_tick()
                    draw.assert_not_called()
                clock[0] += 1
                with patch.object(service_module.random, 'random', return_value=.031) as draw:
                    service._idle_tick()
                    assert draw.call_count == 1 and not adapter.sent
                    clock[0] += 1
                    service._idle_tick()
                    assert draw.call_count == 1, 'no draw on every supervisor tick'
                clock[0] += 299
                with patch.object(service_module.random, 'random') as draw:
                    service._idle_tick()
                    draw.assert_not_called()
                    clock[0] += 1499
                    service._idle_tick()
                    draw.assert_not_called()
                clock[0] += 1
                with patch.object(service_module.random, 'random', return_value=.031) as draw:
                    service._idle_tick()
                    assert draw.call_count == 1 and not adapter.sent  # 60 minutes
                    clock[0] += 1800
                    service._idle_tick()
                    assert draw.call_count == 2 and not adapter.sent  # 90 minutes
                    clock[0] += 1799
                    service._idle_tick()
                    assert draw.call_count == 2
                    clock[0] += 1
                    draw.return_value = .029
                    service._idle_tick()
                    assert draw.call_count == 3  # 120 minutes, first successful draw
                assert len(adapter.sent) == 1 and len(adapter.sent[0][1]) <= 23
                assert memory.recent('test')[-1]['meta']['proactive']
                assert not adapter.files and not service._followups
                clock[0] += 3600
                with patch.object(service_module.random, 'random') as draw:
                    service._idle_tick()
                    draw.assert_not_called()
                assert len(adapter.sent) == 1, 'one message per uninterrupted silence'
                # Echo/old/system events do not reopen the idle episode.
                service._note_idle_incoming(incoming(adapter.sent[-1][1], direction='out', origin='bot_echo'))
                service._note_idle_incoming(incoming('系统', direction='system'))
                old = incoming('旧消息'); old.received_at -= 10000
                service._note_idle_incoming(old)
                assert service._idle_chats['test']['sent']
                # An ordinary non-trigger message at ingress resets the timer.
                clock[0] += 1
                message = incoming(external_id='new-human')
                service._on_incoming(message)
                assert not service._idle_chats['test']['sent']
                clock[0] += 1800
                with patch.object(service_module.random, 'random') as draw:
                    service._idle_tick()
                    draw.assert_not_called()  # still queued / waiting for its reply
                assert service._incoming.get_nowait() is message
                # Disabled/quiet/admin/paused/disconnected/busy all block.
                for field in ('idle_chat_enabled', 'auto_reply'):
                    config.bot[field] = False
                    service._idle_tick()
                    config.bot[field] = True
                with patch.object(service, '_in_quiet_hours', return_value=True): service._idle_tick()
                service._admin_sessions[('test', 'admin')] = {}
                service._idle_tick(); service._admin_sessions.clear()
                service._muted_until['test'] = wall()+600
                service._idle_tick(); service._muted_until.clear()
                with patch.object(adapter, 'connected', False): service._idle_tick()
                service._reply_busy = True
                service._idle_tick(); service._reply_busy = False
                config.bot['idle_chat_probability'] = 0
                service._idle_tick(); config.bot['idle_chat_probability'] = 1
                assert len(adapter.sent) == 1
                # New activity during preparation cancels the pending send.
                def activity_race(*args):
                    clock[0] += 1
                    service._note_idle_incoming(incoming('新消息'))
                    return '各位老师，早上好。'
                with patch.object(service_module, 'conversation_starter', side_effect=activity_race):
                    service._idle_tick()
                assert len(adapter.sent) == 1
                clock[0] += 1800
                # A failed send does not consume the episode; retry is delayed.
                with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')) as send:
                    service._idle_tick(); service._idle_tick()
                    assert send.call_count == 1 and not service._idle_chats['test']['sent']
                clock[0] += 1800
                service._idle_tick()
                assert len(adapter.sent) == 2 and service._idle_chats['test']['sent']
                assert adapter.sent[-1][0] == 'test'
                # Removed targets lose eligibility. Re-enabling starts a full wait.
                group['enabled'] = False
                service._idle_tick()
                assert not service._idle_chats
                group['enabled'] = True
                service._idle_tick()
                assert not service._idle_chats['test']['sent'] and len(adapter.sent) == 2
                group2 = config.add_target('alias', 'group', 'arona')
                with patch.object(adapter, 'canonical_chat', side_effect=lambda name: 'test' if name=='alias' else name):
                    service._idle_tick()
                    assert set(service._idle_chats) == {'test'}, 'aliases must share one timer'
                group2['enabled'] = False
                service._idle_tick()
                clock[0] += 1800
                memory.add_user('test', '我很难过，家人生病了', sender='群友')
                service._idle_tick()
                assert len(adapter.sent) == 2, 'do not cheer over serious context'
                # A custom period is also the retry period. New activity during
                # a missed cycle cancels its deadline and starts a full wait.
                config.bot.update(idle_chat_minutes=2, idle_chat_probability=.03)
                clock[0] += 1
                service._note_idle_incoming(incoming('普通群消息', external_id='custom-period-start'))
                with patch.object(service_module.random, 'random', return_value=.99) as draw:
                    clock[0] += 119
                    service._idle_tick()
                    draw.assert_not_called()
                    clock[0] += 1
                    service._idle_tick()
                    assert draw.call_count == 1
                    clock[0] += 60
                    service._note_idle_incoming(incoming('又一条消息', external_id='custom-period-reset'))
                    clock[0] += 119
                    service._idle_tick()
                    assert draw.call_count == 1, 'new message must reset the missed deadline'
                    clock[0] += 1
                    service._idle_tick()
                    assert draw.call_count == 2
                    clock[0] += 120
                    service._idle_tick()
                    assert draw.call_count == 3 and len(adapter.sent) == 2
                service.stop()
                assert not service._idle_chats
        finally:
            service.stop()
            memory.close(); training.close()
    for hour in (2, 8, 12, 16, 20):
        with patch('app.idle_chat.datetime') as date:
            date.now.return_value = datetime(2026, 11, 2, hour)
            for persona in ({'id':'arona','name':'阿洛娜'}, {'id':'plana','name':'普拉娜'}):
                for _ in range(8):
                    text = conversation_starter(persona)
                    assert text and len(text) <= 23
                    assert not any(word in text for word in ('最近', '想聊聊', '来冒个泡', '开心的事', '?', '？'))
                    if hour == 2:
                        assert '早上好' not in text
    assert not conversation_starter({'name':'阿洛娜'}, max_chars=1), 'never truncate a sentence'


def test_idle_videos() -> None:
    import urllib.error
    from email.message import Message
    import app.bilibili_video as videos
    from app.news import NewsService
    stamp = int(time.time()) - 30
    row = {'bvid': 'BV1hb4c64EBb', 'pubdate': stamp, 'title': '主题视频', 'tag': '蔚蓝档案'}
    detail = {'bvid': row['bvid'], 'aid': 123456, 'title': '主题视频', 'pubdate': stamp, 'state': 0}
    video = videos.Video(row['bvid'], detail['aid'], detail['title'], '蔚蓝档案', stamp,
                         'https://b23.tv/Abc1234')
    def api(path, params, **kwargs):
        if path.endswith('/type'):
            assert params['keyword'] == '蔚蓝档案' and params['search_type'] == 'video'
            return {'result': [row]}
        assert path.endswith('/view') and params['bvid'] == row['bvid']
        return detail
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        store = videos.BilibiliVideoStore(base/'videos.json')
        with patch.object(videos, '_api_json', side_effect=api), \
                patch.object(videos, '_verified_short_link', return_value=video.url):
            assert store.pick(['蔚蓝档案'], 'test') == video
            assert not store.state_path.exists(), 'selection must not mark a failed/cancelled send'
            store.mark_sent('test', video)
            assert not store.pick(['蔚蓝档案'], 'test')
            assert store.pick(['蔚蓝档案'], 'another') == video
            assert not videos.BilibiliVideoStore(store.state_path).pick(['蔚蓝档案'], 'test')
        for bad_row in ({**row, 'tag': '其他主题'}, {**row, 'pubdate': 0},
                        {**row, 'pubdate': stamp-8*86400}, {**row, 'pubdate': stamp+3600},
                        {**row, 'is_pay': 1}, {**row, 'bvid': 'https://evil.example/'}):
            isolated = videos.BilibiliVideoStore(base/'none.json')
            with patch.object(videos, '_api_json', return_value={'result': [bad_row]}) as request:
                assert not isolated.pick(['蔚蓝档案'], 'test') and request.call_count == 1
        with patch.object(videos, '_api_json', side_effect=OSError('offline')):
            assert not videos.BilibiliVideoStore(base/'none.json').pick(['蔚蓝档案'], 'test')
        with patch.object(videos, '_api_json', side_effect=[{'result':[row]}, {**detail, 'state':-4}]):
            assert not videos.BilibiliVideoStore(base/'none.json').pick(['蔚蓝档案'], 'test')
        # Official short links must redirect to exactly the verified BV or AV.
        for destination, expected in (
            ('https://www.bilibili.com/video/'+video.bvid+'?share=1', video.url),
            ('https://www.bilibili.com/video/av123456', video.url),
            ('https://www.bilibili.com/video/BV1gb4c64EL6', ''),
            ('https://evil.example/video/'+video.bvid, ''),
            ('https://www.bilibili.com@evil.example/video/'+video.bvid, ''),
        ):
            headers = Message(); headers['Location'] = destination
            error = urllib.error.HTTPError(video.url, 302, 'redirect', headers, None)
            with patch.object(videos, '_api_json', return_value={'content':'标题 '+video.url}), \
                    patch.object(videos.urllib.request, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                assert videos._verified_short_link(video) == expected
        with patch.object(videos, '_api_json', return_value={'content':video.url+'longer'}):
            assert not videos._verified_short_link(video), 'never truncate a long URL'

        config = ConfigStore(base/'config.json')
        assert config.bot['idle_video_topics'] == ['蔚蓝档案']
        config.bot['idle_video_topics'] = '蔚蓝档案，Python\nPython'
        config.save()
        assert config.bot['idle_video_topics'] == ['蔚蓝档案', 'Python']
        config.bot['idle_video_topics'] = []
        config.bot['idle_video_probability'] = float('nan')
        config.save()
        assert config.bot['idle_video_topics'] == ['蔚蓝档案'] and config.bot['idle_video_probability'] == 1
        config.set_active_profile('demo-local')
        config.add_target('test', 'group', 'arona')
        config.bot.update(idle_chat_enabled=True, idle_chat_probability=1, idle_chat_minutes=1,
                          idle_video_enabled=True, idle_video_probability=1, replace_punctuation=True,
                          auto_reply=True, reply_delay_min=0, reply_delay_max=0,
                          global_cooldown=0, per_chat_cooldown=0, auto_sticker=False)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        service.idle_videos = videos.BilibiliVideoStore(base/'sent.json')
        adapter = FakeAdapter(config, service._on_incoming)
        service._adapter, service._running = adapter, True
        service._sync_idle_groups(adapter)
        def eligible():
            state = service._idle_chats['test']
            state.update(last=time.monotonic()-61, next_check=0, sent=False)
            return state
        try:
            # Natural comments are complete 15-char sentences, at most two
            # comment bubbles so the URL plus comments never exceeds three.
            with patch.object(config, 'active_profile', return_value={'provider':'openai','model':'fixture','timeout':30}), \
                    patch.object(service_module, 'make_client') as factory:
                for raw, count in [('这个主题挺有意思。', 1),
                                   ('这个标题有点反差。新角色真可爱。', 2),
                                   ('有点意思。标题挺特别。还真有点好奇呢。', 1),
                                   ('这是一条非常非常长且不能随意切断的评论。', 1),
                                   ('我刚看完，剪辑非常棒。', 1), ('这个主题真的很', 1)]:
                    factory.return_value.chat.return_value = LLMResult(raw, 'fixture', 'openai', 1)
                    comments = service._idle_video_comments(video, service.personas.get('arona'), 'test')
                    assert len(comments) == count and all(0 < len(s) <= 15 for s in comments)
                    if raw.startswith(('我刚看完', '这是一条', '这个主题真的很', '有点意思。标题')):
                        assert comments == ['有点意思呢。'], 'unusable comments must not be clipped or fabricated'
                factory.return_value.chat.side_effect = OSError('offline')
                assert service._idle_video_comments(video, service.personas.get('arona'), 'test') == ['有点意思呢。']
            with patch.object(service.idle_videos, 'pick', return_value=video) as pick, \
                    patch.object(service.idle_videos, 'mark_sent') as mark:
                # Ordinary replies and news must never request videos.
                service._handle_incoming(IncomingMessage('test', '群友', '@阿洛娜 早上好', chat_type='group'))
                service.news = NewsService(config, service.personas, state_path=base/'news.json')
                config.target_by_name('test').update(news_enabled=True, news_time='00:00')
                with patch.object(service.news, 'fetch', return_value={'国服':[], '日服':[], '国际服':[]}):
                    service._news_tick()
                pick.assert_not_called(); mark.assert_not_called()
                adapter.sent.clear()
                eligible()
                with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')):
                    service._idle_tick()
                mark.assert_not_called()
                # New activity while searching cancels sending and dedup writes.
                state = eligible()
                def race(*args):
                    with service._idle_lock:
                        state['revision'] += 1
                    return video
                with patch.object(service.idle_videos, 'pick', side_effect=race): service._idle_tick()
                mark.assert_not_called()
                assert not adapter.sent
                eligible()
                service._idle_tick()
                assert adapter.sent[0] == ('test', video.url), 'punctuation conversion must not corrupt links'
                assert len(adapter.sent) == 2 and len(adapter.sent[1][1]) <= 15
                mark.assert_called_once_with('test', video)
                assert memory.recent('test')[-1]['meta']['video']['bvid'] == video.bvid
                service._idle_tick()
                assert len(adapter.sent) == 2
                # Missing results gracefully become a short greeting, not a fabricated link.
                eligible()
                with patch.object(service.idle_videos, 'pick', return_value=None): service._idle_tick()
                assert len(adapter.sent) == 3 and not adapter.sent[-1][1].startswith('http')
                # A URL already delivered is recorded even if its comment fails;
                # do not retry the whole bundle or store an unsent comment.
                mark.reset_mock()
                eligible()
                with patch.object(adapter, 'send_text', side_effect=[SendResult(True), SendResult(False, 'offline')]) as send:
                    service._idle_tick()
                    service._idle_tick()
                    assert send.call_count == 2
                mark.assert_called_once_with('test', video)
                assert memory.recent('test')[-1]['meta']['bubbles'] == [video.url]
                mark.reset_mock()
                eligible()
                def changed_settings(*args):
                    config.bot['idle_video_topics'] = ['其他主题']
                    return ['有点意思。']
                with patch.object(service, '_idle_video_comments', side_effect=changed_settings), \
                        patch.object(adapter, 'send_text') as send:
                    service._idle_tick()
                    send.assert_not_called()
                    mark.assert_not_called()
            # Each canonical group supplies its own topics to the actual search
            # path; only groups left blank inherit the global list.
            from dataclasses import replace
            config.add_target('另一个测试群', 'group', 'arona')
            config.update_target(config.target_by_name('test')['id'], {'idle_video_topics':['Python']})
            config.update_target(config.target_by_name('另一个测试群')['id'], {'idle_video_topics':['明日方舟']})
            def all_eligible():
                service._sync_idle_groups(adapter)
                for state in service._idle_chats.values():
                    state.update(last=time.monotonic()-61, next_check=0, sent=False)
            def themed_video(topics, chat):
                return replace(video, topic=topics[0])
            with patch.object(service.idle_videos, 'pick', side_effect=themed_video) as search, \
                    patch.object(service, '_idle_video_comments', return_value=['有点意思。']):
                all_eligible()
                service._idle_tick()
                assert [c.args for c in search.call_args_list] == [(['Python'], 'test'), (['明日方舟'], '另一个测试群')]
                config.update_target(config.target_by_name('另一个测试群')['id'], {'idle_video_topics':[]})
                config.bot['idle_video_topics'] = ['音乐']
                search.reset_mock()
                all_eligible()
                service._idle_tick()
                assert [c.args for c in search.call_args_list] == [(['Python'], 'test'), (['音乐'], '另一个测试群')]
                config.update_target(config.target_by_name('另一个测试群')['id'], {'enabled':False})
                all_eligible()
                def group_topic_race(*args):
                    config.update_target(config.target_by_name('test')['id'], {'idle_video_topics':['新主题']})
                    return ['有点意思。']
                with patch.object(service, '_idle_video_comments', side_effect=group_topic_race), \
                        patch.object(adapter, 'send_text') as send, \
                        patch.object(service.idle_videos, 'mark_sent') as mark:
                    service._idle_tick()
                    send.assert_not_called()
                    mark.assert_not_called()
        finally:
            service.stop(); memory.close(); training.close()


def test_discussion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        config.set_active_profile('demo-local')
        config.add_target('test', 'group', 'arona')
        config.add_target('other', 'group', 'arona')
        config.bot.update(trigger_enabled=False, group_reply_probability=1, auto_reply=True,
                          global_cooldown=0, per_chat_cooldown=0)
        config.target_by_name('test')['group_reply_probability'] = 1
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        adapter = FakeAdapter(config, lambda _:None)
        service._adapter, service._running = adapter, True
        def turn(i, sender='甲', text='这个机制有点意思', **kwargs):
            return IncomingMessage('test', sender, text, chat_type='group', external_id=str(i), **kwargs)
        try:
            with patch.object(service, '_generate_and_send') as send:
                for i in range(15): service._handle_incoming(turn(i, ['甲','乙','丙','丁'][i%4]))
                send.assert_not_called()
                service._handle_incoming(turn(15,'甲'))
                assert send.call_count == 1 and send.call_args.kwargs['discussion_reply']
                assert not send.call_args.kwargs['force_reply']
                config.target_by_name('test')['group_reply_probability'] = 0
                service._handle_incoming(turn(16,'乙'))
                assert send.call_count == 1
                config.target_by_name('test')['group_reply_probability'] = 1
                service._handle_incoming(turn(17,'乙','@甲 请帮我看看'))
                service._handle_incoming(turn(18,'乙','甲，你帮我看看'))
                assert send.call_count == 1, 'explicit recipient must still be respected'
                assert not service._observe_discussion(turn(15,'甲')), 'duplicates do not extend a discussion'
                assert not service._observe_discussion(turn('echo',direction='out'))
                assert not service._observe_discussion(turn('old',received_at=time.time()-1000))
                service._discussions.clear(); send.reset_mock()
                for i, name in enumerate(['甲','乙','丙','丁']): service._handle_incoming(turn('people'+str(i),name))
                send.assert_not_called()
                service._handle_incoming(turn('people4','戊'))
                assert send.call_count == 1
                other = IncomingMessage('other','甲','同一个话题',chat_type='group',external_id='other1')
                assert not service._observe_discussion(other), 'groups must be isolated'
                service._discussions['test']['last'] -= 300
                assert not service._observe_discussion(turn('after-gap','甲'))
                assert service._discussions['test']['count'] == 1
            profile = {**config.active_profile(), 'provider':'openai'}
            with patch.object(config,'active_profile',return_value=profile), \
                    patch.object(service_module,'make_client') as factory:
                factory.return_value.chat.return_value = LLMResult('UNKNOWN','fixture','openai',1)
                assert service._group_audience(turn('unknown'),discussion=True) == 'ALL'
                factory.return_value.chat.return_value = LLMResult('OTHER','fixture','openai',1)
                assert service._group_audience(turn('private'),discussion=True) == 'OTHER'
            with patch.object(service_module,'make_client') as factory:
                factory.return_value.chat.return_value = LLMResult('这个机制挺有趣的。','fixture','mock',1)
                with patch.object(service.idle_videos,'pick') as video:
                    service._generate_and_send(turn('generation'),config.target_by_name('test'),0,
                                               '这个机制有点意思',discussion_reply=True)
                    assert '本轮允许参与公开讨论' in factory.return_value.chat.call_args.args[1]
                    video.assert_not_called()
        finally:
            service.stop()
            assert not service._discussions
            memory.close(); training.close()


def main() -> int:
    test_discussion()
    test_idle_chat()
    test_idle_videos()
    original_build = service_module.build_adapter
    service_module.build_adapter = lambda backend, config, on_message, on_status=None: FakeAdapter(
        config, on_message, on_status
    )
    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / "config.json")
        config.set_active_profile("demo-local")
        config.bot.update({"reply_delay_min": 0.0, "reply_delay_max": 0.0,
                           "per_chat_cooldown": 0.0, "global_cooldown": 0.0,
                           "sticker_probability": 1.0, "auto_sticker": True,
                           "context_limit": 30, "auto_reply": True,
                           "group_at_always_reply": True,
                           "group_probability_reply": True,
                           "group_reply_probability": 0.0,
                           "trigger_enabled": False})
        config.add_target("测试会话", "friend", "arona")
        group_target = config.add_target("测试群", "group", "arona", require_at=True)
        group_target["group_reply_probability"] = 0.0
        memory = MemoryStore(Path(tmp) / "test.db")
        # 25 historical turns prove the context window is populated.
        for i in range(25):
            memory.add_user("测试会话", f"历史消息 {i}", sender="老师",
                            external_id=f"wx:test-history:{i}")
            memory.add_assistant("测试会话", f"历史回复 {i}", profile_name="mock")
        training = TrainingStore(Path(tmp) / "training.db", Path(tmp) / "training_files")
        service = BotService(config, PersonaStore(), StickerStore(), memory, training)
        try:
            assert service.start(), service.state().get("last_error")
            adapter = FakeAdapter.instances[-1]
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="老师", content="阿洛娜，在吗？",
                msg_type="text", external_id="wx:test-new-1", attr="friend",
            ))
            reply_event = wait_event(service, "reply")
            assert reply_event["chat"] == "测试会话"
            assert adapter.sent, "reply text should be sent"
            assert "阿洛娜" in adapter.sent[-1][1]
            assert reply_event["context_count"] >= 20, "at least 20 context messages"
            assert service.memory.context_count("测试会话") >= 25

            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="老师", content="今天好累啊……",
                msg_type="text", external_id="wx:test-new-2", attr="friend",
            ))
            second = wait_event(service, "reply")
            assert second["sticker"], "mock tired reply should include a sticker"
            assert adapter.files, "sticker file should be sent through adapter"

            bot_text = adapter.sent[-1][1]
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content="自己的消息",
                msg_type="text", external_id="wx:test-self", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2, "self messages must be ignored"
            manual_count = service.memory.context_count("测试会话")
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content="这是老师手动补充的一句",
                msg_type="text", external_id="wx:test-manual", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2, "manually sent messages must not be answered"
            assert service.memory.context_count("测试会话") == manual_count + 1, \
                "manual outgoing message is stored as context"
            latest = service.memory.recent("测试会话", 1)[0]
            assert latest["sender"] == "测试账号" and latest["meta"]["manual"] is True
            # The UI echo of ARONA's own successful send must not be stored twice.
            echo_count = service.memory.context_count("测试会话")
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content=bot_text,
                msg_type="text", external_id="wx:test-echo", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2
            assert service.memory.context_count("测试会话") == echo_count, \
                "bot echo must not create duplicate context"

            # Whitelist and group @ filtering.
            adapter.on_message(IncomingMessage(
                chat="不在白名单", sender="陌生人", content="@阿洛娜 在吗",
                msg_type="text", external_id="wx:test-other", attr="friend",
            ))
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="大家早上好",
                msg_type="text", external_id="wx:test-group-1", attr="friend",
                chat_type="group",
            ))
            time.sleep(0.5)
            assert len(adapter.sent) == 2, "whitelist and group without @ must not reply"
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="@阿洛娜 今天天气真好",
                msg_type="text", external_id="wx:test-group-2", attr="friend",
                chat_type="group",
            ))
            group_reply = wait_event(service, "reply")
            assert group_reply["chat"] == "测试群"
            assert len(adapter.sent) == 3

            # With probability at 100%, a non-@ group message replies too.
            config.bot["group_reply_probability"] = 1.0
            group_target["group_reply_probability"] = 1.0
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="大家随便聊聊",
                msg_type="text", external_id="wx:test-group-3", attr="friend",
                chat_type="group",
            ))
            probability_reply = wait_event(service, "reply")
            assert probability_reply["chat"] == "测试群"
            assert len(adapter.sent) == 4

            # A 100% probability must not bypass confirmation of the addressee.
            for index, text in enumerate(['@张三 在吗', '你吃饭了吗', '@张三 阿洛娜好可爱']):
                count = memory.context_count('测试群')
                service._handle_incoming(IncomingMessage('测试群', '群友', text, chat_type='group',
                                                         external_id=f'audience-other-{index}'))
                assert len(adapter.sent) == 4
                assert memory.context_count('测试群') == count + 1

            # An @ still must get a reply even when the legacy @-always toggle
            # is off; the requirement is stronger than the old probability rule.
            config.bot["group_at_always_reply"] = False
            config.bot["group_reply_probability"] = 0.0
            group_target["group_reply_probability"] = 0.0
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="@阿洛娜 在吗",
                msg_type="text", external_id="wx:test-group-4", attr="friend",
                chat_type="group",
            ))
            time.sleep(0.4)
            assert len(adapter.sent) == 5, "@ must always get a reply"

            # Exercise the real generation and send path with unusable model output.
            config.bot.update({"auto_sticker": False, "voice_enabled": False,
                               "quote_reply": False, "max_reply_chars": 23})
            incoming = IncomingMessage(chat="测试群", sender="群友", content="@阿洛娜 帮帮我",
                                       msg_type="text", chat_type="group")
            class Client:
                def __init__(self, replies):
                    self.replies = iter(replies)

                def chat(self, *args):
                    value = next(self.replies)
                    if isinstance(value, Exception):
                        raise value
                    return LLMResult(value, "test", "mock", 1)

            def check_reply(replies, expected):
                before = len(adapter.sent)
                with patch.object(service_module, "make_client", return_value=Client(replies)):
                    service._generate_and_send(incoming, group_target, 1, "帮帮我", force_reply=True)
                assert [text for _, text in adapter.sent[before:]] == expected

            long_sentence = "这是一句没有合适断句位置而且长度明显超过限制的回答需要完整处理"
            check_reply([long_sentence, RuntimeError("rewrite failed")], ["抱歉，我现在答不上来。"])
            check_reply([long_sentence, ""], ["抱歉，我现在答不上来。"])
            check_reply(["第一句话是完整的。第二句话也是完整的。第三句话也完整。", ""],
                        ["第一句话是完整的。", "第二句话也是完整的。", "第三句话也完整。"])
            check_reply([RuntimeError("model failed")], ["抱歉，我现在答不上来。"])
            check_reply(["", ""], ["抱歉，我现在答不上来。"])

            # Third-person public discussion is eligible, but not a forced keyword call.
            config.bot.update(trigger_enabled=True, trigger_semantic_check=True,
                              group_reply_probability=1.0)
            group_target['group_reply_probability'] = 1.0
            before = len(adapter.sent)
            with patch.object(service, '_keyword_addressed', return_value=False):
                service._handle_incoming(IncomingMessage(
                    chat='测试群', sender='群友', content='阿洛娜好可爱', chat_type='group',
                    external_id='keyword-third-person'))
            assert len(adapter.sent) > before
            before = len(adapter.sent)
            group_target['group_reply_probability'] = 0.0
            service._handle_incoming(IncomingMessage('测试群', '群友', '阿洛娜好可爱', chat_type='group',
                                                     external_id='keyword-third-person-zero'))
            assert len(adapter.sent) == before
            group_target['group_reply_probability'] = 1.0
            memory.add_user('测试群', 'https://example.com/article', sender='群友')
            with patch.object(service_module, 'read_card', return_value='网页资料') as read:
                incoming.content = '阿洛娜看看这个'
                enriched = service._shared_content(incoming, incoming.content)
                assert '网页资料' in enriched
                assert read.call_args.args[0].url == 'https://example.com/article'
            # A semantic decision may accept a call that the old regex misses.
            profile = {**config.active_profile(), 'provider': 'openai'}
            with patch.object(config, 'active_profile', return_value=profile):
                with patch.object(service_module, 'make_client', return_value=Client(['YES'])):
                    assert service._keyword_addressed(incoming, ['阿洛娜'])
                with patch.object(service_module, 'make_client', return_value=Client(['NO'])):
                    assert not service._keyword_addressed(incoming, ['阿洛娜'])
                for decision, allowed in [('BOT', True), ('ALL', True), ('OTHER', False), ('UNKNOWN', False), ('YES', False)]:
                    candidate = IncomingMessage('测试群', '群友', '这个原理是什么', chat_type='group')
                    with patch.object(service_module, 'make_client', return_value=Client([decision])):
                        assert service._group_audience_allowed(candidate) == allowed
                with patch.object(service_module, 'make_client', return_value=Client([RuntimeError('offline')])):
                    assert not service._group_audience_allowed(candidate)
                    assert service._group_audience_allowed(IncomingMessage('测试群', '群友', '大家早上好', chat_type='group'))
                with patch.object(service_module, 'make_client') as client:
                    assert not service._group_audience_allowed(IncomingMessage('测试群', '群友', '@张三 你好', chat_type='group'))
                    client.assert_not_called()

            # Display punctuation replacement must not strip speech punctuation.
            config.bot.update(voice_enabled=True, voice_probability=1.0, replace_punctuation=True)
            with patch.object(service, '_send_voice_reply', return_value=True) as voice:
                check_reply(['我在呢，别着急。'], [])
                assert voice.call_args.args[1] == '我在呢，别着急。'

            # Explicit sound requests override automatic voice settings; the
            # selected chat, max length and text fallback remain enforced.
            config.bot.update(voice_enabled=False, voice_probability=0, voice_max_chars=30,
                              replace_punctuation=False)
            saved_content = incoming.content
            incoming.content = '阿洛娜，喵一声'
            with patch.object(service, '_send_voice_reply', return_value=True) as voice:
                check_reply(['喵～'], [])
                assert voice.call_args.args[0] == '测试群' and '喵' in voice.call_args.args[1]
            with patch.object(service, '_send_voice_reply', return_value=False) as voice:
                check_reply(['喵。'], ['喵。'])
                voice.assert_called_once()
            config.bot['voice_max_chars'] = 8
            with patch.object(service, '_send_voice_reply') as voice:
                check_reply(['这是一句完整的语音回复。'], ['这是一句完整的语音回复。'])
                voice.assert_not_called()
            config.bot['voice_max_chars'] = 30
            for request in ('阿洛娜，不要用语音回复我', '小明说“阿洛娜，喵一声”'):
                incoming.content = request
                with patch.object(service, '_send_voice_reply') as voice:
                    check_reply(['我在听呢。'], ['我在听呢。'])
                    voice.assert_not_called()
            incoming.content = saved_content
            incoming.content = '阿洛娜，不要发语音，只用文字回复'
            config.bot.update(voice_enabled=True, voice_probability=1)
            with patch.object(service, '_send_voice_reply') as voice:
                check_reply(['我在听呢。'], ['我在听呢。'])
                voice.assert_not_called()
            incoming.content = saved_content

            # Manual voice tests must use the selected (possibly unsaved) preset
            # even when automatic voice/probability settings are disabled.
            config.bot.update(voice_enabled=False, voice_probability=0)
            assert not service.send_test_voice('  ')['ok']
            with patch.object(service, '_send_voice_reply', return_value=True) as voice:
                before = len(adapter.sent)
                result = service.send_test_voice('test', preset_id='plana')
                assert result['ok'] and result['voice']
                assert len(adapter.sent) == before
                voice.assert_called_once_with('test', '你好，语音测试。', preset_id='plana')
            with patch.object(service, '_send_voice_reply', return_value=False):
                result = service.send_test_voice('test')
                assert not result['ok'] and result['fallback']
                assert adapter.sent[-1] == ('test', '你好，语音测试。')
                with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')):
                    assert not service.send_test_voice('test')['fallback']
            service._voice_test_lock.acquire()
            try:
                with patch.object(service, '_send_voice_reply') as voice:
                    assert not service.send_test_voice('test')['ok']
                    voice.assert_not_called()
            finally:
                service._voice_test_lock.release()
            with patch.object(adapter, 'connected', False), patch.object(adapter, 'send_text') as send:
                assert not service.send_test_voice('test')['ok']
                send.assert_not_called()

            # Test news uses the named group independently of scheduling, and
            # cancels a slow collection if connection changes before sending.
            assert not service.send_test_news('')['ok']
            with patch.object(adapter, 'connected', False):
                assert not service.send_test_news('test')['ok']
            def fake_news(sender, target=None, **kwargs):
                assert target == {'name': 'test', 'type': 'group'} and kwargs['test_mode']
                result = sender.send_text(target['name'], '新闻测试')
                return {'sent': result.ok, 'reason': result.message}
            with patch.object(service.news, 'run', side_effect=fake_news):
                assert service.send_test_news('test')['ok']
                assert adapter.sent[-1] == ('test', '新闻测试')
            def stopped_news(sender, **kwargs):
                with patch.object(service, '_running', False):
                    assert not sender.send_text('test', '取消发送').ok
                return {'sent': False, 'reason': 'cancelled'}
            with patch.object(service.news, 'run', side_effect=stopped_news), \
                    patch.object(adapter, 'send_text') as send:
                assert not service.send_test_news('test')['ok']
                send.assert_not_called()
            service._news_test_lock.acquire()
            try:
                assert not service.send_test_news('test')['ok']
            finally:
                service._news_test_lock.release()
            with patch.object(service.news, 'run', side_effect=RuntimeError('offline')):
                assert not service.send_test_news('test')['ok']
            assert not service._news_test_lock.locked()

            # TTS parameters, recording tail and UI lock apply to the real path.
            adapter._ui_lock = threading.RLock()
            voice_config = SimpleNamespace(tail_seconds=.3)
            spoken = SimpleNamespace(ok=True, voice='custom-id', total_seconds=1.2)
            with patch.object(adapter, 'ensure_chat_open', return_value=True), \
                    patch('wecvoice_auto.config.Config.load', return_value=voice_config), \
                    patch('wecvoice_auto.VoiceSender') as factory:
                service._voice_sender = None
                factory.return_value.send.side_effect = lambda *args, **kwargs: (
                    spoken if adapter._ui_lock._is_owned() else None)
                custom = service.voice_presets.get('plana')
                assert service.send_test_voice('test', preset_id='plana')['ok']
                kwargs = factory.return_value.send.call_args.kwargs
                assert all(kwargs[key] == custom[key] for key in ('voice', 'rate', 'pitch', 'volume'))
                assert kwargs['chat'] == 'test'
                assert voice_config.tail_seconds == 1.3 and voice_config.verify_chat
                checked_window = factory.call_args.kwargs['window']
                assert service.send_test_voice('test', preset_id='plana')['ok']
                assert service._voice_sender.window is not checked_window
                assert voice_config.tail_seconds == 1.3  # cached sender adds tail only once
                with patch('wecvoice_auto.wechat.WeChatWindow.current_chat', return_value=''):
                    try:
                        checked_window.ensure_chat('test')
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('unverified voice target must be rejected')
                with patch('wecvoice_auto.wechat.WeChatWindow.current_chat', return_value='群聊'), \
                        patch.object(adapter, 'is_same_chat', return_value=True) as same:
                    assert checked_window.ensure_chat('原群名') == '群聊'
                    same.assert_called_once_with('群聊', '原群名')
                with patch('wecvoice_auto.wechat.WeChatWindow.current_chat', return_value='其他群'), \
                        patch.object(adapter, 'is_same_chat', return_value=False):
                    try:
                        checked_window.ensure_chat('原群名')
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('a different group must still be rejected')
            with patch.object(adapter, 'ensure_chat_open', return_value=False), \
                    patch.object(service._voice_sender, 'send') as send:
                assert not service._send_voice_reply('test', '你好')
                send.assert_not_called()
            service._voice_sender = None
            # Successful @ opens a per-group dialogue, independent of probability
            # and cooldown; five consecutive unrelated incoming turns close it.
            service._followups.clear()
            config.bot.update(group_probability_reply=False, trigger_enabled=False,
                              per_chat_cooldown=600, global_cooldown=0,
                              voice_enabled=False, auto_sticker=False)
            def turn(text, uid, audience='UNKNOWN', chat='测试群', **kwargs):
                message = IncomingMessage(chat, '群友', text, chat_type='group', external_id=uid, **kwargs)
                with patch.object(service, '_group_audience', return_value=audience):
                    service._handle_incoming(message)
            before = len(adapter.sent)
            turn('@阿洛娜 在吗', 'followup-start')
            assert len(adapter.sent) > before and service._followup_active('测试群')
            before = len(adapter.sent)
            turn('那你再说说呢', 'followup-direct', 'BOT')
            assert len(adapter.sent) > before, 'confirmed follow-up ignores probability and cooldown'
            with patch.object(adapter, 'canonical_chat', side_effect=lambda chat: '测试群' if chat == '测试群备注' else chat):
                turn('还是在跟你聊', 'followup-alias', 'BOT', chat='测试群备注')
            assert not memory.recent('测试群备注', 1)
            assert '测试群备注' not in service._followups and '测试群' in service._followups
            for index in range(4):
                turn('@张三 在吗', f'followup-miss-{index}', 'OTHER')
            assert service._followups['测试群']['misses'] == 4
            turn('@张三 在吗', 'followup-miss-3', 'OTHER')
            assert service._followups['测试群']['misses'] == 4, 'duplicate delivery must not count twice'
            turn('手动发送的一句话', 'followup-manual', direction='out', origin='self')
            turn('过期消息', 'followup-stale', received_at=time.time()-660)
            assert service._followups['测试群']['misses'] == 4, 'self/old messages do not consume the dialogue'
            turn('继续跟你聊', 'followup-reset', 'BOT')
            assert service._followups['测试群']['misses'] == 0
            config.add_target('另一个测试群', 'group', 'arona')
            turn('@阿洛娜 在吗', 'followup-other-start', chat='另一个测试群')
            for index in range(5):
                turn('大家聊聊', f'followup-five-{index}', 'ALL')
            assert not service._followup_active('测试群')
            assert service._followups['另一个测试群']['misses'] == 0, 'groups must be isolated'
            before = len(adapter.sent)
            turn('还在对你说话', 'followup-closed', 'BOT')
            assert len(adapter.sent) == before, 'closed dialogue returns to normal triggers'
            turn('@阿洛娜 在吗', 'followup-reopen')
            assert service._followup_active('测试群')
            last = service._followups['测试群']['last_activity']
            with patch.object(service_module.time, 'monotonic', return_value=last + 300):
                assert not service._followup_active('测试群'), 'default timeout is five minutes'
            turn('@阿洛娜 在吗', 'followup-custom-timeout')
            config.bot['followup_timeout_minutes'] = 2
            last = service._followups['测试群']['last_activity']
            with patch.object(service_module.time, 'monotonic', return_value=last + 119):
                assert service._followup_active('测试群')
            with patch.object(service_module.time, 'monotonic', return_value=last + 120):
                assert not service._followup_active('测试群')
            service._followups.clear()
            with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')):
                turn('@阿洛娜 在吗', 'followup-send-failed')
            assert not service._followups, 'failed @ send must not open a dialogue'
        finally:
            service.stop()
            assert not service._followups
            service.memory.close()
            training.close()
    service_module.build_adapter = original_build
    print("BOT SERVICE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
