"""Offline tests for the daily Kivotos news collector/scheduler."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_store import ConfigStore, PersonaStore
from app.news import (NewsItem, NewsService, _BingParser, _past_tense_issue,
                      _sanitize_past_tense)
from tests.service_test import FakeAdapter
from app.wechat_adapter import SendResult
from app.news_evidence import ArticleEvidence, published_time, is_recent, read_article


class _RecordingAdapter(FakeAdapter):
    def __init__(self, config, callback):
        super().__init__(config, callback)


def main() -> int:
    # Bing result parser remains a fallback when Bilibili search is unavailable.
    sample = """<ol><li class="b_algo"><h2><a href="https://example.com/a">Blue Archive update</a></h2>
    <p>A new event is coming.</p><cite>example.com</cite></li></ol>"""
    now = datetime(2026, 10, 30, 6, 50)
    assert _past_tense_issue("日服会在10月28日维护后开启。", now)
    assert not _past_tense_issue("日服已于10月28日维护后开启。", now)
    assert not _past_tense_issue("活动将于11月2日开启。", now)
    assert "预计" not in _sanitize_past_tense("预计10月29日开启。", now)
    assert "已开启" in _sanitize_past_tense("10月28日会开启。", now)

    parser = _BingParser()
    parser.feed(sample)
    parser.close()
    assert parser.items and parser.items[0]["title"] == "Blue Archive update", parser.items

    fixed = datetime(2026, 11, 2, 8, 0)
    assert published_time('2026-11-01T23:30:00Z') == '2026-11-02T07:30:00'
    assert published_time('2026.11.02') == '2026-11-02'
    assert published_time('2026-11-02T07:30:00.123456') == '2026-11-02T07:30:00'
    assert is_recent('2026-11-02T07:30:00', fixed)
    assert not is_recent('2026-10-31T07:30:00', fixed)
    assert not is_recent('2026-11-02T09:00:00', fixed)
    assert not is_recent('2026-11-01', fixed)  # yesterday without a time is unproven
    assert not is_recent('', fixed)
    evidence = ArticleEvidence()
    evidence.feed('<meta property="article:published_time" content="2026-10-01T12:00:00+08:00">'
                  '<meta property="article:modified_time" content="2026-11-02T07:00:00+08:00">'
                  '<article>活动将于2026年11月3日开启</article>')
    assert evidence.evidence()[0] == '2026-10-01T12:00:00'
    evidence = ArticleEvidence()
    evidence.feed('<time datetime="2026-11-02T07:00:00">活动日期</time>'
                  '<script type="application/ld+json">{"@type":"NewsArticle",'
                  '"dateModified":"2026-11-02T07:00:00"}</script>')
    assert not evidence.evidence()[0], 'event and modification dates are not publication dates'
    with patch('app.link_reader.fetch_public', return_value=(
            b'<meta property="article:published_time" content="2026-11-02T07:00:00">'
            b'<nav>navigation noise</nav><article><h1>Blue Archive update</h1>new facts</article>', 'utf-8')):
        stamp, body, images, title = read_article('https://example.com/article')
        assert stamp and 'new facts' in body and 'navigation noise' not in body and title == 'Blue Archive update'

    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / "config.json")
        config.set_active_profile("demo-local")
        from types import SimpleNamespace
        from unittest.mock import Mock
        probe = NewsService(config, PersonaStore(Path(tmp) / 'persona-probe.json'), state_path=Path(tmp) / 'probe.json')
        output = '{"国服":"蔚蓝档案 国服 更新", "日服":"ブルーアーカイブ 告知", "国际服":"https://example.com/video"}'
        with patch.object(config, 'active_profile', return_value={'provider': 'openai'}), \
                patch('app.news.make_client') as client:
            client.return_value.chat.return_value = SimpleNamespace(text=output)
            terms = probe._model_search_queries()
            assert terms == {'国服': '蔚蓝档案 国服 更新', '日服': 'ブルーアーカイブ 告知'}
        with patch.object(config, 'active_profile', return_value={'provider': 'openai'}), \
                patch('app.news.make_client', side_effect=RuntimeError('offline')):
            assert probe._model_search_queries() == {}
        config.add_target("新闻群", "group", "arona")
        config.add_target("好友", "friend", "arona")
        config.data["news"] = {
            "enabled": True, "send_time": "06:50", "target_chat": "新闻群",
            "max_items": 2, "timeout": 10, "use_model_summary": False,
        }
        for item in config.targets():
            item["news_enabled"] = False
        config.save()
        service = NewsService(config, PersonaStore(), state_path=Path(tmp) / "state.json")
        adapter = _RecordingAdapter(config, lambda _message: None)
        items = {
            "国服": [NewsItem("国服", "国服更新公告", "https://example.com/cn",
                              "官方账号", snippet='国服发布了新的维护公告。', uncertain=False,
                              published_at=datetime.now().isoformat(), body_read=True)],
            "日服": [NewsItem("日服", "日服新卡池爆料", "https://example.com/jp",
                              "搬运账号", snippet='日服公布了新的招募消息，详情尚待确认。', uncertain=True,
                              published_at=datetime.now().isoformat(), body_read=True)],
            "国际服": [],
        }
        service.fetch = lambda: items  # type: ignore[assignment]

        # A named manual test works with scheduling disabled and does not alter
        # attempts, daily slots, errors or content history, even on failure.
        service._save_state({'last_error': 'keep', 'sent_dates': {'正式群': '2026-11-01'}})
        before_test = service.state_path.read_bytes()
        for _ in range(2):
            result = service.run(adapter, target={'name': 'test', 'type': 'group'}, test_mode=True)
            assert result['sent'] and not result['no_new_items']
            assert service.state_path.read_bytes() == before_test
        with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')):
            assert not service.run(adapter, target={'name': 'test'}, test_mode=True)['sent']
        assert service.state_path.read_bytes() == before_test
        with patch.object(adapter, 'send_text', side_effect=RuntimeError('offline')):
            assert not service.run(adapter, target={'name': 'test'}, test_mode=True)['sent']
        assert service.state_path.read_bytes() == before_test
        adapter.sent.clear()
        service.state_path.unlink()

        assert service.should_run(datetime.now().replace(hour=6, minute=49))[0] is False
        result = service.run(adapter, force=True)
        assert result["sent"] and adapter.sent, result
        assert "国服" in result["message"] and "未确认" in result["message"]
        assert service.last_sent_date() == "", "manual test must not consume the daily slot"

        result = service.run(adapter, now=datetime.now().replace(hour=6, minute=51))
        assert result["sent"], result
        assert service.last_sent_date() == datetime.now().date().isoformat()
        again = service.run(adapter, now=datetime.now().replace(hour=6, minute=52))
        assert not again["sent"] and again["reason"] == "今天已经发送过"
        assert len(adapter.sent) == 2 and all(row[0] == "新闻群" for row in adapter.sent)

        # Disabled means absolutely no fetch.
        config.data["news"]["enabled"] = False
        for item in config.targets():
            item["news_enabled"] = False
        called = False

        def must_not_fetch():
            nonlocal called
            called = True
            raise AssertionError("fetch must not run while disabled")

        service.fetch = must_not_fetch  # type: ignore[assignment]
        result = service.run(adapter, force=True)
        assert not result["sent"] and not called

        # Invalid target must fail before any network access.
        config.data["news"].update({"enabled": True, "target_chat": "好友"})
        result = service.run(adapter)
        assert not result["sent"] and "不是群聊" in result["reason"]

    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / 'config.json')
        config.set_active_profile('demo-local')
        assert config.data['news']['test_chat'] == 'test'
        config.data['news']['test_chat'] = '  '
        config.save()
        assert ConfigStore(config.path).data['news']['test_chat'] == 'test'
        assert config.data['news']['max_age_hours'] == 24
        for value, expected in [('bad', 24), (None, 24), (0, 1), (999, 168), (24, 24)]:
            config.data['news']['max_age_hours'] = value
            config.save()
            assert ConfigStore(config.path).data['news']['max_age_hours'] == expected
        config.data['news'].update(use_model_summary=False, max_age_hours=24)
        a = config.add_target('新闻A', 'group', 'arona')
        b = config.add_target('新闻B', 'group', 'arona')
        for target in (a, b):
            target.update(news_enabled=True, news_time='06:50')
        service = NewsService(config, PersonaStore(Path(tmp)/'personas.json'), state_path=Path(tmp)/'state.json')
        adapter = _RecordingAdapter(config, lambda _: None)
        body = '新版本公告公布了新的关卡机制和参与条件，完成挑战可领取游戏内奖励。' * 6
        fresh = NewsItem('国服', '蔚蓝档案全新关卡机制正式公布', 'https://example.com/new',
                         '国服官网', body, published_at='2026-11-02T07:00:00', body_read=True)
        old = replace(fresh, title='过期公告', url='https://example.com/old', published_at='2026-10-01')
        unknown = replace(fresh, title='无发布时间公告', url='https://example.com/unknown', published_at='')
        future = replace(fresh, title='未来发布时间', url='https://example.com/future', published_at='2026-11-03')
        unread = replace(fresh, title='无法读取正文', url='https://example.com/unread', body_read=False)
        picked = service._filter_seen({'国服': [old, unknown, future, unread, fresh]}, 3, '新闻A', fixed)
        assert picked['国服'] == [fresh]
        with patch('app.news._http_json', return_value={
                'meta': {'ok': True}, 'data': {'rows': [{'id': 123, 'title': 'お知らせ', 'summary':'イベント開催のお知らせ',
                'content': '<p>新しいイベントの参加条件</p>', 'publishTime': '2026-11-02T07:00:00+08:00'}]}}):
            jp = service._official_jp(5)
            assert len(jp) == 1 and jp[0].source == '日服官网' and jp[0].body_read
            assert jp[0].title == 'イベント開催のお知らせ', 'use the actual heading, not the category'
            assert jp[0].published_at == '2026-11-02T07:00:00'
        service._mark_seen(picked, '新闻A', fixed)
        assert not service._filter_seen({'国服': [fresh]}, 3, '新闻A', fixed)['国服']
        assert service._filter_seen({'国服': [fresh]}, 3, '新闻B', fixed)['国服'] == [fresh]
        for repost in (
            replace(fresh, title='转载标题换了', url=fresh.url+'?utm_source=foo#section'),
            replace(fresh, url='https://other.example/new'),
            replace(fresh, title='别的网站搬运文章', url='https://other.example/repost'),
            replace(fresh, title='跨日再发旧文', url='https://other.example/repost2',
                    snippet=body.replace('参与条件', '参加条件'), published_at='2026-11-03T07:00:00'),
        ):
            repost = replace(repost, published_at='2026-11-03T07:00:00')
            assert not service._filter_seen({'国服': [repost]}, 3, '新闻A', fixed+timedelta(days=1))['国服']
        # Migrated legacy history is never forgotten after seven days.
        service._save_state({'seen_titles': {service._item_key(fresh): '2020-01-01'}})
        assert not service._filter_seen({'国服': [fresh]}, 3, '新闻B', fixed)['国服']
        service._save_state({'seen_titles': {}})

        # New sources are consulted even when the official source has results.
        candidate = replace(fresh, url='https://example.com/search', body_read=False, published_at='')
        with patch.object(service, '_official_cn', return_value=[fresh]), \
                patch.object(service, '_bilibili', return_value=[]), \
                patch.object(service, '_search', return_value=[candidate]) as search, \
                patch.object(service, '_rss_search', return_value=[]), \
                patch.object(service, '_publisher_feed', return_value=[]), \
                patch.object(service, '_official_jp', return_value=[]), \
                patch.object(service, '_steam_global', return_value=[]), \
                patch.object(service, '_official_dynamic', return_value=[]), \
                patch.object(service, '_official_list', return_value=[]), \
                patch('app.news.news_now', return_value=fixed), \
                patch('app.news.read_article', return_value=('2020-01-01', 'old article text', (), candidate.title)):
            collected = service.fetch()
            from app.news_sources import SOURCES
            assert search.call_count == 3 + len(SOURCES)
            assert len(SOURCES) >= 25 and len({r.host for r in SOURCES}) == len(SOURCES)
            assert len(service._fetch_report['publishers']) == len(SOURCES)
            # Search/crawl recency never rescues a demonstrably old article.
            assert not service._filter_seen(collected, 3, '新闻A', fixed)['国服']
        # A stale official announcement is not forced back into the digest.
        # Steam Community carries the publisher's announcements independently
        # of a blocked Nexon forum; publication is posttime, never update/start.
        epoch = int(datetime.fromisoformat('2026-11-02T07:00:00+08:00').timestamp())
        # Use an explicit UTC+8 ISO for deterministic fixture comparisons.
        announcement = {'clanid':45643295, 'headline':'Anniversary Login Event',
                        'posttime':epoch, 'updatetime':epoch+10000,
                        'body':'[p]A new login event announcement.[/p][img src="{STEAM_CLAN_IMAGE}/45643295/p.png"][/img]'}
        event = {'appid':3557620,'gid':'123456789','published':True,'announcement_body':announcement}
        with patch('app.news._http_json',return_value={'success':1,'events':[event]}):
            steam = service._steam_global(5)
            assert len(steam)==1 and steam[0].region=='国际服' and steam[0].body_read
            assert steam[0].published_at == published_time(epoch)
            assert 'STEAM_CLAN_IMAGE' not in steam[0].images[0]
            assert not service._video_url(steam[0].url)
        for bad in ({**event,'hidden':True},{**event,'appid':123},
                    {**event,'announcement_body':{**announcement,'clanid':999}}):
            with patch('app.news._http_json',return_value={'success':1,'events':[bad]}):
                assert not service._steam_global(5)
        with patch('app.news._http_json',return_value={'success':1,'events':[
                {**event,'announcement_body':{**announcement,'posttime':epoch-10*86400,'updatetime':epoch}}]}):
            repost=service._steam_global(5)
            assert not service._filter_seen({'国际服':repost},2,'unseen',datetime.fromisoformat(published_time(epoch)))['国际服']
        # Image evidence survives long article text and reaches the model prompt.
        from app.model_client import LLMResult
        illustrated = replace(fresh, snippet='正文内容。'*300, images=('https://example.com/news.png',))
        with patch.object(config,'active_profile',return_value={'provider':'mock','supports_vision':True}), \
                patch('app.news.DATA',Path(tmp)), \
                patch('app.link_reader.fetch_public',return_value=(b'x'*2048,'utf-8')), \
                patch('app.model_client.ModelClient') as vision:
            vision.return_value.describe_image.return_value=LLMResult('图片确认：本次奖励为青辉石。','fixture','mock',1)
            service._enrich_images({'国服':[illustrated]})
        assert '[图片内容]' in illustrated.snippet
        with patch('app.news.make_client') as model:
            model.return_value.chat.return_value=LLMResult('素材测试','fixture','mock',1)
            service._model_message({'国服':[illustrated],'日服':[],'国际服':[]},fixed)
            assert '本次奖励为青辉石' in model.return_value.chat.call_args.args[0][0]['content']
        uid=3493265644980448
        dynamic={'type':'DYNAMIC_TYPE_DRAW','id_str':'12345678','modules':{
            'module_author':{'mid':uid,'pub_ts':epoch},
            'module_dynamic':{'major':{'opus':{'title':'新活动公告',
                'summary':{'text':'蔚蓝档案公布了新活动的参与条件。'},'pics':[{'url':'https://example.com/a.png'}]}}}}}
        with patch('app.news._http_json',return_value={'code':0,'data':{'items':[dynamic]}}):
            posts=service._official_dynamic(uid,5)
            assert len(posts)==1 and posts[0].body_read and posts[0].published_at==published_time(epoch)
            assert not service._video_url(posts[0].url)
        with patch('app.news._http_json',return_value={'code':0,'data':{'items':[{**dynamic,'type':'DYNAMIC_TYPE_AV'}]}}):
            assert not service._official_dynamic(uid,5), 'news never fetches video posts'
        with patch.object(service,'_official_cn',return_value=[]), \
                patch.object(service,'_official_jp',return_value=[]), \
                patch.object(service,'_official_list',side_effect=OSError('403')), \
                patch.object(service,'_steam_global',return_value=steam), \
                patch.object(service,'_official_dynamic',return_value=[]), \
                patch.object(service,'_bilibili',return_value=[]), \
                patch.object(service,'_search',return_value=[]), \
                patch.object(service,'_rss_search',return_value=[]), \
                patch('app.news.news_now',return_value=datetime.fromisoformat(published_time(epoch))+timedelta(hours=1)):
            assert service.fetch()['国际服'], 'one blocked source must not erase a working channel'
        service.fetch = lambda: {'国服': [old], '日服': [], '国际服': []}
        notice = service.run(adapter, target=a, now=fixed)
        assert notice['sent'] and notice['no_new_items'] and '旧闻' in notice['message']
        assert '阿洛娜' in notice['message'] and '过期公告' not in notice['message']
        assert not service.run(adapter, target=a, now=fixed)['sent'], 'empty report also consumes its daily slot'
        # Different groups can receive the same new digest; no group starves another.
        service.fetch = lambda: {'国服': [fresh], '日服': [], '国际服': []}
        assert service.run(adapter, target=b, now=fixed)['sent']
        failed_date = fixed + timedelta(days=1)
        newer = replace(fresh, title='蔚蓝档案角色养成系统调整', url='https://example.com/newer',
                        snippet='新增养成系统开放，资源消耗和角色能力上限随新版本一起调整。',
                        published_at='2026-11-03T07:00:00')
        service.fetch = lambda: {'国服': [newer], '日服': [], '国际服': []}
        with patch.object(adapter, 'send_text', return_value=SendResult(False, 'offline')):
            assert not service.run(adapter, target=a, now=failed_date, force=True)['sent']
        assert service._filter_seen({'国服': [newer]}, 3, a['name'], failed_date)['国服']
        assert service.run(adapter, target=a, now=failed_date, force=True)['sent']
        assert not service._filter_seen({'国服': [newer]}, 3, a['name'], failed_date)['国服']
        # Network/model failures get honest, in-character fallback text.
        config.data['active_persona_id'] = 'plana'
        service._source_errors = ['日服官网']
        text, _ = service.build_message({'国服': [], '日服': [], '国际服': []}, fixed)
        assert '普拉娜' in text and '阿洛娜' not in text and '暂时无法访问' in text
        config.data['active_persona_id'] = 'arona'
        config.data['news']['use_model_summary'] = True
        with patch('app.news.make_client', side_effect=RuntimeError('model offline')):
            text, _ = service.build_message({'国服': [fresh], '日服': [], '国际服': []}, fixed)
            assert '阿洛娜' in text and '2026-11-02' in text and fresh.url in text
        with patch.object(service, 'fetch', side_effect=OSError('network offline')), \
                patch('app.news.make_client') as model:
            notice = service.run(adapter, target=a, now=failed_date+timedelta(days=1), force=True)
            assert notice['sent'] and notice['no_new_items']
            assert '阿洛娜' in notice['message'] and '暂时打不开' in notice['message']
            model.assert_not_called()

    print("KIVOTOS NEWS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
