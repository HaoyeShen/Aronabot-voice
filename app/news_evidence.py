"""Publication evidence and article fingerprints for daily news (no model inference)."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

SHANGHAI = timezone(timedelta(hours=8))


def news_now() -> datetime:
    return datetime.now(SHANGHAI).replace(tzinfo=None)


def published_time(value) -> str:
    """Normalise explicit publication metadata, never an event date in the prose."""
    if value is None or value == '':
        return ''
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r'\d{10,13}', str(value)):
            stamp = float(value)
            if stamp > 1e11:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, SHANGHAI).replace(tzinfo=None).isoformat(timespec='seconds')
        text = str(value).strip()
        text = re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})日', r'\1-\2-\3', text)
        text = re.sub(r'^(\d{4})[./](\d{1,2})[./](\d{1,2})', r'\1-\2-\3', text)
        if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', text):
            year, month, day = map(int, text.split('-'))
            return datetime(year, month, day).date().isoformat()
        try:
            result = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            result = parsedate_to_datetime(text)
        if result.tzinfo:
            result = result.astimezone(SHANGHAI).replace(tzinfo=None)
        return result.isoformat(timespec='seconds')
    except (ValueError, TypeError, OverflowError, OSError):
        return ''


def is_recent(value: str, now: datetime, hours: int = 24) -> bool:
    stamp = published_time(value)
    if not stamp:
        return False
    # A date-only article from yesterday cannot prove it is within 24 hours.
    if len(stamp) == 10 and hours <= 24:
        return stamp == now.date().isoformat()
    published = datetime.fromisoformat(stamp)
    return now - timedelta(hours=hours) <= published <= now


def canonical_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not k.lower().startswith(('utm_', 'spm', 'from', 'share', 'fbclid', 'gclid'))]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                   parts.path.rstrip('/'), urllib.parse.urlencode(sorted(query)), ''))


def normal_text(value: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unescape(value).lower())


def fingerprints(item) -> list[str]:
    values = [('url', canonical_url(item.url)), ('title', item.region + ':' + normal_text(item.title))]
    body = normal_text(item.snippet)
    if len(body) >= 60:
        values.append(('body', item.region + ':' + body))
    return [kind + ':' + hashlib.sha256(text.encode()).hexdigest() for kind, text in values if text]


class ArticleEvidence(HTMLParser):
    """Collect article text, images and *publication* metadata, not modified dates."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.dates = []
        self.parts = []
        self.main_parts = []
        self.images = []
        self.links = []
        self.hidden = 0
        self.main = 0
        self.ld = None
        self.title = ''
        self.heading = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript'):
            self.hidden += 1
        if tag in ('article', 'main'):
            self.main += 1
        if tag == 'h1':
            self.heading = True
        if tag == 'script' and a.get('type') == 'application/ld+json':
            self.ld = []
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if key in ('article:published_time', 'og:published_time', 'datepublished', 'pubdate',
                       'publishdate', 'publish_time', 'publication_date', 'date'):
                self.dates.append(a.get('content', ''))
            if key == 'og:title':
                self.title = a.get('content', '')
        if tag == 'time' and ('pubdate' in a or a.get('itemprop', '').lower() == 'datepublished'):
            if a.get('datetime'):
                self.dates.append(a['datetime'])
        if tag == 'img' and a.get('src'):
            self.images.append(a['src'])
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])

    def handle_data(self, data):
        if self.heading and not self.title:
            self.title = data.strip()
        if self.ld is not None:
            self.ld.append(data)
        if not self.hidden and data.strip():
            self.parts.append(data.strip())
            if self.main:
                self.main_parts.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.heading = False
        if tag == 'script' and self.ld is not None:
            try:
                self._json_dates(json.loads(''.join(self.ld)))
            except (ValueError, TypeError, RecursionError):
                pass
            self.ld = None
        if tag in ('script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('article', 'main'):
            self.main = max(0, self.main - 1)

    def _json_dates(self, obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, list):
            for item in obj:
                self._json_dates(item, depth + 1)
        elif isinstance(obj, dict):
            kind = str(obj.get('@type', '')).lower()
            if any(word in kind for word in ('article', 'posting')) and obj.get('datePublished'):
                self.dates.append(obj['datePublished'])
            if '@graph' in obj:
                self._json_dates(obj['@graph'], depth + 1)

    def evidence(self):
        stamps = [published_time(value) for value in self.dates]
        valid = [value for value in stamps if value]
        # Conflicting publication metadata is resolved conservatively to the oldest.
        stamp = min(valid) if valid else ''
        body = ' '.join(self.main_parts or self.parts)
        if not stamp:
            match = re.search(r'(?:发布时间|发布日期|发表于|Published(?: on)?)\s*[:：]?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)', body, re.I)
            if match:
                stamp = published_time(match.group(1))
        return stamp, body[:2400]


def read_article(url: str, timeout: float = 10):
    from .link_reader import fetch_public
    raw, encoding = fetch_public(url)
    parser = ArticleEvidence()
    parser.feed(raw.decode(encoding, 'replace'))
    parser.close()
    stamp, body = parser.evidence()
    return stamp, body, tuple(urllib.parse.urljoin(url, src) for src in parser.images[:2]), parser.title
