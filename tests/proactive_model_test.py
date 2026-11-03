"""Active groups use the connected model's proactive decision."""
import tempfile
import time
import sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.wechat_adapter import SendResult
from tests.service_test import FakeAdapter

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    config = ConfigStore(base/'config.json')
    config.add_target('test', 'group', 'arona')
    config.bot.update(idle_chat_enabled=True, auto_reply=True, idle_chat_probability=0,
                      global_cooldown=0, per_chat_cooldown=0, idle_video_enabled=False)
    memory = MemoryStore(base/'memory.db')
    training = TrainingStore(base/'training.db', base/'files')
    service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                         memory, training, admin_state_path=base/'admin.json')
    adapter = FakeAdapter(config, service._on_incoming)
    service._adapter, service._running = adapter, True
    service._sync_idle_groups(adapter)
    state = service._idle_chats['test']
    state.update(last=time.monotonic(), sent=True)
    try:
        memory.add_user('test', '我喜欢蔚蓝档案的活动剧情', sender='测试甲')
        memory.add_user('other', '其他群独有的话题', sender='测试乙')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch('app.service.make_client') as client, patch.object(service, '_record_usage'):
            client.return_value.chat.return_value = SimpleNamespace(text='[SEARCH:蔚蓝档案 测试甲]')
            assert service._video_search_topics(['蔚蓝档案'], 'test') == ['蔚蓝档案']
            model_input = client.return_value.chat.call_args.args[0][0]['content']
            assert '活动剧情' in model_input and '其他群独有' not in model_input
        candidate = SimpleNamespace(topic='蔚蓝档案', title='游戏活动', description='活动内容')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch('app.service.make_client') as client, patch.object(service, '_record_usage'):
            client.return_value.chat.return_value = SimpleNamespace(text='SHARE')
            assert service._approve_video(candidate, {}, 'test')
            client.return_value.chat.return_value = SimpleNamespace(text='SKIP')
            assert not service._approve_video(candidate, {}, 'test')
            client.return_value.chat.side_effect = RuntimeError('offline')
            assert not service._approve_video(candidate, {}, 'test')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='WAIT') as decision:
            service._idle_tick()
            service._idle_tick()
            assert not adapter.sent
            assert decision.call_count == 1, 'unchanged context must not repeatedly call the model'
        memory.add_user('test', '继续聊活动剧情吧', sender='测试甲')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='TEXT'):
            service._idle_tick()
            assert len(adapter.sent) == 1 and adapter.sent[0][0] == 'test'
        adapter.sent.clear()
        page = SimpleNamespace(url='https://example.org/article', title='主题文章')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='ARTICLE'), \
                patch.object(service.proactive_articles, 'pick', return_value=(page, '有点意思。')), \
                patch.object(service.proactive_articles, 'mark_sent') as mark:
            service._idle_tick()
            assert adapter.sent[0] == ('test', page.url)
            mark.assert_called_once_with('test', page.url)
        adapter.sent.clear()
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='IMAGE'), \
                patch('app.service.download_image', side_effect=OSError('offline')), \
                patch.object(service.proactive_articles, 'pick_image',
                             return_value=('https://example.org/image.png', '有点意思。', page.url)), \
                patch.object(service.proactive_articles, 'mark_sent') as mark:
            service._idle_tick()
            assert adapter.sent[0] == ('test', 'https://example.org/image.png')
            assert mark.call_count == 2
        adapter.sent.clear()
        for decision, kind in [('IMAGE', 'image'), ('SCREENSHOT', 'webpage-screenshot')]:
            media_path = base / (kind+'.png')
            media_path.write_bytes(b'fixture')
            with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                    patch.object(service, '_proactive_decision', return_value=decision), \
                    patch.object(service.proactive_articles, 'pick', return_value=(page, '有点意思。')), \
                    patch.object(service.proactive_articles, 'pick_image', return_value=('https://example.org/image.png', '有点意思。', page.url)), \
                    patch('app.service.download_image', return_value=media_path), \
                    patch('app.service.capture_page', return_value=media_path), \
                    patch.object(service.proactive_articles, 'mark_sent'):
                service._idle_tick()
                assert adapter.files[-1] == str(media_path)
                assert memory.recent('test')[-1]['meta']['web_media']['kind'] == kind
                assert not media_path.exists(), 'temporary media cleaned after sending'
            adapter.sent.clear()
        fallback_path = base/'fallback.png'
        fallback_path.write_bytes(b'fixture')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='SCREENSHOT'), \
                patch.object(service.proactive_articles, 'pick', return_value=(page, '有点意思。')), \
                patch('app.service.capture_page', return_value=fallback_path), \
                patch.object(adapter, 'send_sticker', return_value=SendResult(False, 'failed')), \
                patch.object(service.proactive_articles, 'mark_sent'):
            service._idle_tick()
            assert adapter.sent[0] == ('test', page.url)
            assert 'web_media' not in memory.recent('test')[-1]['meta']
            assert not fallback_path.exists()
        adapter.sent.clear()
        def changed(*args):
            state['revision'] += 1
            return 'TEXT'
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', side_effect=changed):
            service._idle_tick()
            assert not adapter.sent
        memory.add_user('test', '换一个话题吧', sender='测试甲')
        with patch.object(config, 'active_profile', return_value={'provider':'openai'}), \
                patch.object(service, '_proactive_decision', return_value='ERROR') as decision:
            service._idle_tick()
            service._idle_tick()
            assert decision.call_count == 1, 'model errors must back off instead of polling'
            state['model_retry_after'] = 0
            service._idle_tick()
            assert decision.call_count == 2, 'a failed decision can retry after backoff'
            assert not adapter.sent
    finally:
        service.stop()
        memory.close()
        training.close()
print('PROACTIVE MODEL TEST PASSED')
