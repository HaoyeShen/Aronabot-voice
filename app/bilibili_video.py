"""Public Bilibili video discovery and page-checked links for idle sharing."""
from __future__ import annotations

import html
import gzip
import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from .paths import DATA

log = logging.getLogger(__name__)
BV = re.compile(r"BV[1-9A-HJ-NP-Za-km-z]{10}")
SHORT = re.compile(r"https://b23\.tv/[A-Za-z0-9]{5,8}")
PAGE = re.compile(r'https://www\.bilibili\.com/(?:zh-Hans/)?video/(BV[1-9A-HJ-NP-Za-km-z]{10})/')
DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'


def valid_video_link(url: str, bvid: str = '') -> bool:
    """Complete official URLs are exempt from the ordinary chat length limit.

    Legacy short links remain readable for stored records; new discovery uses
    page-checked full URLs without tracking parameters.
    """
    match = PAGE.fullmatch(url)
    return bool((match and (not bvid or match.group(1) == bvid)) or SHORT.fullmatch(url))


class VideoAccessError(ValueError):
    def __init__(self, risk_control: bool = False):
        super().__init__('视频页面触发访问限制' if risk_control else '视频页面暂时无法核实')
        self.risk_control = risk_control


def _probe_video_page(url: str, bvid: str, user_agent: str) -> None:
    class SameVideoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # Never follow a share link to a login page, another video or host.
            clean = urllib.parse.urlsplit(newurl)._replace(query='', fragment='').geturl()
            if not valid_video_link(clean, bvid) or not PAGE.fullmatch(clean):
                raise VideoAccessError()
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    request = urllib.request.Request(url, headers={
        'User-Agent': user_agent, 'Accept': 'text/html', 'Accept-Encoding': 'identity',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameVideoRedirect())
    try:
        with opener.open(request, timeout=6) as response:
            final = urllib.parse.urlsplit(response.geturl())._replace(query='', fragment='').geturl()
            if not PAGE.fullmatch(final) or not valid_video_link(final, bvid):
                raise VideoAccessError()
            # Some public responses are compressed even when identity is requested.
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding', '').lower() == 'gzip' else response
            body = stream.read(600_001).decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raise VideoAccessError(exc.code in (403, 412, 429)) from exc
    except (urllib.error.URLError, OSError, EOFError) as exc:
        raise VideoAccessError() from exc
    lowered = body.lower()
    title = re.search(r'<title[^>]*>(.*?)</title>', body, re.S | re.I)
    risk = ('security control policy' in lowered or '安全风控策略' in body
            or bool(title and re.search(r'(?:错误号|错误码|error)[^<]{0,10}412|访问受限|安全验证|验证码',
                                        title.group(1), re.I)))
    if risk:
        raise VideoAccessError(True)
    if (not title or bvid not in body or
            not ('__INITIAL_STATE__' in body or 'videoData' in body)):
        raise VideoAccessError()


def _verified_video_link(video: 'Video') -> str:
    if not BV.fullmatch(video.bvid):
        raise VideoAccessError()
    url = f'https://www.bilibili.com/zh-Hans/video/{video.bvid}/'
    # Check the actual HTML, not just a short-link redirect. These anonymous
    # requests cannot guarantee accessibility on the recipient's own network.
    for ua in (DESKTOP_UA, DESKTOP_UA + ' MicroMessenger/4.1.0 WindowsWechat/WMPF'):
        _probe_video_page(url, video.bvid, ua)
    return url


def _api_json(path: str, params: dict, *, post: bool = False) -> dict:
    encoded = urllib.parse.urlencode(params).encode()
    url = 'https://api.bilibili.com/' + path
    if not post:
        url += '?' + encoded.decode()
    request = urllib.request.Request(url, data=encoded if post else None, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
        'Referer': 'https://search.bilibili.com/', 'Accept': 'application/json',
        'Cookie': 'buvid3=' + str(uuid.uuid4()).upper() + 'infoc',
    })
    # Anonymous public access only. Do not inherit login cookies or proxy credentials.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=6) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('视频接口响应过大')
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict) or data.get('code') != 0:
        raise ValueError('视频接口暂时不可用')
    return data.get('data') or {}


def _text(value) -> str:
    return html.unescape(re.sub(r'<[^>]*>', '', str(value or ''))).strip()


