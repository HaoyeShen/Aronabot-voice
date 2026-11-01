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


def main():
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
        assert store.get('arona')['pitch'] == '+22Hz'
        assert store.get('plana')['rate'] == '-12%'
        assert store.get('custom')['pitch'] == '+7Hz'
        assert not store.delete('arona')
    print('LINK AND VOICE TESTS PASSED')


if __name__ == '__main__':
    main()
