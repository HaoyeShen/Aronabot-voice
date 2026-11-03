"""Exercise a synthetic @ through the real model/transport, only in test."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import configure_local_temp
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import WeChatAutoAdapter


def main():
    configure_local_temp()
    config = ConfigStore()
    target = {**(config.target_by_name('test') or {}),
              'name': 'test', 'type': 'group', 'enabled': True}
    adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        local = ConfigStore(base/'config.json')
        local.data = json.loads(json.dumps(config.data))
        local.data['targets'] = [target]
        local.bot.update(auto_reply=True, voice_enabled=False, auto_sticker=False,
                         quote_reply=False, quiet_hours_enabled=False, log_message_content=False,
                         group_reply_probability=0, reply_delay_min=0, reply_delay_max=0,
                         global_cooldown=0, per_chat_cooldown=600)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(local, PersonaStore(), StickerStore(), memory, training,
                             admin_state_path=base/'admin.json')
        try:
            adapter.start([target])
            assert adapter.ensure_chat_open('test') and adapter._uia.current_chat() == 'test'
            username = next(iter(adapter.target_usernames()))
            before = adapter._last_seq(username)
            raw = dict(username=username, local_id=1, shard='mention-regression',
                       sort_seq=time.time_ns(), create_time=int(time.time()),
                       sender_username='wxid_synthetic_debug', type='文本',
                       content=f'@{adapter.self_name} 在吗')
            memory.add_user('test', '历史测试记录', sender='调试',
                            external_id=f'wxdb:{username}:mention-regression:1')
            incoming = adapter._map_message(raw, target, 'test')
            incoming.sender = '调试'
            service._adapter, service._running = adapter, True
            service._handle_incoming(incoming)
            answers = [row for row in memory.recent('test', 10) if row['role'] == 'assistant']
            assert answers, 'No response was sent through the service'
            bubbles = answers[-1].get('meta', {}).get('bubbles') or [answers[-1]['content']]
            confirmed = False
            for _ in range(10):
                rows = adapter._db.get_messages(username, limit=20)
                sent = {str(row.get('content') or '').strip() for row in rows
                        if int(row.get('sort_seq') or 0) > before
                        and row.get('sender_username') == adapter.self_wxid}
                if all(bubble.strip() in sent for bubble in bubbles):
                    confirmed = True
                    break
                time.sleep(0.5)
            assert confirmed, 'Response not confirmed in test chat database'
            print('TEST_ONLY_SYNTHETIC_MENTION_REPLY_DB_CONFIRMED', confirmed)
            print('REPLY_BUBBLES', len(bubbles))
        finally:
            service.stop()
            adapter.stop()
            memory.close()
            training.close()


if __name__ == '__main__':
    main()
