"""Natural pauses, lossless bubble limits and old configuration compatibility."""
import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore
from app.service import BotService
from app.chat_bubbles import split_chat_bubbles
from app.prompting import parse_reply

with tempfile.TemporaryDirectory() as tmp:
    config = ConfigStore(Path(tmp)/'config.json')
    assert config.bot['max_reply_chars'] == 25
    assert config.bot['max_reply_bubbles'] == 20
    assert config.data['privacy']['screenshots'] and config.data['privacy']['screen_recording']
    assert not config.data['privacy']['wechat_read_screenshots']
    assert not config.data['privacy']['wechat_read_screen_recording']
    assert not BotService._looks_incomplete_reply('装饰器能给函数加新功能。')
    assert BotService._looks_incomplete_reply('装饰器能。')
    subject = SimpleNamespace(config=config)
    subject._natural_reply_text = lambda text, *args: text
    subject._looks_incomplete_reply = BotService._looks_incomplete_reply
    split = lambda text: BotService._split_reply_messages(subject, text, [], False, 15, 15)
    # Short sentences may share a bubble; the cap counts actual messages.
    assert len(split('明白了。'*10)) < 10
    sentences = ['这句话已经说完了。']*20
    assert split('\n'.join(sentences)) == sentences
    assert not split('\n'.join(sentences+sentences[:1]))
    text = '这个工具能处理数据，也可以帮你整理内容。'
    assert ''.join(split(text)) == text and all(len(s) <= 15 for s in split(text))
    long_clause = '这句话超过十五个字而且没有能够完整分句的地方。'
    pieces = split(long_clause)
    assert ''.join(pieces) == long_clause and all(len(piece) <= 15 for piece in pieces)
    config.bot['max_reply_bubbles'] = 2
    assert len(split('\n'.join(sentences[:2]))) == 2 and not split('\n'.join(sentences[:3]))
    config.bot.update(max_reply_chars=19, max_reply_bubbles=4)
    config.save()
    loaded = ConfigStore(config.path)
    assert loaded.bot['max_reply_chars'] == 19 and loaded.bot['max_reply_bubbles'] == 4

    assert split_chat_bubbles('嗯。老师，先别着急。我们一起慢慢看。',15,20) == [
        '嗯。老师，先别着急。','我们一起慢慢看。']
    assert split_chat_bubbles('老师，先把题目发给我，我陪你一起看看哪里卡住了。',15,20) == [
        '老师，先把题目发给我，','我陪你一起看看哪里卡住了。']
    assert split_chat_bubbles('这句话超过十五个字而且没有能够完整分句的地方。',15,20) == [
        '这句话超过十五个字','而且没有能够完整分句的地方。']
    paragraphs = '先把设置保存下来。\n然后重新打开程序看看。'
    assert split_chat_bubbles(paragraphs,25,20) == paragraphs.splitlines()
    assert split_chat_bubbles('老师，\n先别着急。',25,20) == ['老师，先别着急。']
    for source in ['可以试试Mon3tr这个角色，不过要先看看队伍里缺什么。',
                   '先把音量调整到25%，然后再试听一下。',
                   '他刚才说“不要着急”，我们先一起看看具体的问题。']:
        pieces = split_chat_bubbles(source,15,20)
        assert ''.join(pieces) == source and all(len(p)<=15 for p in pieces)
        assert all(p[0] not in '，。！？；：、）》”' for p in pieces)
        for token in ['Mon3tr','25%']:
            if token in source: assert any(token in p for p in pieces)
    url = 'https://example.org/article'
    assert not split_chat_bubbles('看看这个：'+url,15,20), 'never corrupt an indivisible link'
    assert not split_chat_bubbles('没有标点的内容已经超过十五个字需要重新组织语言',15,20,allow_soft_split=False)
    assert split_chat_bubbles('先把题目发给我，再一起慢慢看看。',15,20,allow_soft_split=False)
    # Cleanup for multi-bubble sending must not truncate at six sentences or
    # eighty characters, and must retain the model's intentional line breaks.
    source = '这句话已经说完了。\n'*10+'阿'*100+'。'
    clean = BotService._natural_reply_text(subject,source,[],False)
    assert clean == source.strip()
    assert parse_reply(paragraphs,[],enforce_limits=False)[0] == paragraphs
print('BUBBLE POLICY TEST PASSED')
