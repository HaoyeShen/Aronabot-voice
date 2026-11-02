"""Offline checks for complete video URLs and Bilibili access-denied pages."""
import gzip
import io
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.bilibili_video as videos


BV = 'BV1btH36uEru'
URL = f'https://www.bilibili.com/zh-Hans/video/{BV}/'
HTML = f'<html><title>主题视频_哔哩哔哩</title><script>__INITIAL_STATE__={{"videoData":{{"bvid":"{BV}"}}}}</script></html>'


class Response(io.BytesIO):
    def __init__(self, text=HTML, url=URL, compressed=False):
        super().__init__(gzip.compress(text.encode()) if compressed else text.encode())
        self.headers = {'Content-Encoding': 'gzip'} if compressed else {}
        self.url = url

    def geturl(self):
        return self.url


def main():
    subtitle = b'{"body":[{"content":"first line"},{"content":"second line"}]}'
    with patch.object(videos, '_api_json', return_value={'subtitle': {'subtitles': [
            {'lan':'zh-CN', 'subtitle_url':'//aisubtitle.hdslb.com/test.json'}]}}), \
            patch.object(videos.urllib.request, 'build_opener') as opener:
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = subtitle
        assert videos._subtitle_text(BV, 1) == 'first line second line'
    with patch.object(videos, '_api_json', return_value={'subtitle': {'subtitles': [
            {'subtitle_url':'https://evil.example/test.json'}]}}), \
            patch.object(videos.urllib.request, 'build_opener') as opener:
        assert videos._subtitle_text(BV, 1) == ''
        opener.assert_not_called()
    # Regression: this real example carried a game tag but no game evidence in
    # its title/description. Search tags alone must never qualify a candidate.
    unrelated = {'bvid': 'BV1Q54P6YEGv', 'title': '就不p，就原视频', 'desc': '-',
                 'tag': '可爱,萝莉,cos,蔚蓝档案,萌妹,蔚蓝档案？蔚蓝档案启动！'}
    assert not videos._related('蔚蓝档案', unrelated)
    assert not videos._related('蔚蓝档案', {**unrelated, 'tag': '蔚蓝档案'})
    for row in ({'title': '今日生活', 'description': '#蔚蓝档案# #游戏# #二次元#'},
                {'title': '蔚蓝的天空和人事档案'},
                {'title': '【蔚蓝档案】真人cos展示'},
                {'title': '【蔚蓝档案】H油哦'},
                {'title': '蔚蓝档案角色黑丝写真'},
                {'title': '蔚蓝档案游戏攻略', 'tag': 'NSFW'},
                {'title': '猫咪日常', 'tag': '人工智能'}):
        assert not videos._related('蔚蓝档案', row), row
    for topic, row in (
        ('蔚蓝档案', {'title': '蔚蓝档案 总力战攻略'}),
        ('蔚蓝档案游戏', {'title': 'Blue Archive 新活动剧情'}),
        ('蔚蓝档案', {'title': '新人怎么打', 'desc': '今天整理了一份蔚蓝档案新手攻略，介绍队伍搭配、技能和副本的打法。'}),
        ('人工智能与计算机', {'title': 'AI agent 技术介绍'}),
        ('AI和人工智能', {'title': '计算机与 AI 介绍'}),
        ('Python', {'title': 'Python 装饰器教程'}),
        ('明日方舟', {'title': 'Arknights 活动通关'}),
        ('猫咪', {'title': '猫咪散步日常'})):
        assert videos._related(topic, row), (topic, row)
    assert not videos._related('AI', {'title': 'daily holiday'})
    video = videos.Video(BV, 123, '主题视频', '主题', int(time.time())-30)
    assert videos.valid_video_link(URL, BV) and len(URL) > 23
    for invalid in (URL+'?trackid=123', URL+'#fragment', URL.replace('www.', 'evil.'),
                    URL.replace(BV, 'BV1hb4c64EBb'), URL.replace('https:', 'http:')):
        assert not videos.valid_video_link(invalid, BV)
    opener = Mock()
    opener.open.side_effect = [Response(compressed=True), Response()]
    with patch.object(videos.urllib.request, 'build_opener', return_value=opener):
        assert videos._verified_video_link(video) == URL
    assert opener.open.call_count == 2
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert all(req.full_url == URL and req.get_method() == 'GET' for req in requests)
    assert 'MicroMessenger' in requests[1].get_header('User-agent')

    errors = [urllib.error.HTTPError(URL, 412, 'blocked', {}, None),
              Response('<title>错误号：412</title><p>security control policy</p>'),
              Response('<title>安全验证</title>'),
              Response('<title>登录</title>'),
              Response(url='https://evil.example/'),
              Response(HTML.replace(BV, 'BV1hb4c64EBb'))]
    for index, error in enumerate(errors):
        opener = Mock()
        if isinstance(error, Exception):
            opener.open.side_effect = error
        else:
            opener.open.return_value = error
        with patch.object(videos.urllib.request, 'build_opener', return_value=opener):
            try:
                videos._verified_video_link(video)
            except videos.VideoAccessError as exc:
                if index < 3:
                    assert exc.risk_control
            else:
                raise AssertionError('unverified/error page accepted')
    # A normal browser check alone is insufficient if the WeChat check fails.
    opener.open.side_effect = [Response(), urllib.error.HTTPError(URL, 412, 'blocked', {}, None)]
    with patch.object(videos.urllib.request, 'build_opener', return_value=opener):
        try:
            videos._verified_video_link(video)
        except videos.VideoAccessError as exc:
            assert exc.risk_control
        else:
            raise AssertionError('WeChat access restriction ignored')
    # Reject redirects before making any request to another host/video.
    with patch.object(videos.urllib.request, 'build_opener', return_value=Mock(open=Mock(return_value=Response()))) as factory:
        videos._probe_video_page(URL, BV, videos.DESKTOP_UA)
        redirect = factory.call_args.args[1]
        for destination in ('https://evil.example/', 'https://www.bilibili.com/login',
                            URL.replace(BV, 'BV1hb4c64EBb')):
            try:
                redirect.redirect_request(requests[0], None, 302, 'redirect', {}, destination)
            except videos.VideoAccessError:
                pass
            else:
                raise AssertionError('unsafe redirect followed')
    # Access denial must neither masquerade as no matching topic nor consume dedup.
    row = {'bvid': BV, 'title': '主题视频', 'pubdate': video.published_at}
    def api(path, params, **kwargs):
        return {'result': [row]} if path.endswith('/type') else {**row, 'aid': 123, 'state': 0}
    with tempfile.TemporaryDirectory() as tmp:
        store = videos.BilibiliVideoStore(Path(tmp)/'state.json')
        with patch.object(videos, '_api_json', side_effect=api), \
                patch.object(videos, '_verified_video_link', side_effect=videos.VideoAccessError(True)):
            assert store.pick(['主题'], 'fixture-group') is None
            assert '412' in store.miss_message()
            assert not store.state_path.exists() and not store._sent
        # A convincing search title cannot override unrelated actual details;
        # skip it and continue to a genuinely relevant second candidate.
        bad_search = {**row, 'bvid': unrelated['bvid'], 'title': '蔚蓝档案游戏攻略'}
        good_search = {**row, 'title': '蔚蓝档案总力战攻略'}
        def review_api(path, params, **kwargs):
            if path.endswith('/type'):
                return {'result': [bad_search, good_search]}
            candidate = unrelated if params['bvid'] == unrelated['bvid'] else good_search
            return {**candidate, 'aid': 123, 'state': 0, 'pubdate': video.published_at}
        store = videos.BilibiliVideoStore(Path(tmp)/'review.json')
        with patch.object(videos, '_api_json', side_effect=review_api), \
                patch.object(videos, '_verified_video_link', return_value=URL) as verify:
            result = store.pick(['蔚蓝档案'], 'fixture-group')
            assert result and result.bvid == BV
            assert verify.call_count == 1 and verify.call_args.args[0].bvid == BV
            assert store.last_report['content_filtered'] == 1
            assert not store.state_path.exists()
        # The model may add context-specific search words; relevance remains
        # anchored to the configured theme rather than the full search phrase.
        with patch.object(videos, '_api_json', side_effect=review_api), \
                patch.object(videos, '_verified_video_link', return_value=URL):
            chosen = videos.BilibiliVideoStore(Path(tmp)/'query.json').pick(
                ['蔚蓝档案 最新活动'], 'fixture-group', filter_topics=['蔚蓝档案'])
            assert chosen and chosen.bvid == BV and chosen.topic == '蔚蓝档案'
        # This exact complaint must never reach page preflight or sending.
        with patch.object(videos, '_api_json', return_value={'result': [{**unrelated, 'pubdate': video.published_at}]}), \
                patch.object(videos, '_verified_video_link') as verify:
            blocked = videos.BilibiliVideoStore(Path(tmp)/'blocked.json')
            assert blocked.pick(['蔚蓝档案'], 'fixture-group') is None
            assert '不适合暖场' in blocked.miss_message()
            verify.assert_not_called()
    print('VIDEO ACCESS TEST PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
