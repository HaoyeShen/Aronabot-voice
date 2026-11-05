"""Probability precedes every semantic call; misses still build the dialogue."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage
from tests.service_test import FakeAdapter


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        assert config.bot['probability_first_reply'] is True
        assert config.bot['group_reply_probability'] == .03
        group = config.add_target('test', 'group', 'arona')
        friend = config.add_target('friend', 'friend', 'arona')
        assert config.target_by_name('test')['group_reply_probability'] == .03
        assert friend['reply_probability'] == .03
        config.bot.update(per_chat_cooldown=0, global_cooldown=0, quiet_hours_enabled=False)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                             memory, training, admin_state_path=base/'admin.json')
        adapter = FakeAdapter(config, lambda _: None)
        adapter.self_name = 'ARONA'
        service._adapter, service._running = adapter, True
        def turn(key, text='阿洛娜，在吗', **kw):
            return IncomingMessage('test', '群友', text, chat_type='group', external_id='wx:prob:'+key, **kw)
        try:
            # Ordinary keywords/public discussion draw before any semantic call.
            with patch('app.service.random.random', return_value=.03) as draw, \
                    patch.object(service, '_probability_reply_allowed') as decide, \
                    patch.object(service, '_keyword_addressed') as keyword, \
                    patch.object(service, '_group_audience') as audience, \
                    patch.object(service, '_generate_and_send') as send, \
                    patch.object(service, '_shared_content') as links:
                for i, text in enumerate(['大家好', '阿洛娜，在吗', '那刚才的问题呢']):
                    service._handle_incoming(turn('miss'+str(i), text))
                assert draw.call_count == 3
                for fn in [decide, keyword, audience, send, links]: fn.assert_not_called()
                assert len(memory.recent('test')) == 3
                assert service.state()['processed'] == 3
                # The same stable DB event cannot be redrawn.
                service._handle_incoming(turn('miss0', '大家好'))
                assert draw.call_count == 3
            # User-authorized exceptions: @ and confirmed follow-up reply
            # directly, even at 0%, with probability replies disabled/in cooldown.
            config.bot['group_probability_reply'] = False
            service._last_reply_at['test'] = time.time()
            config.bot['per_chat_cooldown'] = 60
            with patch('app.service.random.random') as draw, \
                    patch.object(service, '_probability_reply_allowed') as decide, \
                    patch.object(service, '_generate_and_send') as send:
                service._handle_incoming(turn('mention', '@ARONA 你好'))
                assert send.call_args.kwargs['force_reply'] is True
                assert send.call_args.kwargs['start_followup'] is True
                service._followups['test'] = {'last_activity': time.monotonic(), 'misses': 0, 'seen': []}
                with patch.object(service, '_group_audience', return_value='BOT'):
                    service._handle_incoming(turn('follow', '那接下来呢'))
                assert send.call_count == 2 and send.call_args.kwargs['force_reply'] is True
                draw.assert_not_called(); decide.assert_not_called()
            service._followups.clear(); service._last_reply_at.clear()
            config.bot.update(group_probability_reply=True, per_chat_cooldown=0)
            sequence = []
            with patch('app.service.random.random', side_effect=lambda: sequence.append('draw') or .029), \
                    patch.object(service, '_probability_reply_allowed', side_effect=lambda *a: sequence.append('model') or True), \
                    patch.object(service, '_generate_and_send', side_effect=lambda *a, **kw: sequence.append('send')) as send:
                service._handle_incoming(turn('accept', '这游戏的新剧情有意思'))
                assert sequence == ['draw', 'model', 'send'], sequence
                assert not send.call_args.kwargs['start_followup']
                assert not service._discussions, 'No hard participant/count threshold'
            with patch('app.service.random.random', return_value=0), \
                    patch.object(service, '_probability_reply_allowed', return_value=False) as decide, \
                    patch.object(service, '_generate_and_send') as send:
                service._handle_incoming(turn('decline', '大家看看这个'))
                decide.assert_called_once(); send.assert_not_called()
                assert memory.recent('test')[-1]['external_id'] == 'wx:prob:decline'
            # Private messages use one independent per-contact draw too.
            with patch('app.service.random.random', return_value=.029) as draw, \
                    patch.object(service, '_probability_reply_allowed', return_value=True) as decide, \
                    patch.object(service, '_generate_and_send') as send:
                service._handle_incoming(IncomingMessage('friend', '群友', '你好', external_id='wx:private'))
                draw.assert_called_once(); decide.assert_called_once(); send.assert_called_once()
            # Disabled / quiet / stale / outgoing turns make no model decision.
            with patch('app.service.random.random') as draw, patch.object(service, '_probability_reply_allowed') as decide:
                config.bot['auto_reply'] = False
                service._handle_incoming(turn('paused'))
                config.bot['auto_reply'] = True
                with patch.object(service, '_in_quiet_hours', return_value=True):
                    service._handle_incoming(turn('quiet'))
                service._handle_incoming(turn('stale', received_at=time.time()-3600))
                service._handle_incoming(turn('outgoing', direction='out'))
                draw.assert_not_called(); decide.assert_not_called()
            # Real decision adapter: explicit YES only, no local fallback on errors.
            profile = {**config.active_profile(), 'provider': 'openai'}
            persona = service.personas.get('arona')
            with patch.object(config, 'active_profile', return_value=profile), patch('app.service.make_client') as factory:
                for text, allowed in [('YES', True), ('NO', False), ('可能吧', False)]:
                    factory.return_value.chat.return_value = LLMResult(text, 'fixture', 'openai', 1)
                    assert service._probability_reply_allowed(turn('classify'), persona) == allowed
                factory.return_value.chat.side_effect = OSError('offline')
                assert not service._probability_reply_allowed(turn('error'), persona)
            # Missing fields migrate; invalid values never become 100%.
            raw = json.loads(config.path.read_text(encoding='utf-8'))
            raw['bot'].pop('probability_first_reply', None)
            raw['bot']['group_reply_probability'] = float('nan')
            raw['targets'][0]['group_reply_probability'] = float('nan')
            config.path.write_text(json.dumps(raw), encoding='utf-8')
            migrated = ConfigStore(config.path)
            assert migrated.bot['probability_first_reply'] is True
            assert migrated.bot['group_reply_probability'] == .03
            assert migrated.targets()[0]['group_reply_probability'] == .03
        finally:
            service.stop(); memory.close(); training.close()
    print('PROBABILITY REPLY TEST PASSED')


if __name__ == '__main__': main()
