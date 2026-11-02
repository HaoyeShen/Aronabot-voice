"""Optional local character TTS, isolated from Tkinter and the Edge runtime."""
from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import queue
import re
import subprocess
import threading

from .paths import BUNDLE, DATA

log = logging.getLogger(__name__)
VOICE_ROOT = DATA / 'character_voice'


class CharacterVoiceClient:
    def __init__(self, root=VOICE_ROOT):
        self.root = root
        self._lock = threading.Lock()
        self._process = None
        self._responses = None
        self._stderr = None

    @staticmethod
    def _read_responses(process, responses):
        try:
            while line := process.stdout.readline(6 * 1024 * 1024):
                try:
                    response = json.loads(line)
                    if not isinstance(response, dict):
                        raise ValueError('invalid protocol object')
                    responses.put(response)
                except (ValueError, UnicodeError):
                    responses.put({'error': 'invalid worker response', 'protocol_error': True})
                    return
        except (OSError, ValueError):
            pass
        finally:
            responses.put(None)

    def _receive(self, timeout):
        try:
            response = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError('本机角色语音合成超时，已停止推理进程') from exc
        if response is None:
            self.close()
            raise RuntimeError('本机角色语音进程已退出，请查看 character_voice/worker.log')
        if response.get('error'):
            if response.get('protocol_error'):
                self.close()
            raise RuntimeError('本机角色语音合成失败：' + str(response['error']))
        return response

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        python = self.root / 'runtime/Scripts/python.exe'
        worker = BUNDLE / 'scripts/character_voice_worker.py'
        try:
            profile = json.loads((self.root / 'profile.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RuntimeError('本机角色音色尚未配置') from exc
        if not profile.get('validated') or not python.is_file() or not worker.is_file():
            raise RuntimeError('本机角色音色尚未通过合成验证')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                   PYTHONNOUSERSITE='1', ARONA_CHARACTER_VOICE_ROOT=str(self.root),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        self._stderr = (self.root / 'worker.log').open('a', encoding='utf-8')
        try:
            process = subprocess.Popen(
                [str(python), '-u', str(worker)], cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
                text=True, encoding='utf-8', env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self._process = process
            self._responses = queue.Queue()
            threading.Thread(target=self._read_responses,
                             args=(process, self._responses), daemon=True).start()
            if not self._receive(180).get('ready'):
                raise RuntimeError('本机角色语音初始化失败')
        except Exception:
            self.close()
            raise

    def synthesize(self, text, speed=1.0):
        with self._lock:
            self._start()
            try:
                self._process.stdin.write(json.dumps({'text': text, 'speed': speed},
                                                     ensure_ascii=False) + '\n')
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.close()
                raise RuntimeError('本机角色语音进程不可用') from exc
            response = self._receive(180)
            try:
                return base64.b64decode(response['wav'], validate=True)
            except (KeyError, ValueError) as exc:
                self.close()
                raise RuntimeError('本机角色语音返回了无效音频') from exc

    def close(self):
        # No request lock here: stopping must also interrupt a slow inference.
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                log.warning('关闭本机角色语音进程失败')
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None


_client = CharacterVoiceClient()
atexit.register(_client.close)


class CharacterTTS:
    def __init__(self, sample_rate=48000, client=None):
        self.sample_rate = sample_rate
        self.client = client or _client

    def synthesize(self, text, profile):
        if re.search('[A-Za-z]', text):
            raise RuntimeError('本机角色音色当前仅支持中文，本条回退文字')
        import miniaudio
        import numpy as np
        from wecvoice_auto.tts import AudioClip
        speed = max(.5, min(1.5, 1 + float(profile.rate.rstrip('%')) / 100))
        data = self.client.synthesize(text, speed)
        decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32,
                                   nchannels=1, sample_rate=self.sample_rate)
        samples = np.asarray(decoded.samples, dtype=np.float32)
        if not samples.size or not np.isfinite(samples).all():
            raise RuntimeError('本机角色语音没有可播放音频')
        gain = max(0, min(2, 1 + float(profile.volume.rstrip('%')) / 100))
        samples = samples * gain
        peak = float(np.abs(samples).max())
        if peak > .9:
            samples *= .9 / peak
        return AudioClip(samples, self.sample_rate, text)


class PaddedTTS:
    """Keep the audio device active while the final syllable drains to WeChat."""
    def __init__(self, backend):
        self.backend = backend

    def synthesize(self, text, profile):
        import numpy as np
        from wecvoice_auto.tts import AudioClip
        clip = self.backend.synthesize(text, profile)
        return AudioClip(np.concatenate((clip.samples, np.zeros(round(clip.sample_rate * .6), dtype=np.float32))),
                         clip.sample_rate, clip.text)


def make_tts(preset, config=None):
    """Use exactly the selected backend; errors retain the caller's text fallback."""
    if preset.get('engine', 'edge') == 'gpt_sovits':
        if preset.get('id') != 'plana':
            raise ValueError('本机角色模型仅配置了普拉娜')
        return PaddedTTS(CharacterTTS(getattr(config, 'sample_rate', 48000)))
    if preset.get('engine', 'edge') != 'edge':
        raise ValueError('未知的语音合成引擎')
    from wecvoice_auto.tts import EdgeTTS
    return PaddedTTS(EdgeTTS(sample_rate=getattr(config, 'sample_rate', 48000),
                   timeout=getattr(config, 'edge_timeout', 30),
                   retries=getattr(config, 'edge_retries', 2)))


def close_character_voice():
    _client.close()
