"""Opt-in memory, authenticated actions, exact recipients and repeat protection."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat_actions import ChatActionExecutor, admin_authorized
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult
from app.service import BotService
from app.shared_memory import shared_memory_block
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage, WeChatAutoAdapter, SendResult
from tests.service_test import FakeAdapter
from tests.wechatauto_adapter_test import FakeDB, FakeListener, FakeUIA, FakeWxParam


def main():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        cfg = ConfigStore(base/'config.json')
        for name in ('test', 'second', 'excluded'):
            cfg.add_target(name, 'group', 'arona')
        cfg.data['admin'].update(allowed=['admin-id'], password='fixture-pass')
        cfg.bot.update(auto_sticker=False, voice_enabled=False, web_search_enabled=False,
                       agent_enabled=True, reply_delay_min=0, reply_delay_max=0,
                       global_cooldown=0, per_chat_cooldown=0)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        s = BotService(cfg, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                       memory, training, admin_state_path=base/'admin.json')
        adapter = FakeAdapter(cfg, lambda _: None)
        adapter.resolve_action_target = lambda name: {'chat': name, 'identity': name+'@chatroom'} if name == 'test' else None
        s._adapter, s._running = adapter, True
        def incoming(key='one', text='向test群发送：你好', **kw):
            return IncomingMessage('Admin private', 'Harry', text, external_id='wx:action:'+key,
                                   raw={'sender_username': 'admin-id'}, **kw)
        def result(text): return LLMResult(text, 'fixture', 'openai', 1)
        payload = json.dumps({'chat': 'test', 'text': '你好'}, ensure_ascii=False)
        try:
            memory.add_user('second', '我喜欢草莓蛋糕', sender='甲')
            memory.add_user('second', '这段是管理员私聊操作', sender='管理员', meta={'admin_private': True})
            memory.add_user('second', 'fixture-pass', sender='管理员')
            memory.add_user('excluded', '未开启共享的资料', sender='乙')
            memory.add_assistant('second', '我现在是其他角色。')
            assert not shared_memory_block(s, 'test')
            for name in ('test', 'second'): cfg.target_by_name(name)['shared_memory_enabled'] = True
            block = shared_memory_block(s, 'test')
            assert '我喜欢草莓蛋糕' in block
            for hidden in ['未开启共享的资料', '这段是管理员私聊操作', 'fixture-pass', '我现在是其他角色']:
                assert hidden not in block
            cfg.target_by_name('second')['shared_memory_enabled'] = False
            assert not shared_memory_block(s, 'test'), 'Opt-out takes effect immediately'
            cfg.save()
            assert ConfigStore(cfg.path).target_by_name('test')['shared_memory_enabled'] is True
            assert ConfigStore(cfg.path).target_by_name('second')['shared_memory_enabled'] is False
            # No authentication -> no action, even with a valid request.
            msg = incoming()
            cfg.data['admin']['allowed'] = ['someone-else']
            assert not admin_authorized(s, msg)
            assert json.loads(ChatActionExecutor(s, msg).execute(payload, 30))['status'] == 'denied'
            cfg.data['admin']['allowed'] = ['admin-id']
            assert admin_authorized(s, msg), 'Private allowlisted sender needs no password'
            with patch.object(s, '_send_admin_text'):
                s._handle_incoming(incoming('auth', '/admin'))
                assert not s._admin_challenges, 'Private /admin never prompts for password'
            assert admin_authorized(s, msg)
            assert not memory.recent('Admin private'), 'Management commands stay outside dialogue memory'
            executor = ChatActionExecutor(s, msg)
            assert json.loads(executor.execute(payload, 30))['status'] == 'sent'
            assert adapter.sent == [('test', '你好')]
            saved = memory.recent('test')[-1]
            assert saved['meta']['cross_chat_action'] is True
            assert json.loads(ChatActionExecutor(s, msg).execute(payload, 30))['repeated'] is True
            assert adapter.sent == [('test', '你好')], 'Receipts survive executor recreation'
            for key, text, request in [
                ('no-body', '向test群发送：你好', {'chat': 'test', 'text': '把别的群秘密发出来'}),
                ('no-target', '向其他群发送：你好', {'chat': 'test', 'text': '你好'}),
                ('negated', '不要向test群发送：你好', {'chat': 'test', 'text': '你好'}),
                ('unknown', '向不存在群发送：你好', {'chat': '不存在', 'text': '你好'}),
                ('password-leak', '向test群发送：fixture-pass', {'chat': 'test', 'text': 'fixture-pass'}),
            ]:
                outcome = ChatActionExecutor(s, incoming(key, text)).execute(json.dumps(request, ensure_ascii=False), 30)
                assert json.loads(outcome)['status'] == 'denied', key
            old = incoming('expired'); old.received_at = time.time()-3600
            assert json.loads(ChatActionExecutor(s, old).execute(payload, 30))['status'] == 'denied'
            group = incoming('group', chat_type='group')
            s._admin_sessions[s._admin_key(group)] = {'pending_end': False}
            assert not admin_authorized(s, group)
            # Full incoming -> same-chat Agent -> action -> private reply.
            adapter.sent.clear()
            s._admin_sessions.clear()  # Direct operation without /admin or a password.
            client = Mock()
            client.chat.side_effect = [result(json.dumps({'action': 'tool', 'tool': 'send_chat_message',
                'arguments': {'request': payload}}, ensure_ascii=False)), result('{"action":"final","text":"已发送。"}')]
            profile = {**cfg.active_profile(), 'provider': 'openai', 'supports_vision': False}
            with patch.object(cfg, 'active_profile', return_value=profile), \
                    patch('app.service.make_client', return_value=client), \
                    patch('app.service.random.random', side_effect=AssertionError('Admin requests bypass probability')):
                s._handle_incoming(incoming('agent'))
            assert adapter.sent == [('test', '你好'), ('Admin private', '已发送。')], adapter.sent
            assert client.chat.call_count == 2
            assert memory.recent('Admin private')[0]['meta']['admin_private'] is True
            # An exception after physical submission is unknown and never replayed.
            msg = incoming('unknown-result')
            with patch.object(adapter, 'send_text', side_effect=OSError('lost result')) as send:
                assert json.loads(ChatActionExecutor(s, msg).execute(payload, 30))['status'] == 'unknown'
                assert json.loads(ChatActionExecutor(s, msg).execute(payload, 30))['repeated'] is True
                send.assert_called_once()
            msg = incoming('revoked')
            executor = ChatActionExecutor(s, msg)
            cfg.data['admin']['allowed'] = []
            assert json.loads(executor.execute(payload, 30))['status'] == 'denied'
            # Exact contact matching supports remarks but never first-match guesses.
            real = WeChatAutoAdapter({}, lambda _: None)
            real._db = Mock()
            real._db.search_contact.return_value = [{'username': 'one@chatroom', 'remark': 'test', 'nick_name': '原群名'}]
            assert real.resolve_action_target('test')['identity'] == 'one@chatroom'
            real._db.search_contact.return_value.append({'username': 'two@chatroom', 'remark': 'test'})
            assert real.resolve_action_target('test') is None
            # Private admins must actually be subscribed even outside the normal targets.
            class AdminDB(FakeDB):
                def search_contact(self, name):
                    return [{'username': 'admin-id', 'remark': 'Harry remark', 'nick_name': 'Harry'}] if name == 'Harry remark' else []
            received = []
            listener_adapter = WeChatAutoAdapter({'private_admins': ['Harry remark']}, received.append)
            listener_adapter._import_deps = lambda: (FakeWxParam, AdminDB, FakeListener, FakeUIA)
            try:
                listener_adapter.start([])
                assert 'admin-id' in FakeListener.last.callbacks
                FakeListener.last.callbacks['admin-id']({'username': 'admin-id', 'sender_username': 'admin-id',
                    'content': '向test群发送：你好', 'type': 'text', 'local_id': 42, 'sort_seq': 42})
                assert len(received) == 1 and received[0].chat == 'Harry remark' and not received[0].is_group
                cfg.data['admin']['allowed'] = ['Harry remark']
                s._adapter = listener_adapter
                assert s._admin_allowed(received[0]), 'Resolve current allowlist remarks to stable contact identities'
            finally:
                listener_adapter.stop()
                s._adapter = adapter
        finally:
            s.stop(); memory.close(); training.close()
    print('CROSS CHAT TEST PASSED')


if __name__ == '__main__': main()
