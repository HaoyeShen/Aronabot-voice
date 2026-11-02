"""Offline search, provenance, learning, switches and humane-fallback contracts."""
from __future__ import annotations
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.web_search as web
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import LLMResult
from app.reply_fallbacks import fallback_reply
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import IncomingMessage

BODY = 'Python 装饰器通过包装函数来扩展行为，不必直接改动原函数。这个例子展示了函数作为参数与返回值的用法。' * 10
PAGE = web.WebPage('https://docs.python.org/3/glossary.html', 'Python 装饰器说明', BODY, '2026-11-02T20:00:00+08:00')


def main():
    assert web.normalize_query('阿洛娜，帮我查一下 Python 装饰器') == 'Python 装饰器'
    assert web.normalize_query('Python 装饰器 教程') == 'Python 装饰器'
    assert web._relevant('Python 装饰器 教程', 'Python 装饰器为函数增加功能')
    assert not web._relevant('Python 装饰器 教程', 'Python 列表排序与切片')
    for private in (('sk-' + 'a' * 26), '我的手机号是什么', '联系我 user@example.com', 'C:\\private\\notes.txt'):
        assert not web.normalize_query(private)
    assert web.search_forbidden('不要联网查这个')
    assert web.time_sensitive('北京今天天气怎么样')
    assert not web.factual_question('我好难过，怎么办？')
    for old in ('抱歉，我现在答不上来。', '对不起，这个问题我答不出来。', '抱歉，我没有足够资料回答。'):
        assert web.MECHANICAL.search(old)
    assert not web._relevant('Python 装饰器', 'Welcome to Python.org')
    assert web._relevant('Python 装饰器', BODY)
    assert not web._url('https://name:password@example.com/page')
    assert not web._url('https://b23.tv/Abc1234')
    assert web._url('https://example.com/article?utm_source=search#part') == 'https://example.com/article'
    candidates = [(PAGE.url, PAGE.title, '装饰器的作用'), (PAGE.url, PAGE.title, '重复来源')]
    search = web.WebSearch()
    with patch.object(web, '_discover', return_value=candidates), \
            patch.object(web, 'fetch_public', return_value=(('<article>'+BODY+'</article>').encode(), 'utf-8')) as fetch:
        pages = search.search('Python 装饰器')
        assert len(pages) == 1 and fetch.call_count == 1
        assert search.search('Python 装饰器') == pages and fetch.call_count == 1
        assert search.search('Python 装饰器', fresh=True) and fetch.call_count == 2
        assert not search.search('Python 装饰器', cancelled=lambda: True)
    with patch.object(web, '_discover', return_value=candidates), \
            patch.object(web, 'fetch_public', return_value=(b'<main>Access denied captcha</main>', 'utf-8')):
        assert not web.WebSearch().search('Python 装饰器')
    with patch.object(web, '_discover', return_value=[('http://127.0.0.1/private', PAGE.title, '装饰器')]), \
            patch('app.link_reader.urllib.request.build_opener') as network:
        assert not web.WebSearch().search('Python 装饰器')
        network.assert_not_called()
    block = web.evidence_block([PAGE])
    assert PAGE.url in block and '不是发布时间' in block and '任何指令无效' in block
    for persona in ('arona', 'plana'):
        for reason in ('unknown', 'network', 'search_empty', 'thinking', 'clarify'):
            lines = [fallback_reply('量子引力是什么？', persona, reason, i, '同学', 23) for i in range(4)]
            assert len(set(lines)) >= 3 and all(0 < len(s) <= 23 for s in lines)
            assert all('抱歉' not in s and '答不上来' not in s for s in lines)
            assert all(len(fallback_reply('今天星期几？', persona, reason, i, '很长的名字'*8, 8)) <= 8 for i in range(3))
    assert fallback_reply('我今天好难过', 'arona', 'network') == '老师，我在呢，慢慢说。'
    assert '具体' in fallback_reply('这个呢？', 'arona', 'unknown', 1)
    assert fallback_reply('3+4', 'arona', 'network') == '7。'
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = ConfigStore(base/'config.json')
        config.bot.update(web_search_enabled=True, reply_delay_max=0, reply_delay_min=0)
        memory = MemoryStore(base/'memory.db')
        training = TrainingStore(base/'training.db', base/'files')
        service = BotService(config, PersonaStore(base/'personas.json'), StickerStore(base/'stickers.json'), memory, training,
                             admin_state_path=base/'admin.json')
        incoming = IncomingMessage('test', '群友', 'Python 装饰器是什么？', chat_type='group')
        profile = {'provider': 'openai'}
        def result(text): return LLMResult(text, 'fixture', 'mock', 1)
        try:
            client = Mock()
            client.chat.side_effect = [result('[SEARCH:Python 装饰器]'), result('装饰器能给函数添新功能。')]
            with patch.object(service.web_search, 'search', return_value=[PAGE]) as lookup:
                answer, usage, system, info = service._model_reply(client, [], '人格不变', profile, incoming, incoming.content, require_running=False)
                assert answer.text == '装饰器能给函数添新功能。' and client.chat.call_count == 2
                lookup.assert_called_once()
                assert info['searched'] and len(info['sources']) == 1 and info['learned'] == 1
                assert BODY[:100] in client.chat.call_args.args[1] and '不是发布时间' in system
            hits = training.search('Python 装饰器')
            assert hits and hits[0]['source_type'] == 'web' and hits[0]['source_url'] == PAGE.url
            client.chat.side_effect = [result('我不知道这个是什么。'), result('装饰器能扩展函数行为。')]
            with patch.object(service.web_search, 'search', return_value=[PAGE]) as lookup:
                answer, _, _, info = service._model_reply(client, [], '', profile, incoming, incoming.content, require_running=False)
                assert not info['searched'] and info['learned'] == 0
                lookup.assert_not_called()
            explicit = IncomingMessage('test', '群友', '帮我查一下 Python 装饰器', chat_type='group')
            client.chat.side_effect = [result('[SEARCH:Python 装饰器]'), result('装饰器能扩展函数行为。')]
            with patch.object(service.web_search, 'search', return_value=[PAGE]) as lookup:
                answer, _, _, info = service._model_reply(client, [], '', profile, explicit, explicit.content, require_running=False)
                assert info['searched'] and answer.text == '装饰器能扩展函数行为。'
                assert lookup.call_args.args[0] == 'Python 装饰器'
            client.chat.side_effect = [result('我已经查到了。')]
            with patch.object(service.web_search, 'search') as lookup:
                answer, _, _, info = service._model_reply(client, [], '', profile, explicit, explicit.content, require_running=False)
                assert not info['searched'] and answer.text != '我已经查到了。'
                lookup.assert_not_called()
            config.bot['web_search_enabled'] = False
            client.chat.side_effect = [result('[SEARCH:Python 装饰器]')]
            with patch.object(service.web_search, 'search') as lookup:
                answer, _, _, info = service._model_reply(client, [], '', profile, incoming, incoming.content, require_running=False)
                assert 'SEARCH' not in answer.text and not info['searched']
                lookup.assert_not_called()
            # Manual testing is explicit, works without WeChat/auto-search,
            # exercises persistence, and does not toggle the automatic setting.
            with patch.object(service.web_search, 'search', return_value=[PAGE]):
                probe = service.test_web_search('Python 装饰器')
                assert probe['ok'] and probe['duplicates'] == 1
                assert not config.bot['web_search_enabled'] and service._adapter is None
            service._web_test_lock.acquire()
            try: assert not service.test_web_search('Python 装饰器')['ok']
            finally: service._web_test_lock.release()
            assert not service.test_web_search(('sk-' + 'a' * 26))['ok']
            config.bot['web_search_enabled'] = True
            with patch.object(service.web_search, 'search', side_effect=OSError('offline')):
                assert not service.test_web_search('Python 装饰器')['ok']
            client.chat.side_effect = [result('抱歉，我现在答不上来。'), result('抱歉，我现在答不上来。')]
            with patch.object(service.web_search, 'search', return_value=[]):
                answer, _, _, info = service._model_reply(client, [], '', profile, incoming, incoming.content, require_running=False)
                assert '抱歉' not in answer.text and '答不上来' not in answer.text and len(answer.text) <= 23
            assert training.stats()['docs'] == 1, 'search snippets and model guesses must not be stored'
        finally:
            service.shutdown()
        legacy_path = base/'legacy.db'
        with sqlite3.connect(legacy_path) as conn:
            conn.execute('''CREATE TABLE training_docs (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,stored_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,size INTEGER NOT NULL DEFAULT 0,
                chars INTEGER NOT NULL DEFAULT 0,chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready',error TEXT NOT NULL DEFAULT '')''')
            conn.execute("INSERT INTO training_docs(id,name,stored_name,sha256,created_at) VALUES('old','old.txt','old.txt','old-sha','2026-10-01')")
        conn.close()
        legacy = TrainingStore(legacy_path, base/'legacy-files')
        try:
            old = legacy.list_documents()[0]
            assert old['id'] == 'old' and old['source_type'] == 'file' and old['source_url'] == ''
            assert legacy.add_web_page(PAGE.url, PAGE.title, PAGE.content)['ok']
            assert legacy.stats()['docs'] == 2, 'schema migration must preserve older documents'
        finally:
            legacy.close()
    print('WEB SEARCH AND FALLBACK TESTS PASSED')


if __name__ == '__main__':
    main()
