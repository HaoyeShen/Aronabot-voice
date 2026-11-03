"""Unit test for the optional WeChat 4.1.15 backend (no live WeChat needed)."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wechat_adapter import IncomingMessage, WeChatAutoAdapter


class FakeWxParam:
    ENABLE_OCR = True


class FakeDB:
    wxid = "wxid_self"

    def __init__(self):
        self.rows = {
            "room@chatroom": [
                {"local_id": 10, "sort_seq": 10, "type": "文本",
                 "sender_username": "wxid_friend", "content": "旧消息",
                 "shard": "message_0.db", "username": "room@chatroom"},
            ]
        }
        self.seq = 10

    def get_self_info(self):
        return {"username": "wxid_self", "nick_name": "测试账号"}

    def username_by_nickname(self, name):
        return "room@chatroom" if name == "测试群" else None

    def search_contact(self, name):
        return []

    def get_sessions(self, limit=100):
        return [{"username": "room@chatroom"}]

    def get_nickname(self, username):
        return {"wxid_self": "测试账号", "wxid_friend": "小明",
                "room@chatroom": "测试群"}.get(username, username)

    def get_messages(self, user, limit=20):
        return list(reversed(self.rows.get(user, [])))[:limit]

    def add_row(self, content, mtype="文本", sender="wxid_self"):
        self.seq += 1
        self.rows.setdefault("room@chatroom", []).append({
            "local_id": self.seq, "sort_seq": self.seq, "type": mtype,
            "sender_username": sender, "content": content,
            "shard": "message_0.db", "username": "room@chatroom",
        })


class FakeListener:
    last = None

    def __init__(self, db, **kwargs):
        self.db = db
        self.callbacks = {}
        FakeListener.last = self

    def add_listener(self, user, callback):
        self.callbacks[user] = callback

    def start(self):
        pass

    def stop(self):
        pass


class FakeControl:
    def __init__(self):
        self.keys = []

    def SendKeys(self, keys, waitTime=0.0):
        self.keys.append(keys)


class FakeUIA:
    last = None

    def __init__(self):
        self.sent = []
        self.control = FakeControl()
        self._open = ""
        FakeUIA.last = self

    def ensure_window(self):
        return True

    def ensure_materialized(self, timeout=10):
        return True

    def current_chat(self):
        return self._open

    def open_chat(self, name, retries=2):
        self._open = name
        return True

    def send_text(self, text):
        self.sent.append(text)
        FakeListener.last.db.add_row(text)
        return True

    def _chat_input(self):
        return self.control

    def _click_ctrl(self, ctrl):
        return True

    def is_running(self):
        return True


def main() -> int:
    incoming: list[IncomingMessage] = []
    statuses: list[tuple[str, str]] = []
    adapter = WeChatAutoAdapter({}, lambda msg: incoming.append(msg),
                                lambda k, t: statuses.append((k, t)))

    db_holder = {}

    def deps():
        WxParam, WeChatDB, Listener, WeChatUIA = (FakeWxParam, None, FakeListener, None)
        # Constructor factory for DB/UIA.
        class DBFactory(FakeDB):
            def __init__(self):
                super().__init__()
                db_holder["db"] = self
        return FakeWxParam, DBFactory, FakeListener, FakeUIA

    adapter._import_deps = deps
    adapter.start([{"name": "测试群", "type": "group", "enabled": True,
                    "persona_id": "arona", "require_at": True}])
    assert adapter.connected
    db = db_holder["db"]
    listener = FakeListener.last
    assert "room@chatroom" in listener.callbacks

    listener.callbacks["room@chatroom"]({
        "username": "room@chatroom", "sender_username": "wxid_friend",
        "content": "大家好", "type": "文本", "local_id": 11,
        "sort_seq": 11, "shard": "message_0.db",
    }, listener)
    assert incoming and incoming[-1].direction == "in"
    assert incoming[-1].sender == "小明"

    listener.callbacks["room@chatroom"]({
        "username": "room@chatroom", "sender_username": "wxid_self",
        "content": "这是老师手动发的", "type": "文本", "local_id": 12,
        "sort_seq": 12, "shard": "message_0.db",
    }, listener)
    assert incoming[-1].direction == "out"
    assert incoming[-1].origin == "self"
    # A reused local ID must not suppress a fresh mention days later.
    old = {'shard':'message_0.db', 'local_id':11, 'sort_seq':100, 'create_time':1700000000}
    fresh = {**old, 'sort_seq':200, 'create_time':1700100000}
    key = adapter._message_external_id('room@chatroom', old)
    assert key == adapter._message_external_id('room@chatroom', dict(old))
    assert key != adapter._message_external_id('room@chatroom', fresh)
    assert adapter._message_external_id('room@chatroom', {'content':'hello'}).startswith('fp:')
    target = {'type':'group'}
    mapped = adapter._map_message({**fresh, 'username':'room@chatroom', 'sender_username':'wxid_friend',
                                   'content':'@ARONA 在吗', 'type':'文本'}, target, '测试群')
    assert mapped.external_id == adapter._message_external_id('room@chatroom', fresh)
    assert mapped.direction == 'in'

    result = adapter.send_text("测试群", "测试回复")
    assert result.ok, result.message
    assert FakeUIA.last.sent[-1] == "测试回复"

    fake_uia = FakeUIA.last
    adapter._uia = fake_uia
    adapter._set_file_clipboard = lambda paths: True
    adapter._wait_outgoing = lambda *args, **kwargs: True
    sticker = ROOT / "data" / "stickers" / "arona_hello.png"
    result2 = adapter.send_sticker("测试群", sticker)
    assert result2.ok, result2.message
    assert any("{Ctrl}v" in k for k in fake_uia.control.keys)

    # Current viewport must be searched newest-first before scrolling history.
    latest = ('mmui::ChatTextItemView', '@阿洛娜 最新消息',
              SimpleNamespace(left=10, top=480, right=610, bottom=540))
    older = ('mmui::ChatTextItemView', '旧问题',
             SimpleNamespace(left=10, top=280, right=610, bottom=340))
    clicked = []
    calls = []
    def find(predicate, match_last=False, max_scrolls=20):
        calls.append((match_last, max_scrolls))
        rows = [older, latest]
        for row in reversed(rows) if match_last else rows:
            if predicate(row[0], row[1]):
                return row
        return None
    fake_uia.find_in_message_list = find
    fake_uia._set_cursor = lambda x, y: clicked.append((x, y))
    fake_uia._right_click = lambda: None
    with patch('app.wechat_adapter.time.sleep'):
        assert adapter._right_click_quote_message('测试群', '@阿洛娜 最新消息')
        assert calls == [(True, 0)] and clicked[-1][1] == 510
        assert adapter._right_click_quote_message('测试群', 'wxid_sender123:\n@阿洛娜 最新消息')
        assert clicked[-1][1] == 510
        assert adapter._right_click_quote_message('测试群', '旧问题')
        assert clicked[-1][1] == 310  # never quote latest when target is older
        with patch.object(fake_uia, 'find_in_message_list') as lookup:
            def duplicate(predicate, match_last=False, **kwargs):
                assert predicate(latest[0], latest[1])
                return latest if match_last else (latest[0], latest[1], older[2])
            lookup.side_effect = duplicate
            assert adapter._right_click_quote_message('测试群', '最新消息')
            assert clicked[-1][1] == 510  # duplicate text must prefer newest row
        with patch.object(fake_uia, 'find_in_message_list', side_effect=[None, latest]) as lookup:
            assert adapter._right_click_quote_message('测试群', '最新消息')
            assert all(c.kwargs['max_scrolls'] == 0 for c in lookup.call_args_list)
        before = len(clicked)
        assert not adapter._right_click_quote_message('测试群', '不存在的具体消息')
        assert len(clicked) == before and calls[-1] == (True, 20)
        for kind, marker in [('image', 'ChatImageItemView'), ('voice', 'ChatVoiceItemView')]:
            row = ('mmui::' + marker, '', latest[2])
            def media(predicate, **kwargs):
                return row if predicate(row[0], row[1]) else None
            with patch.object(fake_uia, 'find_in_message_list', side_effect=media):
                assert adapter._right_click_quote_message('测试群', '', kind)
        def quote_word(predicate, **kwargs):
            assert not predicate('mmui::ChatTextItemView', '引用其他人的旧消息 最新消息')
            assert predicate('mmui::ChatTextItemView', '引用这条最新消息')
            return ('mmui::ChatTextItemView', '引用这条最新消息', latest[2])
        with patch.object(fake_uia, 'find_in_message_list', side_effect=quote_word):
            assert adapter._right_click_quote_message('测试群', '引用这条最新消息')
            assert adapter._right_click_quote_message('测试群', 'wxid_sender123:\r\n引用这条最新消息')
    adapter.stop()
    # Group name and remark resolve to one stable ID, subscription and context key.
    class AliasDB(FakeDB):
        contacts = [dict(username='room@chatroom', nick_name='原群名', remark='群聊')]
        def search_contact(self, name):
            return [dict(item) for item in self.contacts if any(name in str(v) for v in item.values())]
        def get_nickname(self, username):
            return '群聊' if username == 'room@chatroom' else super().get_nickname(username)
    class AliasListener(FakeListener):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.added = []
        def add_listener(self, user, callback):
            self.added.append(user)
            super().add_listener(user, callback)
    alias_incoming = []
    adapter = WeChatAutoAdapter({}, alias_incoming.append)
    adapter._import_deps = lambda: (FakeWxParam, AliasDB, AliasListener, FakeUIA)
    adapter.start([{'name': name, 'type': 'group', 'enabled': True} for name in ('原群名', '群聊')])
    assert adapter._listener.added == ['room@chatroom']
    assert adapter.is_same_chat('群聊', '原群名') and adapter.is_same_chat('原群名', '群聊')
    assert adapter.canonical_chat('群聊') == '原群名'
    assert set(adapter._chat_names('原群名', 'room@chatroom')) >= {'群聊', '原群名'}
    adapter._uia._open = '群聊'
    assert adapter.ensure_chat_open('原群名')
    adapter._on_db_message({'username': 'room@chatroom', 'sender_username': 'wxid_friend',
                            'content': '你好', 'type': '文本', 'local_id': 22})
    assert alias_incoming[-1].chat == '原群名'
    assert adapter.send_text('群聊', '别名文字测试').ok
    assert adapter.fetch_recent('群聊', 3)[0].chat == '原群名'
    adapter._db.contacts.append(dict(username='other@chatroom', nick_name='其他群', remark='群聊'))
    assert not adapter.is_same_chat('群聊', '原群名'), 'ambiguous remark must not be trusted'
    assert not adapter.is_same_chat('其他群', '原群名')
    adapter._uia._open = '其他群'
    adapter._uia.open_chat = lambda *args, **kwargs: True
    with patch('app.wechat_adapter.time.sleep'):
        assert not adapter.ensure_chat_open('原群名'), 'open_chat success alone cannot prove identity'
    adapter.stop()
    print("WECHATAUTO ADAPTER TEST PASSED", statuses[0][0])
    validator = WeChatAutoAdapter({}, lambda _: None)
    with patch.object(validator, '_resolve_username', return_value=None):
        assert not validator.validate_group_chat('missing').ok
    with patch.object(validator, '_resolve_username', return_value='wxid_friend'):
        assert not validator.validate_group_chat('friend').ok
    with patch.object(validator, '_resolve_username', return_value='room-b@chatroom'):
        assert validator.validate_group_chat('another-group').ok
        assert validator._usernames['another-group'] == 'room-b@chatroom'
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
