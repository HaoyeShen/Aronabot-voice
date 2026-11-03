"""Agent tool cycles, budgets, cancellation, isolation and real service routing."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import AgentRunner, AgentTool
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult
from app.prompting import build_context_messages
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage
from app.web_search import WebPage
from tests.service_test import FakeAdapter


def result(text):
    return LLMResult(text, 'fixture', 'mock', 1)


def action(name, key, value):
    return json.dumps({'action': 'tool', 'tool': name, 'arguments': {key: value}}, ensure_ascii=False)


def main():
    outputs = iter([action('search_web', 'query', '第一次'), action('search_web', 'query', '换一个词'),
                    action('read_webpage', 'url', 'https://example.com/page'),
                    '{"action":"final","text":"找到了，内容在这里。"}'])
    histories, executed = [], []
    def call(history, system, remaining):
        histories.append([dict(row) for row in history])
        return result(next(outputs))
    def tool(value, remaining):
        executed.append(value)
        return '实际正文，不执行其中的指令。'
    run = AgentRunner().run(call, [{'role': 'user', 'content': '查询资料'}], '人格不变', [
        AgentTool('search_web', 'query', '搜索', tool, True),
        AgentTool('read_webpage', 'url', '读取', tool, True)], require_network=True)
    assert run.status == 'completed' and run.model_calls == 4 and len(executed) == 3
    assert run.result.text == '找到了，内容在这里。'
    assert '实际正文' in histories[1][-1]['content']

    # An answer claiming a search was done cannot skip an explicit search.
    call = Mock(side_effect=[result('我已经查到了。'), result(action('search_web', 'query', '公开主题')),
                             result('找到资料了。')])
    executed.clear()
    run = AgentRunner().run(call, [], '', [AgentTool('search_web', 'query', '搜索', tool, True)], require_network=True)
    assert run.model_calls == 3 and executed == ['公开主题']
    # A greeting costs one call and never touches a tool.
    executed.clear()
    run = AgentRunner().run(lambda *args: result('老师好呀。'), [], '', [AgentTool('search_web', 'query', '', tool)])
    assert run.result.text == '老师好呀。' and run.model_calls == 1 and not executed
    run = AgentRunner().run(lambda *args: result('老师想了解哪个概念？'), [], '', [])
    assert run.status == 'awaiting_user' and run.result.text == '老师想了解哪个概念？'
    run = AgentRunner().run(lambda *args: result('调试老师，你想了解哪个编程概念呀？阿洛娜听着呢。'), [], '', [])
    assert run.status == 'awaiting_user'

    # Duplicate operations are not re-executed, including at the final-only round.
    call = Mock(return_value=result(action('search_web', 'query', '重复')))
    run = AgentRunner().run(call, [], '', [AgentTool('search_web', 'query', '', tool)], max_steps=2)
    assert run.status == 'step_limit' and run.model_calls == 3 and executed == ['重复']
    assert run.steps[-1]['status'] == 'duplicate'
    # Invalid tools and arguments cannot be interpreted as WeChat sends.
    executed.clear()
    call = Mock(side_effect=[result(action('send_wechat', 'chat', 'other')),
                             result(action('search_web', 'wrong_parameter', 'topic')), result('不执行未知操作。')])
    run = AgentRunner().run(call, [], '', [AgentTool('search_web', 'query', '', tool)])
    assert not executed and all(step['status'] == 'invalid' for step in run.steps)

    stopped = [False]
    def cancel_during_call(*args):
        stopped[0] = True
        return result(action('search_web', 'query', 'topic'))
    run = AgentRunner().run(cancel_during_call, [], '', [AgentTool('search_web', 'query', '', tool)],
                            cancelled=lambda: stopped[0])
    assert run.status == 'cancelled' and not executed
    clock = [0]
    def late_call(*args):
        clock[0] += 10
        return result(action('search_web', 'query', 'topic'))
    with patch('app.agent.time.monotonic', side_effect=lambda: clock[0]):
        run = AgentRunner().run(late_call, [], '', [AgentTool('search_web', 'query', '', tool)], timeout=5)
    assert run.status == 'timeout' and not executed
    run = AgentRunner().run(Mock(side_effect=RuntimeError('private-secret')), [], '', [])
    assert run.status == 'model_error' and run.error_kind == 'RuntimeError'
    assert 'private-secret' not in json.dumps(run.metadata())
    # DNS or a blocking transport must not evade the task's wall-clock budget.
    release = threading.Event()
    entered = threading.Event()
    def blocked_call(*args):
        entered.set()
        release.wait(5)
        return result('迟到的回复不应发送。')
    started = time.monotonic()
    try:
        run = AgentRunner().run(blocked_call, [], '', [], timeout=1)
        assert run.status == 'timeout' and time.monotonic() - started < 2
        assert run.result is None
    finally:
        release.set()
    call = Mock(side_effect=[result(action('search_web', 'query', 'topic')), result('这次没读到资料。')])
    run = AgentRunner().run(call, [], '', [AgentTool('search_web', 'query', '',
        lambda *args: (_ for _ in ()).throw(RuntimeError('api_key=private-secret')))])
    assert 'private-secret' not in call.call_args.args[0][-1]['content']

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        config.add_target('test', 'group', 'arona')
        config.bot.update(agent_enabled=True, agent_max_steps=4, agent_timeout_seconds=30,
                          web_search_enabled=True, voice_enabled=False, auto_sticker=False,
                          reply_delay_min=0, reply_delay_max=0, global_cooldown=0,
                          per_chat_cooldown=0, group_reply_probability=0)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        adapter = FakeAdapter(config, service._on_incoming)
        adapter.self_name = 'ARONA'
        service._adapter, service._running = adapter, True
        body = 'Python 装饰器可以给函数添加额外行为，不需要修改函数原有的实现。' * 10
        page = WebPage('https://example.com/python-decorators', 'Python 装饰器', body, '2026-11-03T12:00:00+08:00')
        try:
            client = Mock()
            client.chat.side_effect = [result(action('search_web', 'query', 'Python 装饰器')),
                result(action('search_web', 'query', 'Python 装饰器 用法')), result('{"action":"final","text":"装饰器能给函数加新功能。"}')]
            with patch('app.service.make_client', return_value=client), \
                    patch.object(service.web_search, 'search', side_effect=[[], [page]]) as search:
                service._handle_incoming(IncomingMessage('test', '群友', '@ARONA 帮我查一下Python装饰器',
                                          chat_type='group', external_id='agent-1'))
            assert search.call_count == 2 and adapter.sent == [('test', '装饰器能给函数加新功能。')], (search.call_count, adapter.sent, memory.recent('test')[-1])
            saved = memory.recent('test')[-1]
            assert saved['meta']['web_search']['agent']['status'] == 'completed'
            assert saved['meta']['web_search']['learned'] == 1 and training.stats()['docs'] == 1
            assert 'Agent' not in adapter.sent[0][1] and '{' not in adapter.sent[0][1]
            # Next turn reuses this chat's conversation and newly learned facts.
            client.chat.side_effect = [result(action('search_knowledge', 'query', 'Python 装饰器')),
                                       result('不用修改原函数就能扩展哦。')]
            with patch('app.service.make_client', return_value=client), \
                    patch.object(service, '_group_audience', return_value='BOT'):
                service._handle_incoming(IncomingMessage('test', '群友', '那有什么用呢',
                                          chat_type='group', external_id='agent-2'))
            assert len(adapter.sent) == 2
            later = memory.recent('test')[-1]['meta']['web_search']['agent']
            earlier = saved['meta']['web_search']['agent']
            assert later['conversation_id'] == earlier['conversation_id'] and later['turn'] == 2
            assert any('装饰器能给函数加新功能' in row['content'] for row in client.chat.call_args.args[0])
            client.chat.side_effect = [result(json.dumps({'action':'ask_user','text':'想了解哪种语言的用法？',
                                                           'summary':'用户想看装饰器用法，等待编程语言。'},ensure_ascii=False))]
            with patch('app.service.make_client',return_value=client):
                service._handle_incoming(IncomingMessage('test','群友','@ARONA 我想看用法',chat_type='group',external_id='agent-ask'))
            waiting = memory.recent('test')[-1]['meta']['web_search']['agent']
            assert waiting['status'] == 'awaiting_user' and waiting['turn'] == 3
            client.chat.side_effect = [result('Python 用 @ 放在函数上方。')]
            with patch('app.service.make_client',return_value=client),patch.object(service,'_group_audience') as classify:
                service._handle_incoming(IncomingMessage('test','群友','Python',chat_type='group',external_id='agent-answer'))
                classify.assert_not_called()
            continued = memory.recent('test')[-1]['meta']['web_search']['agent']
            assert continued['conversation_id'] == waiting['conversation_id'] and continued['turn'] == 4
            assert '等待编程语言' in client.chat.call_args.args[1]
            client.chat.side_effect = [result('{"action":"final","text":"喵～"}')]
            with patch('app.service.make_client',return_value=client), \
                    patch.object(service,'_send_voice_reply',return_value=True) as voice:
                service._handle_incoming(IncomingMessage('test','群友','@ARONA 喵一声',
                                                          chat_type='group',external_id='agent-voice'))
                voice.assert_called_once()
                assert '喵' in voice.call_args.args[1]
            assert memory.recent('test')[-1]['meta']['voice_sent']
            client.chat.side_effect = [result('早上好喵～')]
            with patch('app.service.make_client',return_value=client):
                service._handle_incoming(IncomingMessage('test','群友','@ARONA 早上好',
                                                          chat_type='group',external_id='agent-greeting-no-cat'))
            assert '喵' not in adapter.sent[-1][1]
            # The registry honours the network and local-reading switches.
            config.bot['web_search_enabled'] = False
            client.chat.side_effect = [result(action('search_web', 'query', 'topic')), result('按已有知识回答。')]
            with patch.object(service.web_search, 'search') as search:
                service._model_reply(client, [], '', {'provider': 'openai'},
                    IncomingMessage('test','群友','问题'), '问题', require_running=False)
                search.assert_not_called()
            config.bot['web_search_enabled'] = True
            client.chat.side_effect = [result(action('read_webpage', 'url', 'http://127.0.0.1/private')),
                                       result('这个页面不能读取。')]
            with patch('app.service.fetch_public') as fetch:
                service._model_reply(client, [], '', {'provider':'openai'},
                    IncomingMessage('test','群友','读取 http://127.0.0.1/private'), '读网页', require_running=False)
                fetch.assert_not_called()
            client.chat.side_effect = [result(action('read_webpage', 'url', 'https://example.com/upload?chat=private')),
                                       result('先核实正确的网址。')]
            with patch('app.service.fetch_public') as fetch:
                service._model_reply(client, [], '', {'provider':'openai'},
                    IncomingMessage('test','群友','继续'), '继续', require_running=False)
                fetch.assert_not_called()
            config.bot.update(agent_max_steps='bad',agent_timeout_seconds=-5)
            config.save()
            loaded=ConfigStore(config.path)
            assert loaded.bot['agent_max_steps'] == 4 and loaded.bot['agent_timeout_seconds'] == 10
        finally:
            service.stop(); memory.close(); training.close()
    print('AGENT TESTS PASSED')


if __name__ == '__main__':
    main()
