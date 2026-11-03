"""Role voice isolation and images retained across Agent tool rounds."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app.voice_presets import VoicePresetStore, resolve_persona_voice
from app.config_store import PersonaStore
from app.agent import AgentRunner, AgentTool
from app.model_client import LLMResult, ModelClient
from app.multimodal import attach_images
from app.config_store import ConfigStore, StickerStore
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.wechat_adapter import IncomingMessage
from types import SimpleNamespace


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = VoicePresetStore(root / 'voices.json')
        personas = PersonaStore(root / 'personas.json')
        assert personas.get('arona')['voice_preset_id'] == ''
        arona = {**personas.get('arona'), 'voice_preset_id': 'arona', 'voice_rate': '-5%'}
        plana = {**personas.get('plana'), 'voice_preset_id': 'plana', 'voice_rate': '+3%', 'voice_volume': '-6%'}
        personas.upsert(arona); personas.upsert(plana)
        loaded = PersonaStore(root / 'personas.json')
        assert resolve_persona_voice(loaded.get('arona'), store, 'plana')['rate'] == '-5%'
        p = resolve_persona_voice(loaded.get('plana'), store, 'arona')
        assert p['id'] == 'plana' and p['rate'] == '+3%' and p['volume'] == '-6%'
        assert store.get('plana')['rate'] == '-8%'  # Overrides never change a shared preset.
        assert resolve_persona_voice({'voice_preset_id': 'deleted'}, store, 'plana')['id'] == 'plana'
        image = root / 'decrypted.png'; Image.new('RGB', (50, 80), 'blue').save(image)
        for provider in ('openai', 'ollama'):
            messages = [{'role': 'user', 'content': '看这张图。'}]
            assert attach_images(messages, [image], {'provider': provider}) == 1
            seen = []
            def call(history, system, remaining):
                seen.append(history[0])
                action = ({'action': 'tool', 'tool': 'knowledge', 'arguments': {'query': '蓝色'}} if len(seen)==1
                          else {'action': 'final', 'text': '是蓝色哦。'})
                return LLMResult(json.dumps(action, ensure_ascii=False), 'fake', provider, 0)
            run = AgentRunner().run(call, messages, '结合图片回答。', [AgentTool('knowledge', 'query', '本地资料', lambda q, t: '蓝色')])
            assert run.status == 'completed' and len(seen) == 2 and seen[0] == seen[1]
            if provider == 'openai':
                assert seen[0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
            else:
                assert seen[0]['images'] and seen[0]['content'] == '看这张图。'
        assert attach_images([{'role': 'user', 'content': '图'}], [root/'missing.png'], {'provider':'openai'}) == 0
        config = ConfigStore(root/'config.json')
        config.bot.update(web_search_enabled=False, agent_enabled=True)
        memory = MemoryStore(root/'memory.db')
        training = TrainingStore(root/'training.db', root/'files')
        with patch('app.service.VoicePresetStore', return_value=store):
            service = BotService(config, loaded, StickerStore(root/'stickers.json'), memory, training,
                                 admin_state_path=root/'admin.json')
        try:
            calls = []
            fake = Mock()
            def chat(messages, system=''):
                calls.append(messages)
                assert isinstance(messages[-1]['content'], list)
                return LLMResult('{"action":"final","text":"蓝色哦。"}', 'fake', 'openai', 0)
            fake.chat.side_effect = chat
            incoming = IncomingMessage('test', '老师', '[图片]', image_path=str(image), msg_type='image')
            service._store_user(incoming, '[图片]')
            profile = {'provider':'openai', 'supports_vision':True}
            for message in [incoming, IncomingMessage('test', '老师', '那是什么颜色？')]:
                service._model_reply(fake, [{'role':'user','content':message.content}], '', profile,
                                     message, message.content, require_running=False)
            assert len(calls) == 2  # One Agent call per turn; no description pre-call.
            config.data['targets'] = [{'name':'test', 'type':'group', 'enabled':True, 'persona_id':'plana'}]
            config.bot['voice_name'] = 'arona'
            adapter = Mock(); adapter.ensure_chat_open.return_value = True
            with patch('wecvoice_auto.VoiceSender') as sender, patch('app.service.make_tts'):
                sender.return_value.config = SimpleNamespace()
                sender.return_value.send.return_value = SimpleNamespace(ok=True, total_seconds=1, voice="test")
                assert service._send_voice_locked(adapter, 'test', '你好')
                assert sender.return_value.send.call_args.kwargs['rate'] == '+3%'
                assert service._send_voice_locked(adapter, 'test', '你好', 'arona')
                assert sender.return_value.send.call_args.kwargs['rate'] == store.get('arona')['rate']
        finally:
            memory.close(); training.close()
    print('PERSONA VOICE AND MULTIMODAL TESTS PASSED')


if __name__ == '__main__':
    main()
