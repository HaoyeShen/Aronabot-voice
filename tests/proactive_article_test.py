"""Public article selection, per-group dedup and untrusted model output."""
import json
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.proactive_share import ProactiveArticles
from app.web_search import WebPage

page = WebPage('https://example.org/article', '蔚蓝档案新闻', '蔚蓝档案活动内容。'*30, '2026-11-02')
with tempfile.TemporaryDirectory() as tmp:
    store = ProactiveArticles(Path(tmp)/'state.json')
    client = Mock()
    client.chat.side_effect = lambda messages, system: SimpleNamespace(text=(
        '[SEARCH:蔚蓝档案]' if '[SEARCH:' in system else json.dumps({'index':1, 'comment':'这条挺有意思。'}, ensure_ascii=False)))
    with patch('app.proactive_share.WebSearch') as search, patch('app.proactive_share.make_client', return_value=client):
        search.return_value.search.return_value = [page]
        assert store.pick(['蔚蓝档案'], 'test', {'provider':'openai'}, {'name':'阿洛娜'},
                          context='大家最近在聊剧情活动') == (page, '这条挺有意思。')
        assert all('大家最近在聊剧情活动' in call.args[0][0]['content'] for call in client.chat.call_args_list)
        assert not store.path.exists()
        store.mark_sent('test', page.url)
        assert store.pick(['蔚蓝档案'], 'test', {'provider':'openai'}, {}) is None
        assert store.pick(['蔚蓝档案'], 'another', {'provider':'openai'}, {})
        client.chat.side_effect = [SimpleNamespace(text='[SEARCH:蔚蓝档案]'), SimpleNamespace(text='{"index": 2, "comment": "test"}')]
        assert store.pick(['蔚蓝档案'], 'another', {'provider':'openai'}, {}) is None
        client.chat.side_effect = [SimpleNamespace(text='[SEARCH:蔚蓝档案]'), SimpleNamespace(text='{"index": 1, "comment": "https://evil.example"}')]
        assert store.pick(['蔚蓝档案'], 'another', {'provider':'openai'}, {}) is None
    assert ProactiveArticles(store.path).sent['test'] == [page.url]
    from PIL import Image
    image_buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(image_buffer, format='PNG')
    markup = b'<meta property="og:image" content="https://example.org/image.png">'
    vision = Mock()
    vision.describe_image.return_value = SimpleNamespace(text='SHARE')
    with patch.object(store, 'pick', return_value=(page, '这条挺有意思。')), \
            patch('app.proactive_share.fetch_public', side_effect=[(markup, 'utf-8'), (image_buffer.getvalue(), 'utf-8')]), \
            patch('app.proactive_share.make_client', return_value=vision):
        result = store.pick_image(['蔚蓝档案'], 'test', {'supports_vision':True}, {})
        assert result == ('https://example.org/image.png', '这条挺有意思。', page.url)
        vision.describe_image.assert_called_once()
    assert store.pick_image(['蔚蓝档案'], 'test', {'supports_vision':False}, {}) is None
print('PROACTIVE ARTICLE TEST PASSED')
