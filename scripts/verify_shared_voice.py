"""Explicit desktop checks restricted to test; never starts the bot service."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.link_reader import extract_card
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.wechat_adapter import WeChatAutoAdapter, IncomingMessage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['link', 'preview', 'voice', 'semantic'])
    parser.add_argument('--no-send', action='store_true')
    args = parser.parse_args()
    handler = logging.StreamHandler()
    logger = logging.getLogger('app.service')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    config = ConfigStore()
    if args.mode == 'semantic':
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory = MemoryStore(base / 'memory.db')
            training = TrainingStore(base / 'training.db', base / 'files')
            try:
                service = BotService(config, PersonaStore(), StickerStore(), memory, training,
                                     admin_state_path=base / 'admin.json')
                for text, expected in [('阿洛娜，帮我看看这个链接', True), ('阿洛娜好可爱', False),
                                       ('阿洛娜的卡池什么时候开', False), ('昨天我问阿洛娜在吗', False)]:
                    actual = service._keyword_addressed(
                        IncomingMessage('test', '调试', text, chat_type='group'), ['阿洛娜', 'Arona'])
                    print(text, actual)
                    assert actual == expected
                return 0
            finally:
                memory.close()
                training.close()
    if args.mode == 'preview':
        from app.voice_presets import VoicePresetStore
        from wecvoice_auto import VoiceSender
        for item in VoicePresetStore().list()[:2]:
            result = VoiceSender().send('你好', preview=True, **{
                k: item[k] for k in ('voice', 'rate', 'pitch', 'volume')})
            print(json.dumps(result.to_dict(), ensure_ascii=False))
            if not result.ok:
                return 1
        return 0
    adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
    try:
        adapter.start([{'name': 'test', 'type': 'group', 'enabled': True}])
        if not adapter.ensure_chat_open('test'):
            raise RuntimeError('无法确认 test 会话，取消发送')
        if adapter._uia.current_chat() != 'test':
            raise RuntimeError('当前会话不是 test，取消发送')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            local = ConfigStore(base / 'config.json')
            local.data = json.loads(json.dumps(config.data))
            local.bot.update(voice_enabled=False, auto_sticker=False, quote_reply=False,
                             reply_delay_min=0, reply_delay_max=0, global_cooldown=0)
            memory = MemoryStore(base / 'memory.db')
            training = TrainingStore(base / 'training.db', base / 'files')
            service = BotService(local, PersonaStore(), StickerStore(), memory, training,
                                 admin_state_path=base / 'admin.json')
            service._adapter = adapter
            service._running = True
            try:
                if args.mode == 'voice':
                    ok = service._send_voice_reply('test', '你好，今天也请多关照。')
                    print('TEST_VOICE_OK', ok)
                    return 0 if ok else 1
                users = adapter.target_usernames()
                user = next(iter(users))
                selected = None
                for row in adapter._db.get_messages(user, limit=30):
                    text = str(row.get('content') or '')
                    card = extract_card(text)
                    if card.url or card.channels:
                        selected = adapter._map_message(row, {'type': 'group'}, 'test')
                        break
                if selected is None:
                    selected = IncomingMessage('test', '调试', 'https://github.com/rany2/edge-tts', chat_type='group')
                    print('NO_RECENT_CARD: using public Edge TTS project page')
                card = extract_card(selected.content)
                print('SHARE_KIND', '视频号' if card.channels else '链接')
                print('SHARE_TITLE', card.title[:120])
                enriched = service._shared_content(selected, selected.content)
                print('READ_OK', '网页可见文字：' in enriched or card.channels)
                if args.no_send:
                    print('READ_EXCERPT', enriched[:2200])
                    from app.model_client import make_client
                    result = make_client(config.active_profile()).chat(
                        [{'role': 'user', 'content': enriched[:7000]}],
                        '你是阿洛娜。对分享内容发表一句自然中文评论，整条必须不超过23字。只根据读到的文字，不假装看过视频。')
                    print('MODEL_REPLY', result.text)
                    return 0
                row_id = memory.add_user('test', enriched, sender=selected.sender)
                target = {'type': 'group', 'name': 'test', 'persona_id': 'arona', 'context_limit': 20}
                selected.content = '链接读取测试'
                service._generate_and_send(selected, target, row_id, enriched, force_reply=True)
                print('TEST_REPLY_COUNT', service.state().get('replies'))
                print('TEST_REPLY', memory.recent('test', 1)[0].get('content', ''))
                return 0 if service.state().get('replies') else 1
            finally:
                service._running = False
                memory.close()
                training.close()
    finally:
        adapter.stop()


if __name__ == '__main__':
    raise SystemExit(main())
