"""Core tests that do not require WeChat to be logged in."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.model_client import ModelClient
from app.prompting import (
    build_compact_system_prompt,
    build_context_messages,
    build_system_prompt,
    fix_self_reference,
    at_mentioned,
    reduce_ellipsis,
    parse_reply,
    resolve_sticker,
    select_fallback_sticker,
)
from app.secretstore import protect, unprotect
from app.wechat_adapter import IncomingMessage


def assert_true(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_secret() -> None:
    value = "sk-arona-test-key-123456"
    encrypted = protect(value)
    assert_true(encrypted.startswith("dpapi:") or encrypted.startswith("obf:"), "key encrypted")
    assert_true(unprotect(encrypted) == value, "key roundtrip")


def test_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryStore(Path(tmp) / "test.db")
        for i in range(25):
            row = memory.add_user("测试会话", f"消息 {i}", sender="老师",
                                  external_id=f"wx:test:{i}")
            assert_true(row is not None, "insert user")
        assert_true(len(memory.recent("测试会话", 20)) == 20, "20-message window")
        assert_true(len(memory.recent("测试会话", 30)) == 25, "full history")
        duplicate = memory.add_user("测试会话", "消息 0", sender="老师",
                                    external_id="wx:test:0")
        assert_true(duplicate is None, "stable id dedupe")
        repeated = memory.add_user("测试会话", "在吗", sender="老师")
        repeated2 = memory.add_user("测试会话", "在吗", sender="老师")
        assert_true(repeated and repeated2 and repeated != repeated2, "repeat text is kept")
        memory.add_assistant("测试会话", "老师，我在。", sticker_name="挥手问候",
                             profile_name="mock:arona-demo", latency_ms=123)
        memory.record_usage("测试会话", "openai", "deepseek-chat", 120, 30, "reply")
        usage = memory.usage_totals()
        assert_true(usage["total"] == 150 and usage["requests"] == 1,
                    "token usage totals")
        replies = memory.recent_replies(5)
        assert_true(replies and replies[0]["sticker_name"] == "挥手问候", "reply query")
        assert_true(memory.context_count("测试会话") == 28, "context count")
        memory.close()


def test_prompt_and_stickers() -> None:
    stickers = StickerStore().enabled()
    assert_true(len(stickers) >= 8, "sample stickers")
    persona = PersonaStore().get("arona")
    system = build_system_prompt(persona, stickers, target_name="文件传输助手",
                                 target_type="friend")
    assert_true("阿洛娜" in system and "sticker:" in system, "persona + sticker prompt")
    compact = build_compact_system_prompt(persona, stickers, target_name="文件传输助手",
                                          max_chars=180)
    assert_true("阿洛娜" in compact and stickers[0]["name"] in compact and "sticker:" in compact,
                "compact persona + sticker prompt")
    assert_true(len(compact) < len(system), "compact prompt is shorter")
    assert_true("绝不能自称" in system and "自称只能用" in compact,
                "self-reference rules present")
    group_system = build_system_prompt(persona, stickers, target_name="测试群",
                                       target_type="group", recipient_name="小明")
    assert_true("小明老师" in group_system and "微信昵称+老师" in group_system,
                "group address rule")
    private_system = build_system_prompt(persona, stickers, target_name="文件传输助手",
                                         target_type="friend", recipient_name="赵军情")
    assert_true("赵军情老师" in private_system,
                "private address rule")
    assert_true(reduce_ellipsis("唔……让我想想。") == "唔，让我想想。",
                "ellipsis is reduced")
    assert_true("…" not in reduce_ellipsis("今天先到这里……"),
                "trailing ellipsis removed")
    assert_true(at_mentioned("@我的昵称 在吗", "我的昵称", ["阿洛娜"]),
                "self nickname mention counts when enabled")
    assert_true(not at_mentioned("@我的昵称 在吗", "", ["阿洛娜"]),
                "self nickname mention is ignored when disabled")
    assert_true(at_mentioned("@阿洛娜 在吗", "", ["阿洛娜"]),
                "persona name mention still counts")
    assert_true(PersonaStore().get("plana").get("name") == "普拉娜",
                "Plana persona exists")
    first_name = stickers[0]["name"]
    text, chosen = parse_reply(f"老师辛苦啦，早点休息。\n[sticker:{first_name}]", stickers)
    assert_true(chosen and chosen["name"] == first_name, "parse sticker tag")
    assert_true("[sticker" not in text, "tag removed from text")
    text2, chosen2 = parse_reply("好耶！[sticker:不存在的表情]", stickers)
    assert_true(chosen2 is None and "sticker" not in text2, "unknown sticker dropped")
    text3, _ = parse_reply("老师好！\n\n（简洁自然，符合中文问候习惯，适用于课堂）", stickers)
    assert_true(text3 == "老师好！", "strip model meta commentary")
    assert_true(fix_self_reference("老师好开心～") == "阿洛娜好开心～",
                "fix self feeling")
    assert_true(fix_self_reference("老师，告诉老师吧。") == "老师，告诉我吧。",
                "fix tell-teacher")
    assert_true(fix_self_reference("老师会一直陪着老师。") == "我会一直陪着老师。",
                "fix self action")
    labelled_reply, _ = parse_reply("[阿洛娜] 老师，告诉老师吧。", stickers)
    assert_true("[阿洛娜]" not in labelled_reply and "告诉我吧" in labelled_reply,
                "strip speaker label and self-address")
    history = [{"role": "user" if i % 2 == 0 else "assistant",
                "content": f"第 {i} 条"} for i in range(30)]
    messages = build_context_messages(history, min_messages=20)
    assert_true(len(messages) >= 20, "context keeps at least 20")
    assert_true(messages[-1]["content"] == "第 29 条", "context newest last")
    manual_history = [
        {"role": "user", "sender": "老师", "content": "旧消息",
         "meta": {"direction": "out", "manual": True}},
        {"role": "user", "sender": "小明", "content": "新消息",
         "meta": {"direction": "in"}},
    ]
    labelled = build_context_messages(manual_history, target_type="group", min_messages=2)
    assert_true("我本人手动发出" in labelled[0]["content"], "manual outgoing is labelled")
    assert_true(labelled[1]["content"].startswith("[小明老师]"),
                "group participants are labelled nickname+老师")
    labelled_friend = build_context_messages([
        {"role": "user", "sender": "赵军情", "content": "你好"},
        {"role": "assistant", "sender": "阿洛娜", "content": "老师，阿洛娜在哦。"},
    ], target_type="friend", min_messages=2)
    assert_true(labelled_friend[0]["content"].startswith("[赵军情老师]"),
                "private participant is labelled nickname+老师")
    assert_true(labelled_friend[1]["content"].startswith("[阿洛娜]"),
                "assistant input is labelled")

    assert_true(IncomingMessage("c", "s", "x", direction="out", attr="friend").is_self,
                "outgoing message is self")
    fallback = select_fallback_sticker(stickers, "老师，我好委屈", "想哭", probability=1.0)
    assert_true(fallback is not None, "fallback sticker")
    partial = first_name[:1]
    assert_true(resolve_sticker(partial, stickers)["name"] == first_name, "partial sticker name")
    annotated_text, annotated_sticker = parse_reply(
        f"嘿嘿，老师好开心～[{first_name}]（同时发送了表情包「{first_name}」）", stickers
    )
    assert_true(annotated_sticker and annotated_sticker["name"] == first_name,
                "bare sticker + Chinese annotation selects sticker")
    assert_true(f"[{first_name}]" not in annotated_text and "同时发送" not in annotated_text,
                "sticker/annotation removed from text")
    assert_true(annotated_text.startswith("嘿嘿，阿洛娜好开心"),
                "self-reference fixed together with sticker")
    bare_text, bare_sticker = parse_reply(f"老师，阿洛娜在哦～[{first_name}]", stickers)
    assert_true(bare_sticker and bare_sticker["name"] == first_name,
                "bare exact sticker name")
    assert_true(f"[{first_name}]" not in bare_text, "bare sticker tag removed")
    plain_text, plain_sticker = parse_reply("老师，这是[图片]，不要动。", stickers)
    assert_true(plain_sticker is None and "[图片]" in plain_text,
                "unknown bracket is preserved")
    long_text, _ = parse_reply(
        "这是第一句非常非常长而且需要被拆开的句子，里面有好多好多内容。第二句也不短。第三句。第四句。",
        stickers, max_sentence_chars=23,
    )
    for sentence in re.split(r"[。！？!?；;]", long_text):
        assert_true(len(sentence.strip()) <= 23, "every sentence <= 23 chars")
    total_text, _ = parse_reply(
        "这是第一句非常长的话，第二句也很长，第三句还是长。", stickers, max_chars=23
    )
    assert_true(len(total_text) <= 23, "entire message <= 23 chars")
    ai_text, _ = parse_reply(
        "作为AI，我理解你的意思。希望这些建议对你有帮助。老师，早点休息。", stickers
    )
    assert_true("作为AI" not in ai_text and "希望这些" not in ai_text,
                "remove AI phrasing")


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict] = []

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "qwen-test:7b"}, {"name": "llama-test:3b"}]})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": "gpt-test"}, {"id": "gpt-mini"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.calls.append({"path": self.path, "payload": payload,
                                  "auth": self.headers.get("Authorization", "")})
        if self.path == "/api/chat":
            self._send(200, {"message": {"role": "assistant", "content": "Ollama 测试回复"},
                             "eval_count": 7, "prompt_eval_count": 11})
        elif self.path == "/v1/chat/completions":
            self._send(200, {"choices": [{"message": {"role": "assistant",
                                                      "content": "API 测试回复"}}],
                             "usage": {"prompt_tokens": 12, "completion_tokens": 5}})
        else:
            self._send(404, {"error": "not found"})


def test_model_clients() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ollama = ModelClient({
            "provider": "ollama", "base_url": f"http://127.0.0.1:{port}",
            "model": "qwen-test:7b", "temperature": 0.5, "max_tokens": 100,
            "timeout": 10, "num_ctx": 4096, "think": False,
        })
        health = ollama.health()
        assert_true(health.ok and "qwen-test:7b" in health.models, "ollama health")
        result = ollama.chat([{"role": "user", "content": "你好"}], "system")
        assert_true(result.text == "Ollama 测试回复", "ollama chat")
        call = FakeHandler.calls[-1]
        assert_true(call["path"] == "/api/chat", "ollama endpoint")
        assert_true(call["payload"].get("think") is False, "ollama think flag")
        assert_true(call["payload"]["options"].get("num_ctx") == 4096, "ollama num_ctx")
        models = ollama.list_models()
        assert_true("llama-test:3b" in models, "ollama list")

        api = ModelClient({
            "provider": "openai", "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "gpt-test", "api_key_enc": protect("sk-test-api-key"),
            "temperature": 0.6, "max_tokens": 120, "timeout": 10,
        })
        health = api.health()
        assert_true(health.ok and "gpt-test" in health.models, "api health")
        result = api.chat([{"role": "user", "content": "hi"}], "system")
        assert_true(result.text == "API 测试回复", "api chat")
        call = FakeHandler.calls[-1]
        assert_true(call["path"] == "/v1/chat/completions", "api endpoint")
        assert_true(call["auth"] == "Bearer sk-test-api-key", "api auth")

        # Vision request shape: OpenAI-style content parts with a data URI.
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            image_path = handle.name
        Image.new("RGB", (32, 32), "white").save(image_path)
        vision = ModelClient({
            "provider": "openai", "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "gpt-vision-test", "api_key_enc": protect("sk-test-api-key"),
            "temperature": 0.6, "max_tokens": 120, "timeout": 10,
            "supports_vision": True,
        })
        vision_result = vision.describe_image(image_path)
        assert_true(vision_result.text == "API 测试回复", "vision description")
        vision_call = FakeHandler.calls[-1]
        parts = vision_call["payload"]["messages"][0]["content"]
        assert_true(isinstance(parts, list) and parts[1]["type"] == "image_url",
                    "vision image payload")
        Path(image_path).unlink(missing_ok=True)

        # OpenAI-compatible gateways may keep an alias working while /models
        # only lists newer names; health must verify the chat endpoint.
        alias = ModelClient({
            "provider": "openai", "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "gpt-alias", "api_key_enc": protect("sk-test-api-key"),
            "temperature": 0.6, "max_tokens": 120, "timeout": 10,
        })
        alias_health = alias.health()
        assert_true(alias_health.ok and "聊天接口已验证" in alias_health.message,
                    "alias health falls back to chat probe")
        probe_call = FakeHandler.calls[-1]
        assert_true(probe_call["path"] == "/v1/chat/completions", "alias probe endpoint")
        assert_true(probe_call["payload"].get("max_tokens") == 1, "alias probe token cap")
    finally:
        server.shutdown()
        server.server_close()


def test_config_crud() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigStore(Path(tmp) / "config.json")
        target = config.add_target("文件传输助手", "friend", "arona")
        assert_true(config.target_by_name("文件传输助手")["id"] == target["id"], "add target")
        config.update_target(target["id"], {"enabled": False})
        assert_true(not config.enabled_targets(), "disable target")
        config.delete_target(target["id"])
        assert_true(not config.targets(), "delete target")
        try:
            config.add_target("", "friend")
        except ValueError:
            pass
        else:
            raise AssertionError("empty target name must fail")
        assert_true(config.bot["context_limit"] >= 20, "min context 20")


if __name__ == "__main__":
    test_secret()
    test_memory()
    test_prompt_and_stickers()
    test_model_clients()
    test_config_crud()
    print("ALL CORE TESTS PASSED")