def _topic_evidence(text: str) -> str:
    text = _text(text).casefold()
    # Recognise common aliases without matching short ASCII words inside
    # unrelated words (e.g. AI must not match "daily"). Other topics still use
    # generic words/Chinese bigrams; this is not a game-only allowlist.
    aliases = {
        '蔚蓝档案': ('碧蓝档案', 'blue archive', 'bluearchive', 'ブルーアーカイブ'),
        '人工智能': ('artificial intelligence', 'openai', 'chatgpt', 'aigc', 'ai', '机器学习', '深度学习', '大模型'),
        '计算机': ('computer science', 'computing', 'computer', '计算机科学', '软件开发', '编程'),
        '明日方舟': ('arknights',),
    }
    for canonical, values in aliases.items():
        for value in values:
            pattern = re.escape(value)
            if value.isascii():
                pattern = r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])'
            text = re.sub(pattern, canonical, text)
    return text


SUGGESTIVE = re.compile(
    r'擦边|福利姬|走光|露点|爆乳|巨乳|乳摇|胸摇|臀摇|抖胸|扭臀|情趣|脱衣|裸舞|半裸|'
    r'内衣|黑丝|白丝|丝袜|足控|腿控|福利图|里番|黄油|h油|18禁|18\+|'
    r'(?<![a-z0-9])(?:nsfw|r[- ]?18)(?![a-z0-9])', re.I)
GAME_TOPICS = {'蔚蓝档案', '明日方舟', '原神', '崩坏星穹铁道', '绝区零', '鸣潮'}
REAL_PERSON = re.compile(
    r'(?<![a-z])cos(?:play|er)?(?![a-z])|真人|舞蹈|跳舞|热舞|宅舞|颜值|仿妆|变装|写真|穿搭|女仆装', re.I)


def _topic_terms(topic: str) -> list[str]:
    query = _topic_evidence(topic).strip()
    query = re.sub(r'^(?:关于|有关)', '', query)
    query = re.sub(r'(.+?)(?:相关)?(?:视频|主题|推荐)$', r'\1', query)
    terms = []
    # Match entire concepts, not overlapping fragments of an unrelated title.
    # Keep compound topics useful: AI or computing both fit 人工智能与计算机.
    for part in re.split(r'以及|或者|与|和|及|[,，;；、\n]', query):
        part = part.strip()
        if part.endswith('游戏') and part[:-2] in GAME_TOPICS:
            part = part[:-2]
        if len(part) >= 2 and part not in terms:
            terms.append(part)
    return terms


def _content_text(value) -> str:
    text = _text(value)
    # Hashtag piles and URLs are not descriptions of the video's subject.
    text = re.sub(r'https?://\S+|#[^#\n]{1,80}#|#[^\s#，,]+', ' ', text)
    return re.sub(r'[\u200b-\u200f\ufeff]', '', text)


def _has_topic(text: str, term: str) -> bool:
    pattern = re.escape(term)
    if term[0].isascii() and term[0].isalnum():
        pattern = r'(?<![a-z0-9])' + pattern
    if term[-1].isascii() and term[-1].isalnum():
        pattern += r'(?![a-z0-9])'
    return bool(re.search(pattern, _topic_evidence(text)))


def _rejection_reason(topic: str, row: dict) -> str:
    metadata = _text(' '.join(str(row.get(k) or '') for k in
                             ('title', 'description', 'desc', 'tag', 'typename', 'tname', 'tname_v2')))
    if SUGGESTIVE.search(metadata):
        return 'content_filtered'
    terms = _topic_terms(topic)
    if any(term in GAME_TOPICS for term in terms) and REAL_PERSON.search(metadata):
        return 'content_filtered'
    title = _content_text(row.get('title'))
    if any(_has_topic(title, term) for term in terms):
        return ''
    for key in ('description', 'desc'):
        description = _content_text(row.get(key))
        # A meaningful narrative may establish relevance when a title is vague;
        # a game tag or a bare list of keywords cannot do so on its own.
        if (len(description) >= 24 and re.search(r'[。！？!?，：:.]', description)
                and any(_has_topic(description, term) for term in terms)):
            return ''
    return 'topic_rejected'


def _related(topic: str, row: dict) -> bool:
    return not _rejection_reason(topic, row)


@dataclass(frozen=True)
class Video:
    bvid: str
    aid: int
    title: str
    topic: str
    published_at: int
    url: str = ''
    description: str = ''
    transcript: str = ''
    cover_url: str = ''
    cover_description: str = ''


