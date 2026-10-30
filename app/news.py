"""Daily Kivotos news digest.

The collector deliberately uses a small, replaceable set of public search
endpoints and always falls back to a plain template if the model is offline.
It never runs when the feature is disabled.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .config_store import ConfigStore, PersonaStore
from .model_client import make_client
from .paths import DATA
from .prompting import reduce_ellipsis

log = logging.getLogger(__name__)

NEWS_STATE_FILE = DATA / "news_state.json"
DEFAULT_TIME = "06:50"

# A search result is only accepted if it is visibly about Blue Archive.
RELEVANT_TERMS = (
    "蔚蓝档案", "碧蓝档案", "blue archive", "bluearchive",
    "ブルーアーカイブ", "ブルアカ", "블루 아카이브",
)
NEWS_TERMS = (
    "更新", "卡池", "招募", "维护", "活动", "公告", "新角色", "版本",
    "前瞻", "攻略", "实况", "抽卡", "演习", "总力", "新闻", "爆料",
    "情报", "上线", "复刻", "模式", "介绍", "资讯", "pickup", "banner",
    "update", "event", "character", "news", "patch", "announcement",
)
UNCERTAIN_TERMS = (
    "爆料", "泄露", "泄漏", "传闻", "猜测", "疑似", "据传", "或将",
    "可能", "未确认", "未证实", "rumor", "leak", "unconfirmed", "allegedly",
)
SOCIAL_DOMAINS = (
    "x.com", "twitter.com", "weibo.com", "tieba.baidu.com", "reddit.com",
    "bilibili.com", "youtube.com",
)


@dataclass
class NewsItem:
    region: str
    title: str
    url: str
    source: str
    snippet: str = ""
    uncertain: bool = False
    images: tuple[str, ...] = ()


def _past_tense_issue(text: str, now: datetime) -> bool:
    """Return True if the digest uses future wording for an already-past date."""
    future_words = re.compile(r"将|会|预计|即将|将要|准备|计划")
    date_re = re.compile(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日")
    for sentence in re.split(r"[。！？!?\n]", text or ""):
        if not sentence.strip():
            continue
        for match in date_re.finditer(sentence):
            year = int(match.group(1) or now.year)
            month = int(match.group(2))
            day = int(match.group(3))
            try:
                event_date = date(year, month, day)
            except ValueError:
                continue
            if event_date < now.date() and future_words.search(sentence):
                return True
    return False


def _sanitize_past_tense(text: str, now: datetime) -> str:
    """Remove predictive wording from sentences whose date is already past.

    The model is asked to verify dates, but a second deterministic pass keeps
    a single hallucinated "预计/将/会" from reaching the group.
    """
    date_re = re.compile(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日")
    future = re.compile(r"预计|即将|将要|将会|将于|将在|会在|会于|将|会")
    out = []
    for sentence in re.split(r"(?<=[。！？!?\n])", text or ""):
        has_past = False
        if sentence.strip():
            for match in date_re.finditer(sentence):
                year = int(match.group(1) or now.year)
                try:
                    event_date = date(year, int(match.group(2)), int(match.group(3)))
                except ValueError:
                    continue
                if event_date < now.date():
                    has_past = True
                    break
        if has_past:
            sentence = sentence.replace("预计持续", "持续")
            sentence = sentence.replace("预计于", "已于")
            sentence = sentence.replace("预计在", "已在")
            sentence = sentence.replace("预计", "")
            sentence = sentence.replace("将会", "已经")
            sentence = sentence.replace("将要", "已经")
            sentence = sentence.replace("即将", "已经")
            sentence = sentence.replace("将于", "已于")
            sentence = sentence.replace("将在", "已在")
            sentence = sentence.replace("会在", "已在")
            sentence = sentence.replace("会于", "已于")
            # Only replace future auxiliaries when they introduce a verb; this
            # leaves words such as 机会/会员 untouched.
            sentence = re.sub(
                r"(?<!机)会(?=(?:于|在|从|开始|开启|上线|结束|持续|进行|登场|举办|发布|更新))",
                "已", sentence,
            )
            sentence = re.sub(
                r"将(?=(?:于|在|从|开始|开启|上线|结束|持续|进行|登场|举办|发布|更新))",
                "已", sentence,
            )
        out.append(sentence)
    return "".join(out)


class _BingParser(HTMLParser):
    """Small, dependency-free parser for Bing's organic result list."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._in_algo = 0
        self._in_h2 = 0
        self._in_link = 0
        self._in_cite = 0
        self._current: dict | None = None
        self._capture: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "li" and "b_algo" in classes:
            self._finish()
            self._in_algo = 1
            self._current = {"title": "", "url": "", "source": "", "snippet": ""}
            return
        if not self._in_algo or not self._current:
            return
        if tag == "h2":
            self._in_h2 += 1
        elif tag == "a" and self._in_h2 and not self._current["url"]:
            self._in_link = 1
            self._current["url"] = attributes.get("href") or ""
            self._capture = []
        elif tag == "p" and not self._current["snippet"]:
            self._capture = []
        elif tag == "cite":
            self._in_cite = 1
            self._capture = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_algo or not self._current:
            return
        if tag == "h2":
            self._in_h2 = max(0, self._in_h2 - 1)
        elif tag == "a" and self._in_link:
            self._current["title"] = _clean("".join(self._capture))
            self._in_link = 0
            self._capture = []
        elif tag == "p" and self._capture:
            self._current["snippet"] = _clean("".join(self._capture))
            self._capture = []
        elif tag == "cite" and self._in_cite:
            self._current["source"] = _clean("".join(self._capture))
            self._in_cite = 0
            self._capture = []
        elif tag == "li":
            self._finish()

    def handle_data(self, data: str) -> None:
        if self._in_algo and self._current and (self._in_link or self._in_cite or self._capture):
            self._capture.append(data)
        elif self._in_algo and self._current and self._in_h2 and not self._current["title"]:
            # Some Bing layouts wrap the title in spans instead of a direct link.
            self._capture.append(data)

    def _finish(self) -> None:
        if self._current:
            if self._current.get("title") and self._current.get("url"):
                self.items.append(self._current)
        self._current = None
        self._in_algo = 0
        self._in_h2 = 0
        self._in_link = 0
        self._in_cite = 0
        self._capture = []

    def close(self) -> None:
        super().close()
        self._finish()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip()


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def _is_uncertain(text: str, source: str) -> bool:
    lowered = (text or "").lower()
    if any(term in lowered for term in UNCERTAIN_TERMS):
        return True
    return any(domain in source.lower() for domain in SOCIAL_DOMAINS)


