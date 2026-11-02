"""Public Bilibili video discovery and verified short links for idle sharing."""
from __future__ import annotations

import html
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


def _related(topic: str, row: dict) -> bool:
    evidence = re.sub(r'\s+', '', _text(' '.join(str(row.get(k) or '') for k in
                                                ('title', 'description', 'desc', 'tag')))).casefold()
    if topic.casefold() in ('蔚蓝档案', '碧蓝档案', 'blue archive', 'ブルーアーカイブ'):
        return any(word in evidence for word in ('蔚蓝档案', '碧蓝档案', 'bluearchive', 'ブルーアーカイブ'))
    return all(re.sub(r'\s+', '', part).casefold() in evidence for part in topic.split())


@dataclass(frozen=True)
class Video:
    bvid: str
    aid: int
    title: str
    topic: str
    published_at: int
    url: str = ''
    description: str = ''


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
        try:
            saved = json.loads(self.state_path.read_text(encoding='utf-8'))
            self._sent = {chat: {v for v in values if isinstance(v, str) and BV.fullmatch(v)}
                          for chat, values in saved.items() if isinstance(values, list)}
        except (OSError, ValueError, AttributeError):
            pass

    def pick(self, topics: list[str], chat: str) -> Video | None:
        """No sending or history writes; failure falls back to a normal greeting."""
        topic = random.choice(topics or ['蔚蓝档案'])
        try:
            with self._lock:
                cached_at, rows = self._cache.get(topic, (0.0, []))
                if time.monotonic() - cached_at >= 900 or topic not in self._cache:
                    result = _api_json('x/web-interface/search/type', {
                        'search_type': 'video', 'keyword': topic, 'order': 'pubdate',
                        'page': 1, 'page_size': 20,
                    })
                    rows = result.get('result') or []
                    self._cache[topic] = (time.monotonic(), rows)
                seen = self._sent.get(chat, set()).copy()
            attempts = 0
            for row in rows:
                bvid = str(row.get('bvid') or '')
                published = int(row.get('pubdate') or 0)
                if (not BV.fullmatch(bvid) or bvid in seen or not _related(topic, row)
                        or not 0 <= time.time() - published <= 7 * 86400
                        or row.get('is_pay') or row.get('is_charge_video')):
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
                    video = Video(bvid, int(detail['aid']), title, topic, published)
                    url = _verified_short_link(video)
                    if url:
                        return Video(bvid, video.aid, title, topic, published, url,
                                     _text(detail.get('desc'))[:800])
                except Exception as exc:
                    log.debug('视频资料或分享链接无法核实：%s', exc)
            return None
        except Exception as exc:
            log.warning('哔哩哔哩视频检索失败，回退简单问候：%s', exc)
            return None

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
