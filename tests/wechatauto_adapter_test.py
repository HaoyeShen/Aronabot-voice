"""Unit test for the optional WeChat 4.1.15 backend (no live WeChat needed)."""

from __future__ import annotations

import sys
import time
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

    result = adapter.send_text("测试群", "测试回复")
    assert result.ok, result.message
    assert FakeUIA.last.sent[-1] == "测试回复"

    fake_uia = FakeUIA.last
    adapter._uia = fake_uia
    adapter._set_file_clipboard = lambda paths: True
    adapter._wait_outgoing = lambda *args, **kwargs: True
    sticker = ROOT / "data" / "stickers" / "arona_official_00.png"
    result2 = adapter.send_sticker("测试群", sticker)
    assert result2.ok, result2.message
    assert any("{Ctrl}v" in k for k in fake_uia.control.keys)
    adapter.stop()
    print("WECHATAUTO ADAPTER TEST PASSED", statuses[0][0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
