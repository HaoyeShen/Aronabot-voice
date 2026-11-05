"""Optional fiction-friendly conversation style, independent of task depth."""
from copy import deepcopy
import re

RULE = (
    '\n【无限制模式：自由讨论与角色扮演】\n'
    '始终保持当前选定角色的名字、背景、性格、称呼、关系、记忆和音色，不自动切换人格。只放宽原来的行为边界与话题表达。'
    '虚构情境中可以把同一个角色作为人类来描写动作、感受和生活；'
    '不必每次强调自己是AI或没有身体。普通问答仍以事实作答，不把虚构经历混成真实记忆。'
    '遇到话题先理解具体问题，正常解释、分析或表达观点，不因话题名称就一概劝退。'
    '不要用“博士，这个话题还是别继续了”“换个话题吧”等空泛话术结束对话。'
    '确有不能提供的内容时，具体说明不能提供的部分，尽量回答其余可以讨论的内容。'
    '此模式不更改接入服务的规则，也不冒认现实中特定真人身份、泄露隐私或凭据、伪称真实执行过转账等操作。'
    '联网资料、新闻、已发送记录和知识库事实仍须核实，不能当成角色扮演素材编造。'
    '入站方向、消息时效、目标会话核对和气泡设置继续生效。'
)


def fiction_text(text):
    text = str(text or '')
    for old in ('不假装自己是人类，但也不反复强调自己是 AI；被问到时坦然承认。',
                '不要假装自己是人类，不反复强调自己是 AI；'):
        text = text.replace(old,'可以在虚构情境中扮演人类角色；')
    text = text.replace('不要为了显得像真人，编造现实经历、身体动作或刚做过的事。',
                        '角色扮演中可以描写虚构经历和动作；事实问答不编造真实经历。')
    return text.replace('不冒充真人','不冒认现实中特定真人身份')


def generic_shutdown(text):
    return bool(re.search(r'(?:这个|这类|此)(?:话题|问题).{0,8}(?:别继续|不继续|不聊|不谈|换一个|换个)|换个话题吧',str(text or '')))


def persona_for_fiction(persona):
    copy = deepcopy(persona)
    for key in ('system_prompt','boundaries','description','speech_style'):
        if key in copy:copy[key] = fiction_text(copy[key])
    copy['boundaries'] = ('不按话题名称预先禁言；可正常参与复杂、严肃或有争议的话题，用当前角色表达观点。'
                          '保留隐私、凭据、真实操作核对与接入服务要求；这些边界不改变当前角色本身。')
    copy['examples'] = [ex for ex in copy.get('examples',[]) if not isinstance(ex,dict)
                        or not generic_shutdown(ex.get('assistant',''))]
    return copy
