"""Isolated, Chinese-only GPT-SoVITS worker; never imports into the GUI runtime.

The inference source and weights live in data/character_voice. The worker
accepts JSON lines and returns PCM WAVs; all model inference stays local.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import types
import wave

ROOT = Path(os.environ.get('ARONA_CHARACTER_VOICE_ROOT',
                          Path(__file__).resolve().parents[1] / 'data' / 'character_voice')).resolve()


def load_reference(character='plana'):
    if character not in ('arona', 'plana'):
        raise ValueError('unknown character profile')
    path = ROOT / 'profile.json' if character == 'plana' else ROOT / 'profiles' / f'{character}.json'
    reference = json.loads(path.read_text(encoding='utf-8'))
    if not reference.get('transcript') or reference.get('character') != character:
        raise ValueError('invalid character reference profile')
    defaults = {
        'reference_audio': f'{character}-reference.wav',
        't2s_weights': 'models/plana/plana-e15.ckpt' if character == 'plana' else '',
        'vits_weights': 'models/plana/plana_e8_s232.pth' if character == 'plana' else '',
    }
    for field, default in defaults.items():
        value = reference.get(field, default)
        if not value:
            raise ValueError('missing character asset')
        asset = (ROOT / value).resolve()
        if not asset.is_relative_to(ROOT) or not asset.is_file():
            raise ValueError('invalid character asset')
        reference[field] = str(asset)
    if hashlib.sha256(Path(reference['reference_audio']).read_bytes()).hexdigest() != reference['reference_sha256']:
        raise ValueError('reference audio changed; revalidate the character profile')
    return reference


def load_pipeline(character='plana'):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', version='v2', MPLBACKEND='Agg')
    cache = ROOT / 'cache'
    for name, folder in {'HF_HOME': 'huggingface', 'TRANSFORMERS_CACHE': 'transformers',
                         'NUMBA_CACHE_DIR': 'numba', 'TORCH_HOME': 'torch',
                         'MPLCONFIGDIR': 'matplotlib'}.items():
        target = cache / folder
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    source = ROOT / 'GPT-SoVITS'
    reference = load_reference(character)
    os.chdir(source)
    sys.path[:0] = [str(source), str(source / 'GPT_SoVITS')]
    import numpy as np
    import soundfile as sf
    import librosa
    import torch
    import jieba
    import jieba.posseg

    torch.set_num_threads(min(4, os.cpu_count() or 2))
    torch.set_num_interop_threads(1)
    # The Chinese phonemizer also works with the pure-Python jieba API.
    sys.modules['jieba_fast'] = jieba
    sys.modules['jieba_fast.posseg'] = jieba.posseg
    # This service is explicitly all_zh: no unrelated language detector/model
    # downloads, Japanese/Korean toolchains or WebUI dependencies are needed.
    segmenter = types.ModuleType('text.LangSegmenter')
    class ChineseSegmenter:
        @staticmethod
        def getTexts(text):
            return [{'lang': 'zh', 'text': text}]
    segmenter.LangSegmenter = ChineseSegmenter
    sys.modules['text.LangSegmenter'] = segmenter
    # Use the upstream pypinyin Chinese phonemizer on this CPU-only host.
    from text import chinese
    sys.modules['text.chinese2'] = chinese
    helper = types.ModuleType('tools.my_utils')
    def load_audio(path, sr):
        data, rate = sf.read(path, dtype='float32', always_2d=True)
        data = data.mean(axis=1)
        return librosa.resample(data, orig_sr=rate, target_sr=sr) if rate != sr else data
    helper.load_audio = load_audio
    sys.modules['tools.my_utils'] = helper
    import utils
    torch.serialization.add_safe_globals([utils.HParams])
    # Character checkpoints must contain only tensors and simple metadata.
    original_load = torch.load
    def load_weights(*args, **kwargs):
        kwargs['weights_only'] = True
        return original_load(*args, **kwargs)
    torch.load = load_weights
    from TTS_infer_pack.TTS import TTS, TTS_Config
    settings = {
        'device': 'cpu', 'is_half': False, 'version': 'v2',
        't2s_weights_path': reference['t2s_weights'],
        'vits_weights_path': reference['vits_weights'],
        'bert_base_path': str(ROOT / 'models/base/chinese-roberta-wwm-ext-large'),
        'cnhuhbert_base_path': str(ROOT / 'models/base/chinese-hubert-base'),
    }
    config = TTS_Config({'version': 'v2', 'custom': settings})
    pipeline = TTS(config)
    # The BERT language frontend is the largest resident model. Dynamic int8
    # linear layers reduce RAM pressure on this 8GB CPU host; acoustic weights
    # and the reference voice remain in full precision.
    pipeline.bert_model = torch.ao.quantization.quantize_dynamic(
        pipeline.bert_model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
    pipeline.text_preprocessor.bert_model = pipeline.bert_model
    pipeline._arona_reference_text = reference['transcript']
    pipeline._arona_reference_audio = reference['reference_audio']
    return pipeline


def synthesize(pipeline, text, speed=1.0):
    import numpy as np
    if not isinstance(text, str) or not text.strip() or len(text) > 120:
        raise ValueError('invalid speech length')
    # all_zh would silently drop Latin words. Fail explicitly so the caller
    # retains the full text through its existing text fallback.
    import re
    if re.search('[A-Za-z]', text):
        raise ValueError('Chinese character voice does not support Latin words')
    if not isinstance(speed, (int, float)) or not .5 <= speed <= 1.5:
        raise ValueError('invalid speech speed')
    request = {
        'text': text, 'text_lang': 'all_zh',
        'ref_audio_path': getattr(pipeline, '_arona_reference_audio', str(ROOT / 'plana-reference.wav')),
        'prompt_text': getattr(pipeline, '_arona_reference_text', '老师，生物认证完毕。'),
        'prompt_lang': 'all_zh',
        'text_split_method': 'cut0', 'batch_size': 1, 'speed_factor': speed,
        'top_k': 15, 'top_p': 1, 'temperature': 1, 'seed': 42,
        'parallel_infer': False, 'return_fragment': False,
    }
    segments = []
    sample_rate = None
    for rate, data in pipeline.run(request):
        if sample_rate is not None and rate != sample_rate:
            raise ValueError('inconsistent output sample rate')
        sample_rate = rate
        segments.append(np.asarray(data, dtype='<i2').reshape(-1))
    if not segments or not sample_rate:
        raise ValueError('no synthesized audio')
    samples = np.concatenate(segments)
    if not .15 <= len(samples) / sample_rate <= 45 or not np.any(samples):
        raise ValueError('invalid synthesized audio')
    output = io.BytesIO()
    with wave.open(output, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--character', choices=('arona', 'plana'), default='plana')
    parser.add_argument('--text', default='老师，我是普拉娜，请多关照。')
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    with contextlib.redirect_stdout(sys.stderr):
        pipeline = load_pipeline(args.character)
    if output:
        with contextlib.redirect_stdout(sys.stderr):
            audio = synthesize(pipeline, args.text)
        output.write_bytes(audio)
        return
    print(json.dumps({'ready': True}), flush=True)
    with open(os.devnull, 'w') as sink:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                with contextlib.redirect_stdout(sink):
                    audio = synthesize(pipeline, request.get('text'), request.get('speed', 1.0))
                result = {'wav': base64.b64encode(audio).decode('ascii')}
            except Exception as exc:
                result = {'error': type(exc).__name__}
            print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
