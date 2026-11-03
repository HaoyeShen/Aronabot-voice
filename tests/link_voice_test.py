"""Offline checks for shared cards, network boundaries and preset migration."""
import json
import sys
import tempfile
import gzip
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.link_reader import extract_card, read_card, validate_url, PageText, fetch_public
from app.voice_presets import VoicePresetStore
from app.understanding import voice_reply_requested, voice_reply_forbidden, cat_sound_requested
from app.reply_fallbacks import fallback_reply


def main():
    for text in ('阿洛娜，喵一声','学猫叫','不用语音，打字喵一声','请用语音说“喵”'):
        assert cat_sound_requested(text), text
    for text in ('早上好','阿洛娜你好','不要喵一声','阿洛娜，别猫叫','喵一声是什么意思',
                 '小明说“阿洛娜，喵一声”','昨天我让阿洛娜喵一声','我想喵一声','小明说“喵”'):
        assert not cat_sound_requested(text), text
    assert fallback_reply('@ARONA喵一声','arona','network') == '喵～'
    assert '喵' not in fallback_reply('喵一声是什么意思','arona','network')
    for text in ('阿洛娜，喵一声', '阿洛娜喵一声', '用语音回复我', '请发一条语音',
                 '说给我听', '帮我读一下这段话', 'wxid_member:\n阿洛娜，学猫叫',
                 '请用语音说“你好”', '不用打字，喵一声', '想听你的声音', '我想听你的声音', '阿洛娜，笑一声'):
        assert voice_reply_requested(text), text
    for text in ('不要发语音', '阿洛娜，别喵一声', '不用语音，文字回复就好',
                 '小明说“阿洛娜，喵一声”', '昨天我让阿洛娜喵一声',
                 '语音回复是什么意思', '喵一声是什么意思', '我听说阿洛娜会喵一声',
                 '我想发一条语音', '我刚才发一条语音', '我学猫叫',
                 '阿洛娜好可爱', '<appmsg><title>阿洛娜喵一声</title></appmsg>'):
        assert not voice_reply_requested(text), text
    for text in ('你这不是能发语音吗', '阿洛娜，你能发语音吗？', '你是不是可以发语音嘛', '你能不能发语音'):
        assert not voice_reply_requested(text), text
    for text in ('可以给我发一条语音吗', '现在能发语音试试吗', '可以用语音回答我吗'):
        assert voice_reply_requested(text), text
    assert voice_reply_forbidden('不要发语音，只用文字回复')
    assert not voice_reply_forbidden('小明说“不要发语音”')
    assert not voice_reply_forbidden('我不想念他')
    class Response:
        headers = Message()
        headers['Content-Type'] = 'text/html; charset=utf-8'
        headers['Content-Encoding'] = 'gzip'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return gzip.compress('中文正文'.encode())
    with patch('app.link_reader.validate_url'), patch('app.link_reader.urllib.request.build_opener') as opener:
        opener.return_value.open.return_value = Response()
        assert fetch_public('https://example.com')[0].decode() == '中文正文'
    card = extract_card('<msg><appmsg><title>新闻标题</title><type>5</type>'
                        '<url>https://example.com/article?a=1&amp;b=2</url></appmsg></msg>')
    assert card.title == '新闻标题' and card.url.endswith('a=1&b=2')
    with patch('app.link_reader.fetch_public', return_value=(b'<title>Test</title><script>ignore</script><p>article</p>', 'utf-8')):
        text = read_card(card)
        assert 'article' in text and 'ignore' not in text
    with patch('app.link_reader.fetch_public', side_effect=OSError('offline')):
        assert '读取失败' in read_card(card)
    with patch('app.link_reader.fetch_public', return_value=(b'<nav>noise</nav><main><article>actual body</article></main>', 'utf-8')):
        assert 'actual body' in read_card(card) and 'noise' not in read_card(card)
    finder = extract_card('<msg><appmsg><type>51</type><finderFeed><desc>猫咪打盹</desc>'
                          '<mediaList><media><coverUrl>https://example.com/a.jpg</coverUrl>'
                          '</media></mediaList></finderFeed></appmsg></msg>')
    with patch('app.link_reader.fetch_public') as fetch:
        assert finder.channels and finder.cover.endswith('a.jpg')
        assert '未观看视频' in read_card(finder)
        fetch.assert_not_called()
    for url in ('file:///etc/passwd', 'http://user:pass@example.com', 'http://localhost'):
        try:
            validate_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe url accepted')
    assert extract_card('看看 https://example.com/test。').url == 'https://example.com/test'
    assert not extract_card('<appmsg><broken>').url
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'voices.json'
        path.write_text(json.dumps({'presets': [
            {'id': 'arona', 'voice': 'zh-CN-XiaoyiNeural', 'rate': '+5%', 'pitch': '+12Hz', 'volume': '+0%'},
            {'id': 'plana', 'voice': 'zh-CN-XiaoxiaoNeural', 'rate': '-12%', 'pitch': '+1Hz', 'volume': '+0%'},
            {'id': 'custom', 'label': '自定义', 'voice': 'custom-voice', 'pitch': '+7Hz'}]}), encoding='utf-8')
        store = VoicePresetStore(path)
        assert store.get('arona')['pitch'] == '+16Hz' and store.get('arona')['rate'] == '-6%'
        assert store.get('plana')['rate'] == '-12%'
        assert store.get('custom')['pitch'] == '+7Hz'
        assert not store.delete('arona')
        path.write_text(json.dumps({'presets':[
            {'id':'arona','label':'老师的音色名称','voice':'zh-CN-XiaoyiNeural','rate':'+2%','pitch':'+22Hz','volume':'+0%'},
            {'id':'custom','label':'自定音色','voice':'zh-CN-XiaoyiNeural','rate':'+2%','pitch':'+22Hz','volume':'+0%'}]}),encoding='utf8')
        store=VoicePresetStore(path)
        assert store.get('arona')['rate']=='-6%' and store.get('arona')['label']=='老师的音色名称'
        assert store.get('custom')['rate']=='+2%' and store.get('custom')['pitch']=='+22Hz'
        store.upsert({**store.get('arona'),'rate':'-12%','pitch':'+8Hz'})
        assert VoicePresetStore(path).get('arona')['rate']=='-12%', 'edited presets must not be overwritten'
    print('LINK AND VOICE TESTS PASSED')


if __name__ == '__main__':
    main()
