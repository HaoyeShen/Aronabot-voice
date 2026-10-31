"""End-to-end service test with a fake WeChat transport and the demo model."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.service as service_module
from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.service import BotService
from app.training import TrainingStore
from app.wechat_adapter import BaseWeChatAdapter, IncomingMessage, SendResult


class FakeAdapter(BaseWeChatAdapter):
    instances: list["FakeAdapter"] = []

    def __init__(self, config, on_message, on_status=None) -> None:
        super().__init__(config, on_message, on_status)
        self.sent: list[tuple[str, str]] = []
        self.files: list[str] = []
        self.backend_label = "fake · UI Automation"
        self.self_name = "阿洛娜"
        self.connected = True
        FakeAdapter.instances.append(self)

    def start(self, targets):
        self._targets = {t["name"]: t for t in targets}
        self.connected = True

    def send_text(self, chat, text):
        self.sent.append((chat, text))
        return SendResult(True, "fake send")

    def send_sticker(self, chat, path):
        self.files.append(str(path))
        return SendResult(True, "fake sticker")

    def fetch_recent(self, chat, limit):
        return []

    def probe(self):
        return True, "fake online"

    def stop(self):
        self.connected = False


def wait_event(service: BotService, kind: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event_kind, data = service.events.get(timeout=0.2)
        except Exception:
            continue
        if event_kind == kind:
            return data
    raise AssertionError(f"event {kind} not seen")


def main() -> int:
    original_build = service_module.build_adapter
    service_module.build_adapter = lambda backend, config, on_message, on_status=None: FakeAdapter(
        config, on_message, on_status
    )
    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / "config.json")
        config.set_active_profile("demo-local")
        config.bot.update({"reply_delay_min": 0.0, "reply_delay_max": 0.0,
                           "per_chat_cooldown": 0.0, "global_cooldown": 0.0,
                           "sticker_probability": 1.0, "auto_sticker": True,
                           "context_limit": 30, "auto_reply": True,
                           "group_at_always_reply": True,
                           "group_probability_reply": True,
                           "group_reply_probability": 0.0,
                           "trigger_enabled": False})
        config.add_target("测试会话", "friend", "arona")
        group_target = config.add_target("测试群", "group", "arona", require_at=True)
        group_target["group_reply_probability"] = 0.0
        memory = MemoryStore(Path(tmp) / "test.db")
        # 25 historical turns prove the context window is populated.
        for i in range(25):
            memory.add_user("测试会话", f"历史消息 {i}", sender="老师",
                            external_id=f"wx:test-history:{i}")
            memory.add_assistant("测试会话", f"历史回复 {i}", profile_name="mock")
        training = TrainingStore(Path(tmp) / "training.db", Path(tmp) / "training_files")
        service = BotService(config, PersonaStore(), StickerStore(), memory, training)
        try:
            assert service.start(), service.state().get("last_error")
            adapter = FakeAdapter.instances[-1]
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="老师", content="阿洛娜，在吗？",
                msg_type="text", external_id="wx:test-new-1", attr="friend",
            ))
            reply_event = wait_event(service, "reply")
            assert reply_event["chat"] == "测试会话"
            assert adapter.sent, "reply text should be sent"
            assert "阿洛娜" in adapter.sent[-1][1]
            assert reply_event["context_count"] >= 20, "at least 20 context messages"
            assert service.memory.context_count("测试会话") >= 25

            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="老师", content="今天好累啊……",
                msg_type="text", external_id="wx:test-new-2", attr="friend",
            ))
            second = wait_event(service, "reply")
            assert second["sticker"], "mock tired reply should include a sticker"
            assert adapter.files, "sticker file should be sent through adapter"

            bot_text = adapter.sent[-1][1]
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content="自己的消息",
                msg_type="text", external_id="wx:test-self", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2, "self messages must be ignored"
            manual_count = service.memory.context_count("测试会话")
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content="这是老师手动补充的一句",
                msg_type="text", external_id="wx:test-manual", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2, "manually sent messages must not be answered"
            assert service.memory.context_count("测试会话") == manual_count + 1, \
                "manual outgoing message is stored as context"
            latest = service.memory.recent("测试会话", 1)[0]
            assert latest["sender"] == "测试账号" and latest["meta"]["manual"] is True
            # The UI echo of ARONA's own successful send must not be stored twice.
            echo_count = service.memory.context_count("测试会话")
            adapter.on_message(IncomingMessage(
                chat="测试会话", sender="测试账号", content=bot_text,
                msg_type="text", external_id="wx:test-echo", attr="self",
                direction="out", origin="self",
            ))
            time.sleep(0.3)
            assert len(adapter.sent) == 2
            assert service.memory.context_count("测试会话") == echo_count, \
                "bot echo must not create duplicate context"

            # Whitelist and group @ filtering.
            adapter.on_message(IncomingMessage(
                chat="不在白名单", sender="陌生人", content="@阿洛娜 在吗",
                msg_type="text", external_id="wx:test-other", attr="friend",
            ))
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="大家早上好",
                msg_type="text", external_id="wx:test-group-1", attr="friend",
                chat_type="group",
            ))
            time.sleep(0.5)
            assert len(adapter.sent) == 2, "whitelist and group without @ must not reply"
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="@阿洛娜 今天天气真好",
                msg_type="text", external_id="wx:test-group-2", attr="friend",
                chat_type="group",
            ))
            group_reply = wait_event(service, "reply")
            assert group_reply["chat"] == "测试群"
            assert len(adapter.sent) == 3

            # With probability at 100%, a non-@ group message replies too.
            config.bot["group_reply_probability"] = 1.0
            group_target["group_reply_probability"] = 1.0
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="随便聊聊",
                msg_type="text", external_id="wx:test-group-3", attr="friend",
                chat_type="group",
            ))
            probability_reply = wait_event(service, "reply")
            assert probability_reply["chat"] == "测试群"
            assert len(adapter.sent) == 4

            # An @ still must get a reply even when the legacy @-always toggle
            # is off; the requirement is stronger than the old probability rule.
            config.bot["group_at_always_reply"] = False
            config.bot["group_reply_probability"] = 0.0
            group_target["group_reply_probability"] = 0.0
            adapter.on_message(IncomingMessage(
                chat="测试群", sender="群友", content="@阿洛娜 在吗",
                msg_type="text", external_id="wx:test-group-4", attr="friend",
                chat_type="group",
            ))
            time.sleep(0.4)
            assert len(adapter.sent) == 5, "@ must always get a reply"
        finally:
            service.stop()
            service.memory.close()
            training.close()
    service_module.build_adapter = original_build
    print("BOT SERVICE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