def _subtitle_text(bvid: str, cid) -> str:
    """Read public subtitle text, when supplied by the video platform."""
    if not cid:
        return ''
    try:
        player = _api_json('x/player/v2', {'bvid': bvid, 'cid': int(cid)})
        tracks = (player.get('subtitle') or {}).get('subtitles') or []
        if not tracks:
            return ''
        tracks = sorted(tracks, key=lambda row: not str(row.get('lan') or '').startswith('zh'))
        raw_url = str(tracks[0].get('subtitle_url') or '')
        if raw_url.startswith('//'):
            raw_url = 'https:' + raw_url
        def allowed(url):
            parsed = urllib.parse.urlsplit(url)
            return (parsed.scheme == 'https' and parsed.hostname is not None
                    and (parsed.hostname == 'hdslb.com' or parsed.hostname.endswith('.hdslb.com'))
                    and not parsed.username and not parsed.password and parsed.port in (None, 443))
        if not allowed(raw_url):
            return ''
        class SubtitleRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not allowed(newurl):
                    raise ValueError('字幕地址不可信')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        req = urllib.request.Request(raw_url, headers={'User-Agent': DESKTOP_UA})
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), SubtitleRedirect()).open(req, timeout=6) as response:
            raw = response.read(500001)
        if len(raw) > 500000:
            return ''
        data = json.loads(raw.decode('utf-8'))
        return ' '.join(_text(row.get('content')) for row in data.get('body', []) if isinstance(row, dict))[:5000]
    except Exception as exc:
        log.debug('视频字幕不可用：%s', type(exc).__name__)
        return ''


def _verified_short_link(video: Video) -> str:
    data = _api_json('x/share/click', {
        'build': '7790300', 'buvid': str(uuid.uuid4()).upper(), 'oid': str(video.aid),
        'platform': 'android', 'share_channel': 'COPY',
        'share_id': 'main.ugc-video-detail.0.0.pv', 'share_mode': '3',
    }, post=True)
    match = SHORT.search(str(data.get('link') or data.get('content') or ''))
    if not match:
        return ''
    url = match.group()
    # Do not accept a truncated match from a longer URL or a lookalike host.
    source = str(data.get('link') or data.get('content') or '')
    suffix = source[match.end():match.end()+1]
    if suffix and suffix not in ' \r\n\t。】)':
        return ''

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=6):
            return ''
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            return ''
        destination = urllib.parse.urlsplit(exc.headers.get('Location', ''))
    if (destination.scheme != 'https' or destination.username or destination.password
            or destination.port not in (None, 443)
            or destination.hostname not in ('www.bilibili.com', 'm.bilibili.com', 'bilibili.com')):
        return ''
    expected = (f'/video/{video.bvid}', f'/video/av{video.aid}')
    return url if destination.path.rstrip('/') in expected and len(url) <= 23 else ''


