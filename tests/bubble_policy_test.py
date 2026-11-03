"""Complete sentence bubble limits and old configuration compatibility."""
import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore
from app.service import BotService

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
    assert len(split('明白了。'*10)) == 10
    assert len(split('明白了。'*20)) == 20
    assert not split('明白了。'*21)
    text = '这个工具能处理数据，也可以帮你整理内容。'
    assert ''.join(split(text)) == text and all(len(s) <= 15 for s in split(text))
    long_clause = '这句话超过十五个字而且没有能够完整分句的地方。'
    pieces = split(long_clause)
    assert ''.join(pieces) == long_clause and all(len(piece) <= 15 for piece in pieces)
    config.bot['max_reply_bubbles'] = 2
    assert len(split('明白了。'*2)) == 2 and not split('明白了。'*3)
    config.bot.update(max_reply_chars=19, max_reply_bubbles=4)
    config.save()
    loaded = ConfigStore(config.path)
    assert loaded.bot['max_reply_chars'] == 19 and loaded.bot['max_reply_bubbles'] == 4
print('BUBBLE POLICY TEST PASSED')