def _build_opener():
    """Prefer direct, then a reachable system proxy.

    A stale Windows proxy is very common (Clash/V2Ray was closed while the
    environment variable remained).  Search must keep working in that state.
    """
    for force_direct in (True, False):
        try:
            if force_direct:
                return urllib.request.build_opener(urllib.request.ProxyHandler({}))
            configured = urllib.request.getproxies()
            usable = {}
            for scheme, value in configured.items():
                if scheme == "no" or not value:
                    continue
                parsed = urllib.parse.urlparse(str(value))
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                if not host:
                    continue
                with socket.create_connection((host, port), timeout=0.35):
                    usable[scheme] = str(value)
            if usable:
                return urllib.request.build_opener(urllib.request.ProxyHandler(usable))
        except Exception:
            continue
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_text(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6,ja;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with _build_opener().open(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", response.headers.get("Content-Type", ""), re.I)
    if match:
        charset = match.group(1)
    return raw.decode(charset, "replace")


def _http_bytes(url: str, timeout: float = 15.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36",
            "Referer": "https://bluearchive-cn.com/",
        },
    )
    with _build_opener().open(request, timeout=timeout) as response:
        return response.read(12_000_000)


def _http_json(url: str, timeout: float = 10.0) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36",
            "Referer": "https://search.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Cookie": "buvid3=" + str(uuid.uuid4()).upper() + "infoc; b_nut=1790000000",
        },
    )
    with _build_opener().open(request, timeout=timeout) as response:
        raw = response.read(4_000_000)
    return json.loads(raw.decode("utf-8", "replace"))


def _strip_html(text: str) -> str:
    return _clean(re.sub(r"<[^>]+>", "", text or ""))


OFFICIAL_AUTHORS = {
    "蔚蓝档案", "蔚蓝档案小助理", "Blue Archive", "ブルーアーカイブ",
    "BlueArchive", "蔚蓝档案官方",
}


