"""Explicit desktop checks restricted to test; never starts the bot service."""
from __future__ import annotations

import argparse
import json
import re
import logging
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.link_reader import extract_card
from app.memory import MemoryStore
from app.training import TrainingStore
from app.service import BotService
from app.wechat_adapter import WeChatAutoAdapter, IncomingMessage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['link', 'preview', 'voice', 'requested-voice', 'alias-identity', 'semantic', 'quote', 'news-test', 'idle-chat', 'idle-video', 'manual-video', 'manual-article', 'manual-image'])
    parser.add_argument('--no-send', action='store_true')
    parser.add_argument('--preset', default='', help='Voice preset for the test-only voice check')
    parser.add_argument('--chat-name', help='Read-only alias identity check: original group name')
    parser.add_argument('--remark', help='Read-only alias identity check: group remark')
    args = parser.parse_args()
    handler = logging.StreamHandler()
    logger = logging.getLogger('app.service')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    config = ConfigStore()
    if args.mode == 'alias-identity':
        # Read-only identity check for the pair explicitly named by the user.
        # No UIA initialization, navigation, recording or sending.
        from wechatauto import WeChatDB
        adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
        adapter._db = WeChatDB()
        original, remark = args.chat_name, args.remark
        if not original or not remark:
            parser.error('alias-identity requires --chat-name and --remark')
        left, right = adapter._resolve_username(original), adapter._resolve_username(remark)
        print('ORIGINAL_NAME_RESOLVED', bool(left))
        print('REMARK_RESOLVED', bool(right))
        assert left and right and left == right, 'names did not uniquely resolve to the same identity'
        adapter._usernames[original] = left
        adapter._targets_by_user[left] = {'name': original}
        assert adapter.is_same_chat(remark, original)
        assert adapter.canonical_chat(remark) == original
        print('SAME_CHAT_ID_VERIFIED', True)
        print('CANONICAL_CONTEXT_NAME_VERIFIED', True)
        return 0
    if args.mode == 'semantic':
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory = MemoryStore(base / 'memory.db')
            training = TrainingStore(base / 'training.db', base / 'files')
            try:
                service = BotService(config, PersonaStore(), StickerStore(), memory, training,
                                     admin_state_path=base / 'admin.json')
                for text, expected in [('阿洛娜，帮我看看这个链接', True), ('阿洛娜好可爱', False),
                                       ('阿洛娜的卡池什么时候开', False), ('昨天我问阿洛娜在吗', False)]:
                    actual = service._keyword_addressed(
                        IncomingMessage('test', '调试', text, chat_type='group'), ['阿洛娜', 'Arona'])
                    print(text, actual)
                    assert actual == expected
                return 0
            finally:
                memory.close()
                training.close()
    if args.mode == 'preview':
        from app.voice_presets import VoicePresetStore
        from app.character_voice import make_tts
        from wecvoice_auto import VoiceSender
        for item in VoicePresetStore().list()[:2]:
            result = VoiceSender(tts=make_tts(item)).send('你好', preview=True, **{
                k: item[k] for k in ('voice', 'rate', 'pitch', 'volume')})
            print(json.dumps({**result.to_dict(), 'engine': item.get('engine', 'edge')}, ensure_ascii=False))
            if not result.ok:
                return 1
        return 0
    adapter = WeChatAutoAdapter(dict(config.wechat), lambda _: None)
    try:
        adapter.start([{'name': 'test', 'type': 'group', 'enabled': True}])
        if not adapter.ensure_chat_open('test'):
            raise RuntimeError('无法确认 test 会话，取消发送')
        if adapter._uia.current_chat() != 'test':
            raise RuntimeError('当前会话不是 test，取消发送')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            local = ConfigStore(base / 'config.json')
            local.data = json.loads(json.dumps(config.data))
            local.bot.update(voice_enabled=False, auto_sticker=False, quote_reply=False,
                             reply_delay_min=0, reply_delay_max=0, global_cooldown=0)
            memory = MemoryStore(base / 'memory.db')
            training = TrainingStore(base / 'training.db', base / 'files')
            service = BotService(local, PersonaStore(), StickerStore(), memory, training,
                                 admin_state_path=base / 'admin.json')
            service._adapter = adapter
            service._running = True
            try:
                if args.mode in ('manual-article', 'manual-image'):
                    from unittest.mock import patch
                    from app.proactive_share import ProactiveArticles
                    source = config.target_by_name('test') or {}
                    local.data['targets'] = [{**source, 'name':'test', 'type':'group', 'enabled':True}]
                    local.bot.update(idle_chat_enabled=True, auto_reply=True, web_search_enabled=True,
                                     quiet_hours_enabled=False, global_cooldown=0, per_chat_cooldown=0)
                    service.proactive_articles = ProactiveArticles(base/'article-state.json')
                    service._sync_idle_groups(adapter)
                    user = next(iter(adapter.target_usernames()))
                    before = adapter._last_seq(user)
                    decision = 'IMAGE' if args.mode == 'manual-image' else 'ARTICLE'
                    with patch.object(service, '_proactive_decision', return_value=decision):
                        service._idle_tick()
                    state = service._idle_chats['test']
                    if not state['sent']:
                        print('PUBLIC_SHARE_UNAVAILABLE_NO_MESSAGE_SENT')
                        return 1
                    bubbles = memory.recent('test', 1)[-1]['meta']['bubbles']
                    assert 2 == len(bubbles) and bubbles[0].startswith(('https://', 'http://'))
                    assert len(bubbles[1]) <= 15
                    for _ in range(12):
                        rows = adapter._db.get_messages(user, limit=10)
                        matched = [row for row in rows if int(row.get('sort_seq', 0)) > before
                                   and str(row.get('sender_username') or '') == adapter.self_wxid
                                   and str(row.get('content') or '').strip() in bubbles]
                        if len(matched) == len(bubbles):
                            print('PUBLIC_SHARE_KIND', decision)
                            print('PUBLIC_SHARE_OUTGOING_DB_CONFIRMED', True)
                            print('PUBLIC_SHARE_DEDUP_PERSISTED', bool(service.proactive_articles.sent.get('test')))
                            return 0
                        time.sleep(.5)
                    print('ARTICLE_SENT_BUT_DB_NOT_CONFIRMED')
                    return 1
                if args.mode == 'manual-video':
                    from app.bilibili_video import BilibiliVideoStore, valid_video_link
                    source = config.target_by_name('test') or {}
                    local.data['targets'] = [{**source, 'name': 'test', 'type': 'group', 'enabled': True,
                                              'persona_id': source.get('persona_id') or local.data.get('active_persona_id', 'arona')}]
                    # Copy dedup history into an isolated test store; do not
                    # overwrite another running bot's production state file.
                    state_path = base / 'manual-video-state.json'
                    if service.idle_videos.state_path.exists():
                        state_path.write_bytes(service.idle_videos.state_path.read_bytes())
                    service.idle_videos = BilibiliVideoStore(state_path)
                    local.bot.update(idle_chat_enabled=False, idle_chat_probability=0,
                                     idle_video_enabled=False, idle_video_probability=0)
                    memory.add_user('test', '我喜欢蔚蓝档案的剧情和活动。', sender='测试群友')
                    service._sync_idle_groups(adapter)
                    user = next(iter(adapter.target_usernames()))
                    before = adapter._last_seq(user)
                    result = service.send_manual_idle_video('test')
                    print('MANUAL_VIDEO_RESULT', result['ok'], result.get('partial', False), flush=True)
                    if not result['ok']:
                        print('MANUAL_VIDEO_REASON', result['message'])
                        return 1
                    bubbles = result['bubbles']
                    assert 2 <= len(bubbles) <= 3 and valid_video_link(bubbles[0])
                    assert all(0 < len(s) <= 15 for s in bubbles[1:])
                    assert service._idle_chats['test']['sent']
                    saved = memory.recent('test', 1)[-1]
                    video = saved['meta']['video']
                    assert video['title'] in saved['content'] and all(s in saved['content'] for s in bubbles)
                    print('VIDEO_CONTEXT_AND_SENT_COMMENTS_VERIFIED', True)
                    assert video['bvid'] in BilibiliVideoStore(state_path)._sent['test']
                    for _ in range(12):
                        rows = adapter._db.get_messages(user, limit=10)
                        matches = [r for r in rows if int(r.get('sort_seq', 0)) > before
                                   and str(r.get('sender_username') or '') == adapter.self_wxid
                                   and str(r.get('content') or '').strip() in bubbles]
                        if len(matches) == len(bubbles):
                            print('MANUAL_VIDEO_OUTGOING_DB_CONFIRMED', True)
                            print('MANUAL_VIDEO_DEDUP_PERSISTED', True)
                            print('MANUAL_VIDEO_AUTOMATIC_SWITCHES_DISABLED', True)
                            print('MANUAL_VIDEO_BUBBLE_LENGTHS', [len(s) for s in bubbles])
                            return 0
                        time.sleep(.5)
                    print('MANUAL_VIDEO_SENT_BUT_DB_NOT_CONFIRMED')
                    return 1
                if args.mode in ('idle-chat', 'idle-video'):
                    # Isolate the forced draw and simulated silence to test only.
                    # The real configuration and every other group are untouched.
                    local.data['targets'] = [{'name': 'test', 'type': 'group', 'enabled': True,
                                              'persona_id': local.data.get('active_persona_id', 'arona')}]
                    local.bot.update(idle_chat_enabled=True, idle_chat_probability=1,
                                     idle_chat_minutes=30, auto_reply=True,
                                     idle_video_enabled=args.mode == 'idle-video',
                                     idle_video_topics=['蔚蓝档案'], idle_video_probability=1)
                    from app.bilibili_video import BilibiliVideoStore, valid_video_link
                    service.idle_videos = BilibiliVideoStore(base / 'video-state.json')
                    service._sync_idle_groups(adapter)
                    state = service._idle_chats['test']
                    state['last'] -= 1801
                    user = next(iter(adapter.target_usernames()))
                    before = adapter._last_seq(user)
                    service._idle_tick()
                    assert state['sent'], 'no idle message sent (quiet hours may be active)'
                    text = state['last_text']
                    bubbles = memory.recent('test', 1)[-1]['meta']['bubbles']
                    assert 1 <= len(bubbles) <= 3 and all(s for s in bubbles)
                    assert all(len(s) <= 23 or valid_video_link(s) for s in bubbles)
                    if args.mode == 'idle-video':
                        assert valid_video_link(bubbles[0]), 'video unavailable; greeting was sent instead'
                        assert len(bubbles) >= 2 and all(len(s) <= 15 for s in bubbles[1:])
                    service._idle_tick()
                    for _ in range(12):
                        rows = adapter._db.get_messages(user, limit=10)
                        matches = [row for row in rows if int(row.get('sort_seq', 0)) > before
                                   and str(row.get('sender_username') or '') == adapter.self_wxid
                                   and str(row.get('content') or '').strip() in bubbles]
                        if len(matches) == len(bubbles):
                            assert {str(r.get('content') or '').strip() for r in matches} == set(bubbles)
                            print('IDLE_CHAT_OUTGOING_DB_CONFIRMED', True)
                            print('IDLE_CHAT_ONCE_PER_SILENCE_VERIFIED', True)
                            if args.mode == 'idle-video':
                                assert service.idle_videos.state_path.exists()
                                print('VIDEO_LINK_AND_DEDUP_RECORD_VERIFIED', True)
                                print('VIDEO_COMMENT_BUBBLE_LENGTHS', [len(s) for s in bubbles[1:]])
                            return 0
                        time.sleep(.5)
                    print('IDLE_CHAT_SENT_BUT_DB_NOT_CONFIRMED')
                    return 1
                if args.mode == 'news-test':
                    from app.news import NewsService
                    service.news = NewsService(local, PersonaStore(), state_path=base / 'news.json')
                    # Track both actual local production state and isolated state.
                    from app.news import NEWS_STATE_FILE
                    before = NEWS_STATE_FILE.read_bytes() if NEWS_STATE_FILE.exists() else None
                    result = service.send_test_news('test')
                    after = NEWS_STATE_FILE.read_bytes() if NEWS_STATE_FILE.exists() else None
                    assert before == after and not service.news.state_path.exists()
                    print('NEWS_TEST_RESULT', result['ok'], result.get('no_new_items'))
                    print('PRODUCTION_NEWS_STATE_UNCHANGED', True)
                    return 0 if result['ok'] else 1
                if args.mode == 'requested-voice':
                    local.bot.update(voice_enabled=False, voice_probability=0)
                    user = next(iter(adapter.target_usernames()))
                    before = adapter._last_seq(user)
                    request = IncomingMessage('test', '调试', '阿洛娜，喵一声', chat_type='group')
                    row_id = memory.add_user('test', request.content, sender=request.sender)
                    service._generate_and_send(
                        request, {'name': 'test', 'type': 'group', 'persona_id': 'arona'},
                        row_id, request.content, force_reply=True)
                    events = []
                    while not service.events.empty():
                        kind, data = service.events.get_nowait()
                        if kind == 'reply':
                            events.append(data)
                    voice = bool(events and events[-1].get('voice'))
                    print('REQUESTED_VOICE_REPLY', voice)
                    if not voice:
                        return 1
                    for _ in range(12):
                        rows = adapter._db.get_messages(user, limit=6)
                        if any(int(row.get('sort_seq', 0)) > before
                               and str(row.get('sender_username') or '') == adapter.self_wxid
                               and adapter._message_type_name(row.get('type')) == 'voice' for row in rows):
                            print('NATIVE_VOICE_BUBBLE_DB_CONFIRMED', True)
                            return 0
                        time.sleep(.5)
                    print('VOICE_SENT_BUT_DB_NOT_CONFIRMED')
                    return 1
                if args.mode == 'quote':
                    user = next(iter(adapter.target_usernames()))
                    rows = adapter._db.get_messages(user, limit=30)
                    target = next((row for row in rows
                                   if str(row.get('sender_username') or '') != adapter.self_wxid
                                   and adapter._message_type_name(row.get('type')) == 'text'
                                   and str(row.get('content') or '').strip()), None)
                    if target is None:
                        print('QUOTE_TEST: no incoming text in test; cancelled')
                        return 2
                    content = str(target['content'])
                    body = re.sub(r'^\s*(?:wxid_[^\s:]+|[^\s:]+@chatroom):\r?\n', '', content)
                    target_row = adapter._db.get_message_row(user, int(target['local_id']),
                                                             shard=target.get('shard'))
                    target_server_id = str((target_row or {}).get('server_id') or '')
                    print('QUOTE_TARGET_IS_LATEST', target == rows[0])
                    if args.no_send:
                        uia = adapter._uia
                        lst = uia._message_list()
                        needle = re.sub(r'\s+', '', content)[:40]
                        cleaned = re.sub(r'^\s*@\S+\s*', '', body)
                        cleaned = re.sub(r'^\s*\[[^\]]+\]\s*', '', cleaned)
                        clean_needle = re.sub(r'\s+', '', cleaned)[:40]
                        print('QUOTE_TARGET_METADATA', json.dumps({
                            'chars': len(content), 'clean_chars': len(cleaned),
                            'requested_phrase': '引用这条最新消息' in content,
                            'starts_xml': content.lstrip().startswith('<'),
                            'sender_prefix': bool(re.match(r'^wxid_[^:]+:', content)),
                        }))
                        summary = []
                        for child in lst.GetChildren() if lst else []:
                            name = re.sub(r'\s+', '', child.Name or '')
                            r = child.BoundingRectangle
                            summary.append({'class': child.ClassName,
                                            'name_chars': len(name), 'matches_target': needle in name,
                                            'matches_cleaned': bool(clean_needle and clean_needle in name),
                                            'requested_phrase': '引用这条最新消息' in name,
                                            'rect': [r.left, r.top, r.right, r.bottom]})
                        print('QUOTE_VIEW_DIAGNOSTIC', json.dumps(summary, ensure_ascii=False))
                        return 0
                    before = adapter._last_seq(user)
                    result = adapter.send_quoted_text('test', '引用定位测试，收到啦。', content)
                    if not result.ok:
                        print('QUOTE_TEST_FAILED', result.message)
                        return 1
                    # Validate the sent quote against local DB XML, not just UI success.
                    for _ in range(12):
                        for row in adapter._db.get_messages(user, limit=10):
                            if (int(row.get('sort_seq', 0)) <= before or
                                    str(row.get('sender_username') or '') != adapter.self_wxid):
                                continue
                            try:
                                xml = ET.fromstring(str(row.get('content') or ''))
                            except ET.ParseError:
                                continue
                            ref = xml.findtext('.//refermsg/content', '')
                            title = xml.findtext('.//appmsg/title', '')
                            ref_server_id = xml.findtext('.//refermsg/svrid', '')
                            content_matches = ref.strip() in (content.strip(), body.strip())
                            id_matches = bool(target_server_id and ref_server_id == target_server_id)
                            if title == '引用定位测试，收到啦。' and content_matches and id_matches:
                                print('QUOTE_EXACT_TARGET_VERIFIED', True)
                                return 0
                        time.sleep(.5)
                    print('QUOTE_SENT_BUT_DB_TARGET_NOT_VERIFIED')
                    return 1
                if args.mode == 'voice':
                    user = next(iter(adapter.target_usernames()))
                    before = adapter._last_seq(user)
                    result = service.send_test_voice('test', preset_id=args.preset or str(config.bot.get('voice_name', 'arona')))
                    print('TEST_VOICE_RESULT', json.dumps(result, ensure_ascii=False))
                    if not result.get('ok'):
                        return 1
                    for _ in range(12):
                        rows = adapter._db.get_messages(user, limit=8)
                        if any(int(row.get('sort_seq', 0)) > before
                               and str(row.get('sender_username') or '') == adapter.self_wxid
                               and adapter._message_type_name(row.get('type')) == 'voice' for row in rows):
                            print('NATIVE_VOICE_BUBBLE_DB_CONFIRMED', True)
                            return 0
                        time.sleep(.5)
                    print('VOICE_SENT_BUT_DB_NOT_CONFIRMED')
                    return 1
                users = adapter.target_usernames()
                user = next(iter(users))
                selected = None
                for row in adapter._db.get_messages(user, limit=30):
                    text = str(row.get('content') or '')
                    card = extract_card(text)
                    if card.url or card.channels:
                        selected = adapter._map_message(row, {'type': 'group'}, 'test')
                        break
                if selected is None:
                    selected = IncomingMessage('test', '调试', 'https://github.com/rany2/edge-tts', chat_type='group')
                    print('NO_RECENT_CARD: using public Edge TTS project page')
                card = extract_card(selected.content)
                print('SHARE_KIND', '视频号' if card.channels else '链接')
                print('SHARE_TITLE_CHARS', len(card.title))
                enriched = service._shared_content(selected, selected.content)
                print('READ_OK', '网页可见文字：' in enriched or card.channels)
                if args.no_send:
                    print('READ_CONTENT_CHARS', len(enriched))
                    from app.model_client import make_client
                    result = make_client(config.active_profile()).chat(
                        [{'role': 'user', 'content': enriched[:7000]}],
                        '你是阿洛娜。对分享内容发表一句自然中文评论，整条必须不超过23字。只根据读到的文字，不假装看过视频。')
                    print('MODEL_REPLY_GENERATED', bool(result.text.strip()))
                    return 0
                row_id = memory.add_user('test', enriched, sender=selected.sender)
                target = {'type': 'group', 'name': 'test', 'persona_id': 'arona', 'context_limit': 20}
                selected.content = '链接读取测试'
                service._generate_and_send(selected, target, row_id, enriched, force_reply=True)
                print('TEST_REPLY_COUNT', service.state().get('replies'))
                print('TEST_REPLY', memory.recent('test', 1)[0].get('content', ''))
                return 0 if service.state().get('replies') else 1
            finally:
                service._running = False
                memory.close()
                training.close()
    finally:
        adapter.stop()


if __name__ == '__main__':
    raise SystemExit(main())
