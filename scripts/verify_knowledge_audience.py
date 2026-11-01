"""Live model acceptance with synthetic messages; never connects to WeChat."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import make_client
from app.prompting import build_system_prompt, build_compact_system_prompt
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage


def main() -> int:
    config = ConfigStore()
    profile = {**config.active_profile(), 'timeout': 40, 'max_tokens': 200, 'temperature': .2}
    persona = PersonaStore().get('arona')
    client = make_client(profile)
    for builder in (build_system_prompt, build_compact_system_prompt):
        system = builder(persona, [], target_name='test', target_type='group',
                         recipient_name='验证', max_chars=23, max_sentence_chars=23)
        for question, fact in [('水的化学式是什么？', 'H2O'), ('光年是时间单位还是长度单位？', '长度')]:
            answer = client.chat([{'role': 'user', 'content': question}], system).text
            normal = answer.replace('₂', '2').upper()
            assert fact in normal, f'Knowledge check failed: {question}: {answer}'
            print('KNOWLEDGE_OK', builder.__name__, question, answer)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        try:
            cases = [('大家早上好', True), ('谁知道光年是什么单位', True),
                     ('阿洛娜好可爱', True), ('张三，你明天几点下班', False),
                     ('@张三 你觉得阿洛娜可爱吗', False)]
            for text, expected in cases:
                actual = service._group_audience_allowed(IncomingMessage('test', '验证', text, chat_type='group'))
                assert actual == expected, f'Audience check failed: {text}: {actual}'
                print('AUDIENCE_OK', text, actual)
            memory.add_user('test', '阿洛娜，陪我聊聊吧', sender='验证')
            memory.add_assistant('test', '验证老师，我在听呢。')
            followup = service._group_audience(IncomingMessage('test', '验证', '那你再说说你的看法？', chat_type='group'))
            assert followup == 'BOT', 'must recognise an unmentioned follow-up to the bot'
            print('UNMENTIONED_FOLLOWUP_OK', followup)
            memory.add_user('test', '张三，你吃饭了吗？', sender='李四')
            actual = service._group_audience_allowed(IncomingMessage('test', '张三', '刚吃完，你呢？', chat_type='group'))
            assert not actual, 'must not join a conversation between two other members'
            print('CONTEXT_AUDIENCE_OK', actual)
        finally:
            service.shutdown()
    print('LIVE KNOWLEDGE AND AUDIENCE CHECKS PASSED; NO WECHAT MESSAGES SENT')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
