"""Offline tests for the daily Kivotos news collector/scheduler."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_store import ConfigStore, PersonaStore
from app.news import (NewsItem, NewsService, _BingParser, _past_tense_issue,
                      _sanitize_past_tense)
from tests.service_test import FakeAdapter


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

    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / "config.json")
        config.set_active_profile("demo-local")
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
                              "官方账号", uncertain=False)],
            "日服": [NewsItem("日服", "日服新卡池爆料", "https://example.com/jp",
                              "搬运账号", uncertain=True)],
            "国际服": [],
        }
        service.fetch = lambda: items  # type: ignore[assignment]

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

    print("KIVOTOS NEWS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
