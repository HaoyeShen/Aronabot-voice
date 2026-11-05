"""Reused WeChat local IDs must not suppress new @ messages."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.model_client import LLMResult
from app.wechat_adapter import WeChatAutoAdapter
from tests.service_test import FakeAdapter

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    config = ConfigStore(base/'config.json')
    config.bot["probability_first_reply"] = False  # Legacy wake-up regression coverage.
    config.set_active_profile('demo-local')
    target = config.add_target('test', 'group', 'arona')
    config.bot.update(auto_reply=True, group_reply_probability=0, voice_enabled=False, auto_sticker=False,
                      reply_delay_min=0, reply_delay_max=0, global_cooldown=0, per_chat_cooldown=600)
    memory = MemoryStore(base/'memory.db')
    training = TrainingStore(base/'training.db', base/'files')
    service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                         memory, training, admin_state_path=base/'admin.json')
    fake = FakeAdapter(config, service._on_incoming)
    fake.self_name = 'ARONA'
    service._adapter, service._running = fake, True
    mapper = WeChatAutoAdapter({}, lambda _:None)
    mapper.self_wxid = 'wxid_bot'
    row = {'username':'room@chatroom', 'shard':'message_0.db', 'local_id':11, 'sort_seq':200,
           'create_time':1700100000, 'sender_username':'wxid_friend', 'content':'@ARONA 在吗', 'type':'文本'}
    memory.add_user('test', '旧消息', sender='群友', external_id='wxdb:room@chatroom:message_0.db:11')
    try:
        with patch('app.service.make_client') as client:
            client.return_value.chat.return_value = LLMResult('我在呀。', 'fixture', 'mock', 1)
            incoming = mapper._map_message(row, target, 'test')
            import time
            incoming.received_at = time.time()
            service._handle_incoming(incoming)
            assert fake.sent == [('test', '我在呀。')]
            service._handle_incoming(incoming)
            assert len(fake.sent) == 1, 'same event must not reply twice'
            newer = mapper._map_message({**row, 'sort_seq':201, 'create_time':1700200000}, target, 'test')
            newer.received_at = time.time()
            service._handle_incoming(newer)
            assert len(fake.sent) == 2, 'new row with reused local ID must still reply'
            manual = mapper._map_message({**row, 'sender_username':'wxid_bot', 'sort_seq':202}, target, 'test')
            manual.received_at = time.time()
            service._handle_incoming(manual)
            assert len(fake.sent) == 2, 'self messages never trigger'
    finally:
        service.stop()
        memory.close()
        training.close()
print('MENTION DEDUPE TEST PASSED')
