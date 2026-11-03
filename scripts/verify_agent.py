"""Real model/tool check; optional real sends are hard-restricted to test."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import faulthandler
import logging

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import configure_local_temp
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import make_client
from app.prompting import build_system_prompt, build_context_messages
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage, WeChatAutoAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send-test', action='store_true')
    args = parser.parse_args()
    configure_local_temp()
    trace = (Path(__file__).resolve().parents[1]/'data/temp/agent-check-threads.txt').open('w', encoding='utf-8')
    faulthandler.dump_traceback_later(30, repeat=True, file=trace)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    print('AGENT_CHECK_INITIALIZING', flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        config.data = copy.deepcopy(ConfigStore().data)
        config.bot.update(agent_enabled=True, agent_max_steps=4, agent_timeout_seconds=90,
                          web_search_enabled=True, read_links=True, voice_enabled=False,
                          auto_sticker=False, quote_reply=False)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(), StickerStore(), memory, training,
                             admin_state_path=base/'admin.json')
        adapter = None
        try:
            print('AGENT_CHECK_CALLING_MODEL', flush=True)
            persona = service.personas.get('arona')
            system = build_system_prompt(persona, [], target_name='test', recipient_name='调试',
                                         target_type='group', max_chars=25, max_sentence_chars=25,
                                         stickers_enabled=False)
            initial = '我想了解一个编程概念。先问我想了解哪个概念，等我回答再讲。'
            memory.add_user('test', initial, sender='调试')
            first, _, _, first_info = service._model_reply(
                make_client(config.active_profile()), build_context_messages(memory.recent('test')),
                system, config.active_profile(), IncomingMessage('test','调试',initial,chat_type='group'),
                initial, persona=persona, require_running=False)
            print('FIRST_TURN_STATE', first_info['agent']['status'], 'REPLY', first.text, flush=True)
            assert first_info['agent']['status'] == 'awaiting_user'
            memory.add_assistant('test', first.text, meta={'web_search':first_info})
            print('FIRST_TURN_ASKED', first.text, flush=True)
            question = ('Python 装饰器。先读取 https://peps.python.org/pep-0318/ ，再从本地知识库检索 decorators，'
                        '根据这两步的结果，用两句简短中文说明它的作用。')
            memory.add_user('test',question,sender='调试')
            incoming = IncomingMessage('test', '调试', question, chat_type='group')
            answer, _, _, info = service._model_reply(
                make_client(config.active_profile()), build_context_messages(memory.recent('test')),
                system, config.active_profile(), incoming, question, persona=persona, require_running=False)
            agent = info.get('agent', {})
            print('AGENT_RESULT', json.dumps(agent, ensure_ascii=False), flush=True)
            print('PAGES_READ', len(info['sources']), 'LEARNED', info['learned'], flush=True)
            print('FINAL_REPLY', answer.text, flush=True)
            assert agent.get('status') == 'completed' and info['sources'] and info['learned']
            assert agent['conversation_id'] == first_info['agent']['conversation_id'] and agent['turn'] == 2
            successful = {step['tool'] for step in agent['steps'] if step['status'] == 'ok'}
            assert {'read_webpage', 'search_knowledge'} <= successful
            bubbles = service._split_reply_messages(answer.text, [], False,
                int(config.bot.get('max_reply_chars',25)), int(config.bot.get('max_sentence_chars',25)))
            assert bubbles and all('"action"' not in bubble for bubble in bubbles)
            print('REAL_MULTI_TURN_SESSION_CONFIRMED', True, flush=True)
            if args.send_test:
                adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
                adapter.start([{'name':'test','type':'group','enabled':True}])
                assert adapter.ensure_chat_open('test') and adapter._uia.current_chat() == 'test'
                username = next(iter(adapter.target_usernames()))
                before = adapter._last_seq(username)
                for bubble in bubbles:
                    assert adapter.send_text('test', service._apply_reply_style(bubble)).ok
                for _ in range(12):
                    rows = adapter._db.get_messages(username, limit=20)
                    sent = {str(row.get('content') or '').strip() for row in rows
                            if int(row.get('sort_seq') or 0) > before
                            and row.get('sender_username') == adapter.self_wxid}
                    if all(service._apply_reply_style(bubble) in sent for bubble in bubbles):
                        print('TEST_ONLY_AGENT_REPLY_DB_CONFIRMED', True, flush=True)
                        break
                    time.sleep(.5)
                else:
                    raise AssertionError('test reply not confirmed in WeChat database')
            print('REAL_AGENT_CHECK_PASSED', flush=True)
        finally:
            faulthandler.cancel_dump_traceback_later()
            trace.close()
            if adapter:
                adapter.stop()
            service.stop(); memory.close(); training.close()


if __name__ == '__main__':
    main()
