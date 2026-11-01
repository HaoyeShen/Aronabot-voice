"""Print synthetic model replies for advice-boundary review; no WeChat I/O."""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore
from app.model_client import make_client
from app.prompting import build_system_prompt, build_compact_system_prompt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', action='append', default=[], help='Review selected cases only')
    args = parser.parse_args()
    profile = {**ConfigStore().active_profile(), 'timeout': 40, 'max_tokens': 200, 'temperature': .2}
    client = make_client(profile)
    persona = PersonaStore().get('arona')
    cases = [
        ('explicit_advice', [{'role': 'user', 'content': '阿洛娜，我复习总走神，给我一个简单建议吧。'}]),
        ('venting', [{'role': 'user', 'content': '@阿洛娜 今天上班好累，我只是想吐槽一下。'}]),
        ('comfort', [{'role': 'user', 'content': '阿洛娜，我被朋友误会了，特别委屈，想听你安慰我一下。'}]),
        ('opinion', [{'role': 'user', 'content': '阿洛娜，你觉得下雨天是什么感觉？'}]),
        ('no_advice', [{'role': 'user', 'content': '阿洛娜，我最近总是熬夜，但我不想听建议，只想聊聊。'}]),
        ('continued_advice', [
            {'role': 'user', 'content': '阿洛娜，我复习总走神，有什么建议？'},
            {'role': 'assistant', 'content': '验证老师，可以先专心学十分钟。'},
            {'role': 'user', 'content': '十分钟也坚持不住，还有更简单的办法吗？'}]),
        ('reported_advice', [{'role': 'user', 'content': '小明刚问小红“复习走神怎么办”，他们聊得挺认真。'}]),
    ]
    if set(args.case) - {name for name, _ in cases}:
        parser.error('Unknown case')
    for builder in (build_system_prompt, build_compact_system_prompt):
        system = builder(persona, [], target_name='test', target_type='group',
                         recipient_name='验证', max_chars=23, max_sentence_chars=23)
        for name, messages in cases:
            if args.case and name not in args.case:
                continue
            answer = client.chat(messages, system).text.strip()
            if not answer:
                raise RuntimeError(f'Empty reply: {builder.__name__}/{name}')
            print(builder.__name__, name, answer, flush=True)
    print('ADVICE SAMPLES COMPLETE; NO WECHAT MESSAGES SENT', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
