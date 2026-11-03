"""503 backoff is shared by background tasks and never locks out replies."""
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import AgentRunner
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult, ModelError
from app.service import BotService
from app.training import TrainingStore


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    config = ConfigStore(base/'config.json')
    config.add_target('test', 'group', 'arona')
    memory = MemoryStore(base/'memory.db')
    training = TrainingStore(base/'training.db', base/'files')
    service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'),
                         memory, training, admin_state_path=base/'admin.json')
    profile = dict(config.active_profile())
    try:
        memory.set_summary('test', '已有摘要保留', 1)
        error = ModelError('模型服务返回错误（HTTP 503）。', 'private-error-detail')
        with patch('app.service.make_client') as factory:
            factory.return_value.chat.side_effect = error
            assert service._background_chat(profile, [], '') is None
            assert service._background_chat(profile, [], '') is None
            assert service._proactive_decision('test', config.target_by_name('test'), {}) == 'ERROR'
            service._summarize_due_contexts()
            assert factory.return_value.chat.call_count == 1
            assert memory.get_summary('test')['summary'] == '已有摘要保留'
            first = service._background_health['retry_after']
            assert service._background_health['failures'] == 1
            assert 50 <= first-time.monotonic() <= 60
            with patch('app.service.time.monotonic', return_value=first+1):
                service._background_chat(profile, [], '')
                assert service._background_health['failures'] == 2
                assert service._background_health['retry_after'] == first+121
        # A successful conversation immediately permits background recovery.
        service._record_usage(LLMResult('正常', 'fixture', 'mock', 1), 'test', 'reply-agent')
        assert service._background_ready(profile) and service._background_health['failures'] == 0
        with patch('app.service.make_client') as factory:
            service._reply_busy = True
            assert service._background_chat(profile, [], '') is None
            factory.assert_not_called()
            service._reply_busy = False
        # One background operation at a time, with independent foreground slots.
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set(); release.wait(3)
            return LLMResult('WAIT', 'fixture', 'mock', 1)
        with patch('app.service.make_client') as factory:
            factory.return_value.chat.side_effect = slow
            worker = threading.Thread(target=service._background_chat,args=(profile,[],''))
            worker.start()
            try:
                assert entered.wait(2)
                assert service._background_chat(profile, [], '') is None
                reply = AgentRunner().run(lambda *args: LLMResult('正常回复。','fixture','mock',1),[], '', [])
                assert reply.result.text == '正常回复。'
                assert factory.return_value.chat.call_count == 1
            finally:
                release.set(); worker.join(3)
        service._background_health['retry_after'] = time.monotonic()+600
        assert service._background_ready({**profile, 'api_key_enc':'different-key'})
    finally:
        service.stop();memory.close();training.close()
print('BACKGROUND MODEL TESTS PASSED')