class NewsService:
    """Fetch, deduplicate, summarize, and send one daily digest."""

    REGIONS = (
        ("国服", "蔚蓝档案 国服", "蔚蓝档案"),
        ("日服", "蔚蓝档案 日服", "蔚蓝档案 日服 公告"),
        ("国际服", "蔚蓝档案 国际服", "Blue Archive 国际服"),
    )

    def __init__(self, config: ConfigStore, personas: PersonaStore,
                 state_path: Path | None = None,
                 usage_callback: Callable | None = None) -> None:
        self.config = config
        self.personas = personas
        self.state_path = state_path or NEWS_STATE_FILE
        self.usage_callback = usage_callback
        self._lock = threading.Lock()

    # --------------------------------------------------------------- settings
    @property
    def settings(self) -> dict:
        return self.config.data.setdefault("news", {})

    def enabled_targets(self) -> list[dict]:
        """Groups with their own news switch, or the legacy global target."""
        per_target = [t for t in self.config.enabled_targets()
                      if t.get("type") == "group" and bool(t.get("news_enabled", False))]
        if per_target:
            return per_target
        legacy = self.config.target_by_name(self.target_chat)
        if (not self.settings.get("per_target")
                and self.settings.get("enabled", False) and legacy
                and legacy.get("type") == "group" and legacy.get("enabled", True)):
            return [legacy]
        return []

    @property
    def enabled(self) -> bool:
        return bool(self.enabled_targets())

    @property
    def target_chat(self) -> str:
        return str(self.settings.get("target_chat", "")).strip()

    @property
    def send_time(self) -> str:
        return str(self.settings.get("send_time", DEFAULT_TIME)).strip() or DEFAULT_TIME

    @staticmethod
    def _target_time(target: dict) -> str:
        return str(target.get("news_time") or DEFAULT_TIME).strip() or DEFAULT_TIME

    @staticmethod
    def _parse_time(value: str) -> tuple[int, int]:
        try:
            hour, minute = str(value or DEFAULT_TIME).split(":", 1)
            return max(0, min(23, int(hour))), max(0, min(59, int(minute)))
        except (TypeError, ValueError):
            return 6, 50

    # ------------------------------------------------------------------ state
    def _load_state(self) -> dict:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return {}

    def _save_state(self, patch: dict) -> None:
        state = self._load_state()
        state.update(patch)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError as exc:
            log.warning("写入新闻状态失败：%s", exc)

    def _sent_dates(self) -> dict:
        state = self._load_state()
        dates = state.get("sent_dates")
        if not isinstance(dates, dict):
            dates = {}
        legacy_target = str(state.get("last_target") or "")
        legacy_date = str(state.get("last_sent_date") or "")
        if legacy_target and legacy_date:
            dates.setdefault(legacy_target, legacy_date)
        return dates

    def _attempts(self) -> dict:
        values = self._load_state().get("attempts")
        return values if isinstance(values, dict) else {}

    def last_sent_date(self, chat: str = "") -> str:
        dates = self._sent_dates()
        if chat:
            return str(dates.get(chat, ""))
        return max(dates.values(), default="") or str(
            self._load_state().get("last_sent_date", ""))

    def should_run_for(self, target: dict, now: datetime | None = None) -> tuple[bool, str]:
        if not target or target.get("type") != "group" or not target.get("enabled", True):
            return False, "目标群聊不可用"
        current = now or datetime.now()
        today = current.date().isoformat()
        if self._sent_dates().get(str(target.get("name", ""))) == today:
            return False, "今天已经发送过"
        hour, minute = self._parse_time(self._target_time(target))
        due = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if current < due:
            return False, "还没到发送时间"
        if (current - due).total_seconds() > 6 * 3600:
            return False, "已过早间补发窗口"
        attempt = float(self._attempts().get(str(target.get("name", "")), 0) or 0)
        if attempt and time.time() - attempt < 30 * 60:
            return False, "上次尝试失败，稍后重试"
        return True, "可以发送"

    def should_run(self, now: datetime | None = None) -> tuple[bool, str]:
        targets = self.enabled_targets()
        if not targets:
            return False, "功能关闭"
        for target in targets:
            ok, reason = self.should_run_for(target, now)
            if ok:
                return True, "可以发送"
        return False, reason


    # ------------------------------------------------------------------ fetch
    def _search(self, query: str, region: str, mkt: str, timeout: float) -> list[NewsItem]:
        url = ("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query) +
               f"&mkt={urllib.parse.quote(mkt)}&setlang={mkt.split('-')[0]}&count=8")
        try:
            html = _http_text(url, timeout=timeout)
        except Exception as exc:
            log.warning("新闻搜索失败 · %s · %s", region, exc)
            return []
        parser = _BingParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:
            log.warning("新闻页面解析失败 · %s · %s", region, exc)
            return []
        found: list[NewsItem] = []
        for row in parser.items:
            title = _clean(row.get("title", ""))
            snippet = _clean(row.get("snippet", ""))
            combined = (title + " " + snippet).lower()
            if not title or not any(term in combined for term in RELEVANT_TERMS):
                continue
            if not any(term in combined for term in (
                "更新", "卡池", "招募", "维护", "活动", "公告", "新角色",
                "版本", "news", "update", "banner", "event", "character",
            )):
                continue
            source = _clean(row.get("source", "")) or _domain(row.get("url", ""))
            if not source:
                source = "来源未确认"
            uncertain = _is_uncertain(title + " " + snippet, source)
            found.append(NewsItem(
                region=region,
                title=title[:110],
                url=row.get("url", ""),
                source=source[:48],
                snippet=snippet[:160],
                uncertain=uncertain,
            ))
        return found

    def _official_cn(self, timeout: float, per_region: int) -> list[NewsItem]:
        """Read the official CN "快讯" list.

        The homepage loads it through /api/news/list.  We read the linked
        articles' text (not videos) and keep the official CN server scope.
        """
        url = ("https://bluearchive-cn.com/api/news/list?"
               "pageIndex=1&pageNum=12&type=")
        try:
            payload = _http_json(url, timeout=timeout)
        except Exception as exc:
            log.warning("国服官网快讯读取失败：%s", exc)
            return []
        if int(payload.get("code", -1)) != 0:
            return []
        rows = (payload.get("data") or {}).get("rows") or []
        now = time.time()
        items: list[NewsItem] = []
        for row in rows:
            title = _clean(str(row.get("title") or ""))
            if not title:
                continue
            published = float(row.get("publishTime") or 0) / 1000.0
            if published and published > 0 and now - published > 21 * 86400:
                continue
            raw_content = str(row.get("content") or "")
            image_urls = []
            for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', raw_content, re.I):
                src = unescape(src)
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("https://") and src not in image_urls:
                    image_urls.append(src)
            content = _strip_html(raw_content)
            content = re.sub(r"\s+", " ", content).strip()
            item_id = row.get("id")
            items.append(NewsItem(
                region="国服",
                title=title[:110],
                url=(f"https://bluearchive-cn.com/news/{item_id}" if item_id
                     else "https://bluearchive-cn.com/"),
                source="国服官网",
                snippet=content[:500],
                uncertain=False,
                images=tuple(image_urls[:2]),
            ))
            if len(items) >= per_region:
                break
        return items

    def _bilibili(self, query: str, region: str, timeout: float,
                  per_region: int) -> list[NewsItem]:
        """Search Bilibili articles.  It is a useful public source for the
        Chinese community and also carries translated JP/global announcements.
        """
        url = ("https://api.bilibili.com/x/web-interface/search/type?"
               "search_type=article&keyword=" + urllib.parse.quote_plus(query) +
               "&order=pubdate&page=1")
        try:
            payload = _http_json(url, timeout=timeout)
        except Exception as exc:
            log.warning("新闻搜索失败 · %s · %s", region, exc)
            return []
        if int(payload.get("code", -1)) != 0:
            log.warning("新闻搜索返回错误 · %s · %s", region, payload.get("message", ""))
            return []
        rows = (payload.get("data") or {}).get("result") or []
        now = time.time()
        found: list[NewsItem] = []
        for row in rows:
            title = _strip_html(str(row.get("title") or ""))
            snippet = _strip_html(str(row.get("desc") or ""))
            combined = (title + " " + snippet).lower()
            if not title or not any(term in combined for term in RELEVANT_TERMS):
                continue
            if not any(term in combined for term in NEWS_TERMS):
                continue
            if region == "国服" and any(tag in title for tag in
                                        ("日服", "国际服", "Global", "繁中")):
                continue
            if region == "日服" and not any(tag in title for tag in
                                           ("日服", "ブルー", "日版")):
                continue
            if region == "国际服" and not any(tag in title for tag in
                                              ("国际服", "Global", "繁中", "EN服")):
                continue
            published = float(row.get("pub_time") or row.get("pubdate") or 0)
            if published and published > 0 and now - published > 14 * 86400:
                continue
            author = _clean(str(row.get("author") or "")) or "Bilibili 用户"
            source = author
            official = author in OFFICIAL_AUTHORS
            uncertain = (not official) or _is_uncertain(title + " " + snippet, source)
            article_id = row.get("id")
            url_item = (f"https://www.bilibili.com/read/cv{article_id}"
                        if article_id else "https://www.bilibili.com/")
            image_urls = []
            for src in (row.get("image_urls") or []):
                src = unescape(str(src or ""))
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("https://") and src not in image_urls:
                    image_urls.append(src)
            found.append(NewsItem(
                region=region,
                title=title[:110],
                url=url_item,
                source=source[:48],
                snippet=snippet[:160],
                uncertain=uncertain,
                images=tuple(image_urls[:2]),
            ))
            if len(found) >= per_region:
                break
        return found

    def fetch(self) -> dict[str, list[NewsItem]]:
        settings = self.settings
        timeout = max(5.0, min(20.0, float(settings.get("timeout", 10) or 10)))
        per_region = max(1, min(3, int(settings.get("max_items", 2) or 2)))
        fetch_limit = max(per_region, min(6, per_region * 3))
        result: dict[str, list[NewsItem]] = {region[0]: [] for region in self.REGIONS}
        seen: set[str] = set()
        for region, first_query, second_query in self.REGIONS:
            if region == "国服":
                # One official CN item is mandatory; the rest may come from
                # public article sources.  This keeps the digest fresh while
                # guaranteeing the official 快讯 is represented.
                for item in self._official_cn(timeout, 1):
                    key = re.sub(r"[^\w\u4e00-\u9fff]", "", item.title.lower())
                    if key and key not in seen:
                        seen.add(key)
                        result[region].append(item)
            for query in (first_query, second_query):
                for item in self._bilibili(query, region, timeout, fetch_limit):
                    key = re.sub(r"[^\w\u4e00-\u9fff]", "", item.title.lower())
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    result[region].append(item)
                    if len(result[region]) >= fetch_limit:
                        break
                if len(result[region]) >= fetch_limit:
                    break
            # Fallback to Bing if Bilibili has no usable result; Bing may be
            # empty for some locales but this keeps the collector replaceable.
            if not result[region]:
                for item in self._search(first_query, region,
                                         {"国服": "zh-CN", "日服": "ja-JP",
                                          "国际服": "en-US"}.get(region, "zh-CN"),
                                         timeout):
                    key = re.sub(r"[^\w\u4e00-\u9fff]", "", item.title.lower())
                    if key and key not in seen:
                        seen.add(key)
                        result[region].append(item)
                        if len(result[region]) >= per_region:
                            break
        return result

    # ---------------------------------------------------------------- content
    def _enrich_images(self, grouped: dict[str, list[NewsItem]]) -> None:
        """Read images attached to news articles via the configured vision model.

        The daily digest stays text-only; image understanding is used only to
        provide facts for the summary.  When the active profile has no vision
        capability, images are skipped without failing the news job.
        """
        profile = self.config.active_profile()
        if not bool(profile.get("supports_vision", False)):
            log.info("新闻图片理解跳过：当前模型未启用视觉能力")
            return
        try:
            from .model_client import ModelClient
        except Exception:
            return
        seen: set[str] = set()
        count = 0
        cache_dir = Path(DATA) / "temp" / "news_images"
        for region in ("国服", "日服", "国际服"):
            for item in grouped.get(region, []):
                descriptions = []
                for url in item.images:
                    if count >= 6 or url in seen:
                        continue
                    seen.add(url)
                    try:
                        raw = _http_bytes(url, timeout=15)
                        if len(raw) < 1024:
                            continue
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
                        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                            suffix = ".jpg"
                        path = cache_dir / f"news_{abs(hash(url))}{suffix}"
                        path.write_bytes(raw)
                        result = ModelClient(profile).describe_image(
                            path,
                            prompt=("请像新闻编辑一样读取这张《蔚蓝档案》图片："
                                    "提取其中所有可读文字，尤其是活动名、角色名、卡池、日期、"
                                    "版本、奖励和公告要点；只写确定看得见的内容，不要猜测，"
                                    "60 字以内。"),
                            timeout=90,
                        )
                        desc = str(result.text or "").strip().replace("\n", " ")[:160]
                        if desc:
                            descriptions.append(desc)
                            if self.usage_callback:
                                self.usage_callback(result, "news")
                    except Exception as exc:
                        log.debug("新闻图片读取失败 · %s · %s", url[:80], exc)
                    finally:
                        try:
                            if 'path' in locals() and path.exists():
                                path.unlink()
                        except OSError:
                            pass
                    count += 1
                if descriptions:
                    item.snippet = (item.snippet + "\n[图片内容] " +
                                    "；".join(descriptions))[:900]

    def _persona(self) -> dict:
        return self.personas.get(self.config.data.get("active_persona_id"))

    @staticmethod
    def _short_item(item: NewsItem) -> str:
        text = (item.snippet or item.title).strip()
        text = re.sub(r"\[图片\]|\[表情\]|https?://\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Skip the standard official greeting before the useful sentence.
        for prefix in ("“欢迎连接【什亭之箱】，老师。”", "“欢迎连接什亭之箱，老师。”",
                       "欢迎连接【什亭之箱】，老师。"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", text)
                     if part.strip()]
        picked = ""
        for sentence in sentences:
            if len(picked) + len(sentence) > 86:
                break
            picked += sentence
        if not picked:
            picked = text[:86]
        return picked[:86].rstrip("，,；; ")

    def _fallback_message(self, grouped: dict[str, list[NewsItem]]) -> str:
        persona = self._persona()
        name = str(persona.get("name") or "阿洛娜")
        today = datetime.now().strftime("%m月%d日")
        if "普拉娜" in name:
            intro = "老师，早上好。今日基沃托斯新闻整理如下。"
            outro = "未确认消息已经标注，普拉娜会继续留意。"
        else:
            intro = f"老师早上好，今天的基沃托斯新闻{name}整理好啦。"
            outro = f"有新的确切消息，{name}再补充。"
        lines = [f"【基沃托斯新闻 · {today}】", intro]
        for region in ("国服", "日服", "国际服"):
            lines.append("")
            lines.append(f"■ {region}")
            items = grouped.get(region, [])
            if not items:
                lines.append("· 今天暂时没有可靠的新消息。")
                continue
            for item in items:
                suffix = item.source
                if item.uncertain:
                    suffix += "，未确认"
                lines.append(f"· {self._short_item(item)}（{suffix}）")
        lines.extend(["", outro])
        return "\n".join(lines).strip()

    def _model_message(self, grouped: dict[str, list[NewsItem]]):
        persona = self._persona()
        name = str(persona.get("name") or "阿洛娜")
        facts = []
        for region in ("国服", "日服", "国际服"):
            facts.append(f"【{region}】")
            if not grouped.get(region):
                facts.append("- 今天暂无可靠消息")
            for item in grouped[region]:
                facts.append(
                    f"- {item.title}｜{item.snippet[:180]}｜来源：{item.source}"
                    + ("｜未确认" if item.uncertain else "")
                )
        now = datetime.now()
        system = (
            f"你是《蔚蓝档案》的{name}，正在给老师发每日「基沃托斯新闻」。\n"
            f"【时间核验】当前本地时间是 {now:%Y年%m月%d日 %H:%M}（Asia/Shanghai）。\n"
            f"你的语气：{persona.get('tone', '温柔、简洁、认真')}\n"
            "只使用下面给出的新闻事实，绝对不能补充、想象或编造事实。\n"
            "这不是新闻标题列表：请用你自己的话把内容讲给老师听，不要照抄标题，"
            "也不要逐条写「标题：……」；每条用一句自然的中文概括，必要时补充一句影响。\n"
            "分为【国服】【日服】【国际服】三个板块，每段 1–2 条，每条简短。\n"
            "凡是素材标有「未确认」或来源模糊的，必须在句末写「（未确认）」，"
            "不能把猜测写成确定消息。\n"
            "保留来源名称；总长度不超过 800 字；不要输出 Markdown 表格或分析过程。\n"
            "写每一条新闻前，先把它提到的日期与当前时间核对："
            "早于当前时间的事件必须使用过去时或进行时，例如「已于10月29日开启」"
            "「已在10月28日结束」「正在进行」，绝对不要再用「将」「会」「预计」"
            "「即将」等预测语气描述已经过去的日期；只有晚于当前时间的事件才可以用预测语气。"
            "素材原文即使是预告，也要根据当前时间改写。\n"
            "这是发到群聊里的每日日报，不是私聊：统一称呼「老师」或「各位老师」，"
            "不要点名或假设任何具体群友，也不要把示例昵称写进正文。\n"
            "尽量少用省略号，不要用「……」或「...」拖长语气。\n"
            "说人话：用日常中文把更新、卡池、活动讲清楚，主谓宾完整；"
            "不要堆圈子黑话、缩写或没解释的术语。\n"
            "开头可以自然地向老师问早，结尾用一句符合你性格的收束。"
        )
        prompt = (f"当前本地时间：{now:%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n"
                  "今日素材：\n" + "\n".join(facts))
        profile = self.config.active_profile()
        client = make_client(profile)
        result = client.chat([{"role": "user", "content": prompt}], system)
        text = str(result.text or "").strip()
        if len(text) >= 80 and all(mark in text for mark in
                                   ("国服", "日服", "国际服")) and _past_tense_issue(text, now):
            correction = (
                "上一版总结把已经过去的事件写成了未来时。\n"
                f"当前本地时间是 {now:%Y年%m月%d日 %H:%M}。\n"
                "请保持原有事实和三个板块，改为正确的时间语气："
                "过去的事件写「已于…」「已经…」「已结束」或「正在进行」，"
                "不要出现「将」「会」「预计」「即将」等预测词。只输出修正后的日报。"
            )
            try:
                corrected = client.chat(
                    [{"role": "user", "content": prompt + "\n\n" + correction}],
                    system,
                )
                fixed_text = str(corrected.text or "").strip()
                if (len(fixed_text) >= 80 and
                        all(mark in fixed_text for mark in ("国服", "日服", "国际服"))):
                    text = fixed_text
                    result = corrected
            except Exception as exc:
                log.warning("新闻时间语气修正失败：%s", exc)
        text = _sanitize_past_tense(text, now)
        if _past_tense_issue(text, now):
            return None, result
        if len(text) < 80:
            return None, result
        if not all(mark in text for mark in ("国服", "日服", "国际服")):
            return None, result
        return reduce_ellipsis(text[:1000].strip()), result

    def build_message(self, grouped: dict[str, list[NewsItem]]) -> tuple[str, object | None]:
        if not any(grouped.values()):
            return "", None
        if bool(self.settings.get("use_model_summary", True)):
            try:
                text, result = self._model_message(grouped)
                if text:
                    return text, result
                if result is not None and self.usage_callback:
                    self.usage_callback(result, "news")
            except Exception as exc:
                log.warning("新闻模型总结失败，改用模板：%s", exc)
        fallback = self._fallback_message(grouped)
        return reduce_ellipsis(_sanitize_past_tense(fallback, datetime.now())), None

    @staticmethod
    def _item_key(item: NewsItem) -> str:
        return item.region + ":" + re.sub(r"[^\w\u4e00-\u9fff]", "", item.title.lower())

    def _filter_seen(self, grouped: dict[str, list[NewsItem]],
                     per_region: int) -> dict[str, list[NewsItem]]:
        """Prefer items not sent in the last week, while keeping one CN official item."""
        state = self._load_state()
        raw_seen = state.get("seen_titles") or {}
        cutoff = date.today().toordinal() - 7
        seen = {}
        if isinstance(raw_seen, dict):
            for key, value in raw_seen.items():
                try:
                    if date.fromisoformat(str(value)).toordinal() >= cutoff:
                        seen[key] = str(value)
                except ValueError:
                    continue
        selected: dict[str, list[NewsItem]] = {}
        for region, items in grouped.items():
            fresh = [item for item in items if self._item_key(item) not in seen]
            picked = fresh[:per_region]
            if region == "国服":
                official = [item for item in items if item.source == "国服官网"]
                chosen_official = [item for item in picked if item.source == "国服官网"]
                if official and not chosen_official:
                    # The official CN 快讯 must appear every day, even when its
                    # newest item was already used yesterday.
                    picked = official[:1] + [item for item in picked
                                             if item is not official[0]][:per_region - 1]
                if not picked:
                    picked = official[:1] or items[:per_region]
            if not picked:
                # No fresh item today: avoid an empty section by repeating the
                # most relevant one, but never invent a story.
                picked = items[:per_region]
            selected[region] = picked
        return selected

    def _mark_seen(self, grouped: dict[str, list[NewsItem]]) -> None:
        state = self._load_state()
        seen = state.get("seen_titles") or {}
        if not isinstance(seen, dict):
            seen = {}
        today = date.today().isoformat()
        for items in grouped.values():
            for item in items:
                seen[self._item_key(item)] = today
        # Keep the file small; old keys are irrelevant after the seven-day filter.
        cutoff = date.today().toordinal() - 14
        pruned = {}
        for key, value in seen.items():
            try:
                if date.fromisoformat(str(value)).toordinal() >= cutoff:
                    pruned[key] = str(value)
            except ValueError:
                continue
        self._save_state({"seen_titles": pruned})

    # ------------------------------------------------------------------- send
    def run(self, adapter, target: dict | None = None,
            now: datetime | None = None, force: bool = False) -> dict:
        with self._lock:
            current = now or datetime.now()
            targets = self.enabled_targets()
            if target is None:
                if not targets:
                    if self.settings.get("enabled") and self.target_chat:
                        return {"sent": False, "reason": "目标群聊不可用：不是群聊"}
                    return {"sent": False, "reason": "功能关闭"}
                if force:
                    target = targets[0]
                else:
                    reason = "还没到发送时间"
                    for candidate in targets:
                        ok, candidate_reason = self.should_run_for(candidate, current)
                        reason = candidate_reason
                        if ok:
                            target = candidate
                            break
                    if target is None:
                        return {"sent": False, "reason": reason}
            if not force:
                ok, reason = self.should_run_for(target, current)
                if not ok:
                    return {"sent": False, "reason": reason}
            target_name = str(target.get("name", ""))
            if not target_name:
                return {"sent": False, "reason": "目标群聊不可用"}
            if not force:
                attempts = self._attempts()
                attempts[target_name] = time.time()
                self._save_state({"attempts": attempts,
                                  "last_attempt_ts": time.time(),
                                  "last_attempt_date": current.date().isoformat()})
            grouped = self.fetch()
            grouped = self._filter_seen(
                grouped, max(1, min(3, int(self.settings.get("max_items", 2) or 2)))
            )
            self._enrich_images(grouped)
            if not any(grouped.values()):
                self._save_state({"last_error": "没有检索到可靠新闻"})
                return {"sent": False, "reason": "没有检索到可靠新闻"}
            message, usage_result = self.build_message(grouped)
            if not message:
                return {"sent": False, "reason": "新闻总结为空"}
            if usage_result is not None and self.usage_callback:
                self.usage_callback(usage_result, "news")
            try:
                result = adapter.send_text(target_name, message)
            except Exception as exc:
                self._save_state({"last_error": str(exc)})
                log.warning("基沃托斯新闻发送失败：%s", exc)
                return {"sent": False, "reason": str(exc), "message": message}
            if not getattr(result, "ok", False):
                error = getattr(result, "error", "") or "发送失败"
                self._save_state({"last_error": error})
                log.warning("基沃托斯新闻发送失败 · %s · %s", target_name, error)
                return {"sent": False, "reason": error, "message": message}
            patch = {
                "last_sent_at": current.isoformat(timespec="seconds"),
                "last_target": target_name,
                "last_error": "",
                "last_preview": message[:300],
            }
            # A manual test send must not consume the real daily slot.
            if not force:
                dates = self._sent_dates()
                dates[target_name] = current.date().isoformat()
                patch["sent_dates"] = dates
                patch["last_sent_date"] = current.date().isoformat()
            self._save_state(patch)
            if not force:
                self._mark_seen(grouped)
            log.info("基沃托斯新闻已发送 · %s · %d 字", target_name, len(message))
            return {"sent": True, "target": target_name, "message": message,
                    "usage": usage_result}

    def preview(self) -> dict:
        """Fetch and render without sending (used by the settings page)."""
        grouped = self.fetch()
        self._enrich_images(grouped)
        if not any(grouped.values()):
            return {"message": "", "items": grouped}
        message, usage_result = self.build_message(grouped)
        return {"message": message, "items": grouped, "usage": usage_result}
