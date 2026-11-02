"""Public-source collection and persona rendering checks; never sends WeChat."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore
from app.news import NewsService, NewsItem
from app.news_evidence import news_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['collect', 'tone', 'render'])
    args = parser.parse_args()
    real = ConfigStore()
    # An in-memory copy: never change the account's configuration or send history.
    local = copy.copy(real)
    local.data = copy.deepcopy(real.data)
    for profile in local.data['model_profiles']:
        profile.update(supports_vision=False, timeout=45, max_tokens=1200)
    service = NewsService(local, PersonaStore())
    current = news_now()
    if args.mode == 'render':
        result = service.preview()
        print('LIVE_NEWS_PREVIEW', result['message'], sep='\n', flush=True)
        print('NO_WECHAT_MESSAGES_SENT; NO_STATE_WRITES', flush=True)
        return 0
    if args.mode == 'collect':
        grouped = service.fetch()
        targets = service.enabled_targets()
        chat = str(targets[0].get('name', '')) if targets else ''
        selected = service._filter_seen(grouped, 2, chat, current)
        print('COLLECTION_REPORT', json.dumps(service._fetch_report, ensure_ascii=False), flush=True)
        print('VERIFIED_NEW_COUNTS', json.dumps({k: len(v) for k, v in selected.items()}, ensure_ascii=False), flush=True)
        print('ACCEPTED_PUBLICATION_TIMES', [x.published_at for rows in selected.values() for x in rows], flush=True)
        print('NO_WECHAT_MESSAGES_SENT; NO_STATE_WRITES', flush=True)
        return 0
    for persona in ('arona', 'plana'):
        local.data['active_persona_id'] = persona
        stamp = current.isoformat(timespec='seconds')
        # Entirely synthetic evidence, explicitly labelled; not real game news.
        fixtures = {region: [NewsItem(region, '测试素材：公告校对', 'https://example.com/news-fixture',
                                     '测试来源', '这是语气验收用的合成素材：维护说明补充了领取条件，不包含实际游戏新闻。',
                                     published_at=stamp, body_read=True)]
                    for region in ('国服', '日服', '国际服')}
        text, _ = service.build_message(fixtures, current)
        print('SYNTHETIC_NEWS_TONE', persona, text, sep='\n', flush=True)
        empty = {region: [] for region in fixtures}
        service._source_errors = ['测试来源']
        text, _ = service.build_message(empty, current)
        print('EMPTY_NEWS_TONE', persona, text, sep='\n', flush=True)
        service._source_errors = []
    print('NO_WECHAT_MESSAGES_SENT; NO_STATE_WRITES', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
