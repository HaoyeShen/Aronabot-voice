"""Offline contracts for the optional local voice and its worker protocol."""
import io
import json
import queue
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.character_voice import CharacterTTS, CharacterVoiceClient, PaddedTTS, make_tts
from app.voice_presets import VoicePresetStore
from scripts.character_voice_worker import synthesize


def main():
    import numpy as np
    class Pipeline:
        def run(self, request):
            self.request = request
            yield 32000, (np.sin(np.arange(16000) * .1) * 30000).astype('<i2')
    pipeline = Pipeline()
    wav = synthesize(pipeline, '老师，我是普拉娜。', .92)
    assert pipeline.request['prompt_text'] == '老师，生物认证完毕。'
    assert pipeline.request['speed_factor'] == .92
    assert pipeline.request['text'] == '老师，我是普拉娜。'
    with wave.open(io.BytesIO(wav)) as handle:
        assert handle.getnchannels() == 1 and handle.getframerate() == 32000
        assert handle.getnframes() == 16000
    for text in ('', '老师，API 已启动。', '字' * 121):
        try:
            synthesize(pipeline, text)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid or unsupported input must retain text fallback')
    client = Mock()
    client.synthesize.return_value = wav
    tts = CharacterTTS(48000, client)
    clip = tts.synthesize('你好。', SimpleNamespace(rate='-8%', volume='+10%'))
    client.synthesize.assert_called_once_with('你好。', .92)
    assert clip.sample_rate == 48000 and abs(clip.duration - .5) < .01
    assert float(abs(clip.samples).max()) <= .90001
    client.synthesize.reset_mock()
    try:
        tts.synthesize('API 已启动。', SimpleNamespace(rate='+0%', volume='+0%'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('unsupported Latin text must not silently lose words')
    client.synthesize.assert_not_called()
    client.synthesize.side_effect = RuntimeError('offline')
    try:
        tts.synthesize('你好。', SimpleNamespace(rate='+0%', volume='+0%'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('model failure must propagate to existing text fallback')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = CharacterVoiceClient(root)
        with patch('app.character_voice.subprocess.Popen') as start:
            for settings in (None, {'validated': False}):
                if settings is not None:
                    (root / 'profile.json').write_text(json.dumps(settings), encoding='utf-8')
                try:
                    local.synthesize('你好。')
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('unconfigured character model must not launch')
            start.assert_not_called()
        local._responses = Mock()
        local._responses.get.side_effect = queue.Empty
        with patch.object(local, 'close') as close:
            try:
                local._receive(0)
            except RuntimeError:
                pass
            else:
                raise AssertionError('timeout must fail')
            close.assert_called_once()
        local._responses = queue.Queue()
        local._read_responses(SimpleNamespace(stdout=io.StringIO('[]\n')), local._responses)
        with patch.object(local, 'close') as close:
            try:
                local._receive(0)
            except RuntimeError:
                pass
            else:
                raise AssertionError('malformed protocol must terminate the worker')
            close.assert_called_once()
        store = VoicePresetStore(root / 'voices.json')
        preset = store.get('plana')
        preset.update(engine='gpt_sovits', label='普拉娜（国服中文）', rate='+0%', pitch='+0Hz')
        store.upsert(preset)
        assert VoicePresetStore(root / 'voices.json').get('plana')['engine'] == 'gpt_sovits'
        assert isinstance(make_tts(preset).backend, CharacterTTS)
        assert not isinstance(make_tts(store.get('arona')).backend, CharacterTTS)
        from wecvoice_auto.tts import AudioClip
        samples = np.array([.1, .2, -.3], dtype=np.float32)
        backend = Mock()
        backend.synthesize.return_value = AudioClip(samples, 48000, '你好')
        padded = PaddedTTS(backend).synthesize('你好', None)
        assert np.array_equal(padded.samples[:len(samples)], samples)
        assert len(padded.samples) == len(samples) + 28800
        assert not padded.samples[len(samples):].any()
    print('CHARACTER VOICE TESTS PASSED')


if __name__ == '__main__':
    main()