class BilibiliVideoStore:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path or DATA / 'idle_video_state.json'
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._sent: dict[str, set[str]] = {}
        self.last_report: dict = {}
        try:
            saved = json.loads(self.state_path.read_text(encoding='utf-8'))
            self._sent = {chat: {v for v in values if isinstance(v, str) and BV.fullmatch(v)}
                          for chat, values in saved.items() if isinstance(values, list)}
        except (OSError, ValueError, AttributeError):
            pass

    def pick(self, topics: list[str], chat: str, *, filter_topics: list[str] | None = None) -> Video | None:
        """No sending or history writes; the caller decides how to handle no results."""
        topics = list(dict.fromkeys(s.strip() for s in topics if isinstance(s, str) and s.strip())) or ['蔚蓝档案']
        # Try another configured topic if one has no usable candidates. Keep
        # network work bounded and do not hold the store lock while requesting.
        report = {'results': 0, 'related': 0, 'recent': 0, 'seen': 0, 'api_errors': 0,
                  'detail_errors': 0, 'link_errors': 0, 'risk_blocked': 0,
                  'content_filtered': 0, 'topic_rejected': 0, 'topics': topics}
        deadline = time.monotonic() + 20
        ordered = random.sample(topics, len(topics))
        for topic in ordered:
            if time.monotonic() >= deadline:
                break
            video = self._pick_topic(topic, chat, report, deadline, filter_topics)
            if video:
                with self._lock:
                    self.last_report = report
                return video
        with self._lock:
            self.last_report = report
        return None

    def _pick_topic(self, topic, chat, report, deadline, filter_topics=None) -> Video | None:
        try:
            with self._lock:
                cached_at, rows = self._cache.get(topic, (0.0, []))
                refresh = time.monotonic() - cached_at >= (900 if rows else 15) or topic not in self._cache
                seen = self._sent.get(chat, set()).copy()
            if refresh:
                result = _api_json('x/web-interface/search/type', {
                    'search_type': 'video', 'keyword': topic, 'order': 'pubdate',
                    'page': 1, 'page_size': 20,
                })
                rows = result.get('result') or []
                with self._lock:
                    self._cache[topic] = (time.monotonic(), rows)
            report['results'] += len(rows)
            attempts = 0
            for row in rows:
                if time.monotonic() >= deadline:
                    break
                bvid = str(row.get('bvid') or '')
                published = int(row.get('pubdate') or 0)
                if not BV.fullmatch(bvid):
                    continue
                candidates = filter_topics or [topic]
                matched_topic = next((value for value in candidates if _related(value, row)), None)
                rejection = '' if matched_topic else _rejection_reason(candidates[0], row)
                if rejection:
                    report[rejection] += 1
                    continue
                report['related'] += 1
                if not 0 <= time.time() - published <= 7 * 86400 or row.get('is_pay') or row.get('is_charge_video'):
                    continue
                report['recent'] += 1
                if bvid in seen:
                    report['seen'] += 1
                    continue
                attempts += 1
                if attempts > 3:
                    break
                try:
                    detail = _api_json('x/web-interface/view', {'bvid': bvid})
                    if (detail.get('bvid') != bvid or int(detail.get('state', 0)) != 0
                            or (detail.get('rights') or {}).get('pay')
                            or detail.get('is_upower_exclusive')):
                        continue
                    published = int(detail.get('pubdate') or 0)
                    title = _text(detail.get('title'))
                    if not title or not 0 <= time.time() - published <= 7 * 86400:
                        continue
                    # Search metadata is only a first pass. Do not trust a
                    # search title/tag after the actual details say otherwise.
                    checked_detail = {**detail,
                                      'tag': str(detail.get('tag') or '') + ' ' + str(row.get('tag') or ''),
                                      'typename': str(detail.get('typename') or '') + ' ' + str(row.get('typename') or '')}
                    rejection = _rejection_reason(matched_topic, checked_detail)
                    if rejection:
                        report[rejection] += 1
                        continue
                    video = Video(bvid, int(detail['aid']), title, matched_topic, published)
                    url = _verified_video_link(video)
                    if url:
                        return Video(bvid, video.aid, title, matched_topic, published, url,
                                     _text(detail.get('desc'))[:800], _subtitle_text(bvid, detail.get('cid')),
                                     str(detail.get('pic') or ''))
                    report['link_errors'] += 1
                except VideoAccessError as exc:
                    report['link_errors'] += 1
                    report['risk_blocked'] += int(exc.risk_control)
                    log.debug('视频页面无法核实：%s', exc)
                except Exception as exc:
                    report['detail_errors'] += 1
                    log.debug('视频资料或分享链接无法核实：%s', exc)
            return None
        except Exception as exc:
            report['api_errors'] += 1
            log.warning('哔哩哔哩视频检索失败：%s', exc)
            return None

    def miss_message(self) -> str:
        with self._lock:
            report = dict(self.last_report)
        if report.get('api_errors') and not report.get('results'):
            return '哔哩哔哩检索接口暂时不可用，请稍后重试。'
        if report.get('risk_blocked'):
            return '已找到主题视频，但哔哩哔哩页面触发 403/412 等访问限制；已跳过，本次未发送。'
        if report.get('link_errors'):
            return '已找到主题视频，但完整分享页面暂时无法核实，本次未发送。'
        if report.get('detail_errors'):
            return '已找到候选视频，但详情暂时无法读取或核实，请稍后重试。'
        if report.get('content_filtered') or report.get('topic_rejected'):
            return '候选视频缺少明确主题证据或不适合暖场，已跳过；本次未找到合适视频。'
        if report.get('recent') and report.get('seen') == report.get('recent'):
            return '当前符合主题的候选视频，本群都已经推送过了；其他群的记录互不影响。'
        if report.get('results') and not report.get('related'):
            return '搜索结果与本群主题不够相关，请调整主题关键词后重试。'
        return '暂时没有找到近 7 天、符合本群主题且未推送过的公开视频，请稍后重试。'

    def mark_sent(self, chat: str, video: Video) -> None:
        with self._lock:
            self._sent.setdefault(chat, set()).add(video.bvid)
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.state_path.with_suffix('.tmp')
                temp.write_text(json.dumps({key: sorted(ids) for key, ids in self._sent.items()},
                                           ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(self.state_path)
            except OSError as exc:
                log.warning('视频去重记录写入失败，当前进程仍保留记录：%s', exc)
