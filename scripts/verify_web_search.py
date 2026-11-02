"""Live web/knowledge/model verification with an in-memory send sink, no WeChat."""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import queue
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import BaseWeChatAdapter, IncomingMessage, SendResult


class PreviewSink(BaseWeChatAdapter):
    def __init__(self):
        super().__init__({}, lambda _: None)
        self.connected, self.self_name, self.sent = True, '阿洛娜', []

    def send_text(self, chat, text):
        assert chat == 'test'
        self.sent.append(text)
        return SendResult(True)

    def stop(self):
        self.connected = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--learn', action='store_true', help='Store the manual public-source probe in the actual knowledge base')
    args = parser.parse_args()
    original = ConfigStore()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        config.data = copy.deepcopy(original.data)
        config.bot.update(auto_sticker=False, voice_enabled=False, quote_reply=False,
                          web_search_enabled=False, reply_delay_min=0, reply_delay_max=0,
                          global_cooldown=0, per_chat_cooldown=0)
        config.data['targets'] = [{'name': 'test', 'type': 'group', 'enabled': True, 'persona_id': 'arona'}]
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore() if args.learn else TrainingStore(base/'knowledge.db', base/'files')
        service = BotService(config, PersonaStore(), StickerStore(), memory, training,
                             admin_state_path=base/'admin.json')
        try:
            probe = service.test_web_search('Python 装饰器')
            print('LIVE_SEARCH_PROBE',probe['ok'],'SOURCES',len(probe.get('sources',[])),
                  'LEARNED',probe.get('learned',0),'DUPLICATES',probe.get('duplicates',0),flush=True)
            assert probe['ok'] and probe.get('sources')
            assert config.bot['web_search_enabled'] is False
            if args.learn:
                # Model verification uses only public material, never existing
                # private training documents from the user's knowledge base.
                service.training.close()
                service.training = TrainingStore(base/'model-knowledge.db', base/'model-files')
            config.bot['web_search_enabled'] = True
            sink = PreviewSink()
            service._adapter, service._running = sink, True
            question = '帮我查一下 Python 装饰器'
            incoming = IncomingMessage('test','群友','@阿洛娜 '+question,chat_type='group')
            row = memory.add_user('test', question, sender='群友')
            service._generate_and_send(incoming, config.data['targets'][0], row, question, force_reply=True)
            replies = []
            while True:
                try:
                    kind, data = service.events.get_nowait()
                    if kind == 'reply': replies.append(data)
                except queue.Empty:
                    break
            limit = min(config.bot['max_reply_chars'], config.bot['max_sentence_chars'])
            assert sink.sent and all(0 < len(s) <= limit for s in sink.sent)
            assert any('函数' in s or '装饰' in s for s in sink.sent), 'a factual answer, not a fallback, is required'
            assert replies and replies[-1]['web_search']['searched'] and replies[-1]['web_search']['sources']
            assert service.training.search('Python 装饰器')
            print('LIVE_MODEL_ANSWER',' '.join(sink.sent),flush=True)
            print('WEB_TO_REPLY_AND_KNOWLEDGE_VERIFIED',True,flush=True)
            print('NO_WECHAT_MESSAGES_SENT',True,flush=True)
        finally:
            service.shutdown()


if __name__ == '__main__':
    main()
