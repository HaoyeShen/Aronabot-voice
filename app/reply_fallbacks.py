"""Short, situational replies when the model cannot produce a useful answer."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from .prompting import address_name
from .understanding import cat_sound_requested


LINES = {
    'arona': {
        'unknown': ('{t}，我不会呀。', '{t}，这个我还不懂呀。', '{t}，这题我也得学学。'),
        'network': ('{t}，网络刚刚卡住啦。', '{t}，刚刚没连上呢。', '{t}，这次连接没成功呀。'),
        'search_empty': ('{t}，还没找到靠谱的说法。', '{t}，能再给我一点线索吗？', '{t}，这次还没查到呢。'),
        'thinking': ('{t}，我一下没理清呢。', '{t}，这题让我有点迷糊啦。', '{t}，我还没琢磨明白呢。'),
        'clarify': ('{t}，你指的是哪一件事呀？', '{t}，能再说具体一点吗？', '{t}，我有点没听明白。'),
        'help': ('{t}，哪里需要我帮忙呀？', '{t}，说说遇到什么事啦。', '{t}，你想先解决哪件事呀？'),
        'comfort': ('{t}，我在呢，慢慢说。', '辛苦啦，{t}，我陪着你。', '{t}，我会认真听你说的。'),
        'greeting': ('{t}好呀，阿洛娜在呢。', '{t}，今天也请多关照！', '{t}，见到你真开心。'),
    },
    'plana': {
        'unknown': ('{t}，这部分我还不熟悉。', '{t}，这题还需要核实。', '{t}，这个我还不懂。'),
        'network': ('{t}，刚才连接中断了。', '{t}，暂时没连上网络。', '{t}，这次连接没有成功。'),
        'search_empty': ('{t}，还没有查到可靠说法。', '{t}，请再给我一点线索。', '{t}，目前没有查到。'),
        'thinking': ('{t}，这里我还没理清。', '{t}，这题需要再想一想。', '{t}，我还没弄明白。'),
        'clarify': ('{t}，你指的是哪一件事？', '{t}，可以说具体一点吗？', '{t}，请补充一点信息。'),
        'help': ('{t}，哪里需要我帮忙？', '{t}，遇到了什么事？', '{t}，你想先处理哪件事？'),
        'comfort': ('{t}，我在这里。', '{t}，我会认真听你说。', '{t}，我会陪着你。'),
        'greeting': ('{t}，你好。', '{t}，我在。', '{t}，很高兴见到你。'),
    },
}


def fallback_reply(text: str, persona_id: str, reason: str, turn: int = 0,
                   sender: str = '', limit: int = 23, *, persona: dict | None = None) -> str:
    """Choose complete sentences, never truncate a name or fabricate facts."""
    requested_cat = cat_sound_requested(text)
    text = re.sub(r'@\S+\s*', '', text or '').strip()
    if re.search(r'(?:我|最近|今天|心情).{0,8}(?:难过|伤心|委屈|崩溃|痛苦|好累|失恋|失业|想哭|不好)|^(?:好难过|好伤心|想哭|好累)', text):
        reason = 'comfort'
    elif re.fullmatch(r'(?:阿洛娜|普拉娜)?[，,\s]*(?:帮帮我|帮我一下|救救我|帮个忙)[呀啊吧！!。？?\s]*', text):
        reason = 'help'
    elif len(text) <= 18 and re.search(r'^(?:阿洛娜|普拉娜)?[，,\s]*(?:你好|早上好|晚上好|在吗|嗨|哈喽)', text):
        reason = 'greeting'
    elif re.fullmatch(r'(?:这个|那个|它|这件事|那件事)(?:呢|怎么样|怎么说|怎么办)[？?。\s]*', text):
        reason = 'clarify'
    elif requested_cat:
        return '喵～'
    elif re.search(r'今天(?:是)?几(?:号|月|日)|今天星期几|今天周几', text):
        now = datetime.now().astimezone()
        line = f'今天{now.month}月{now.day}日，星期{"一二三四五六日"[now.weekday()]}。'
        if len(line) <= limit:
            return line
    # Basic arithmetic remains answerable even if every remote service is down.
    match = re.fullmatch(r'(?:算一下|算算|请问)?\s*(-?\d{1,8}(?:\.\d{1,4})?)\s*([+\-*/×÷])\s*(-?\d{1,8}(?:\.\d{1,4})?)(?:\s*[=＝？?]|等于多少|是多少)?', text)
    if match:
        try:
            a, op, b = Decimal(match[1]), match[2], Decimal(match[3])
            if op in ('/', '÷') and not b:
                line = '零不能作为除数。'
                return line if len(line) <= limit else '不能除零。'
            value = a+b if op == '+' else a-b if op == '-' else a*b if op in ('*', '×') else a/b
            answer = format(value.normalize(), 'f')
            if len(answer) + 1 <= limit:
                return answer + '。'
        except (InvalidOperation, ArithmeticError):
            pass
    builtin = (persona_id in ('arona', 'plana') and
               (persona is None or persona.get('name') in ('阿洛娜', '普拉娜')))
    choices = LINES['plana' if persona_id == 'plana' or not builtin else 'arona']
    options = choices.get(reason, choices['unknown'])
    if not builtin:
        # Without a running model, avoid inventing a custom character's title
        # or borrowing Arona's catchphrases. An explicit template is safe.
        names = [address_name(sender, persona)] if persona and persona.get('address_template') else ['']
    elif persona and persona.get('address_template'):
        names = [address_name(sender, persona), '']
    else:
        names = [address_name(sender), '老师']
    for teacher in dict.fromkeys(names):
        for offset in range(len(options)):
            line = options[(turn + offset) % len(options)].format(t=teacher).lstrip('，, ')
            if len(line) <= limit:
                return line
    return '这部分还不确定。'
