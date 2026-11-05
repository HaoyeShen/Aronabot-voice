"""Request depth policy; medium deliberately preserves the existing baseline."""
from __future__ import annotations

LABELS = {'light': '轻度', 'medium': '中', 'high': '高', 'extreme': '极高', 'high_consumption': '高消耗'}
_FACTORS = {'light': .6, 'medium': 1., 'high': 2., 'extreme': 4., 'high_consumption': 1.}
_RULES = {
    'light': '优先直接使用已知事实，简短回答；仅在任务确实需要时调用工具，避免重复检索。',
    'medium': '',
    'high_consumption': '',
    'high': '复杂问题多核对一步关键事实，结合工具证据作答；简单聊天仍然简短，不展开分析过程。',
    'extreme': '复杂任务充分核实关键前提、矛盾和来源，必要时交叉验证；证据充分就结束，不反复调用工具。简单聊天保持自然简短。',
}


def level(value):
    return value if isinstance(value, str) and value in LABELS else 'medium'


def output_tokens(tokens, effort):
    base = max(1, int(tokens))
    return base if level(effort) in ('medium','high_consumption') else max(64, min(32768, round(base * _FACTORS[level(effort)])))


def instruction(effort):
    rule = _RULES[level(effort)]
    return ('\n【任务深度】' + rule) if rule else ''


def agent_budget(bot):
    effort = level(bot.get('reasoning_intensity'))
    steps = max(1, min(8, int(bot.get('agent_max_steps', 4))))
    timeout = max(1, float(bot.get('agent_timeout_seconds', 90)))
    if effort in ('medium','high_consumption'):
        return steps, timeout
    return ({'light': max(1, steps - 2), 'high': min(8, steps + 2), 'extreme': 8}[effort],
            timeout * {'light': 1., 'high': 1.5, 'extreme': 2.}[effort])


def legacy_mode(bot):
    return level(bot.get('reasoning_intensity')) == 'high_consumption'
