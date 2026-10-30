"""Tests for local document training, retrieval, and prompt integration."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config_store import ConfigStore, PersonaStore, StickerStore
from app.memory import MemoryStore
from app.prompting import build_compact_system_prompt, build_training_block
from app.service import BotService
from app.training import TrainingStore, chunk_text


def assert_true(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        store = TrainingStore(base / "training.db", base / "files")
        source = base / "lore.txt"
        source.write_text(
            "阿洛娜是什亭之匣的主控AI，也是老师的导航员。\n\n"
            "老师最喜欢的甜点是草莓蛋糕。\n\n"
            "阿洛娜会称呼对方为老师。",
            encoding="utf-8",
        )
        first = store.add_file(source)
        assert_true(first.get("ok") and not first.get("duplicate"), "add document")
        duplicate = store.add_file(source)
        assert_true(duplicate.get("ok") and duplicate.get("duplicate"), "dedupe document")
        stats = store.stats()
        assert_true(stats["docs"] == 1 and stats["chunks"] >= 1, "stats")
        hits = store.search("阿洛娜喜欢什么", limit=3, max_chars=1000)
        assert_true(hits and "阿洛娜" in hits[0]["content"], "retrieval hit")
        no_hits = store.search("在吗", limit=3)
        assert_true(not no_hits, "short generic query does not inject noise")
        block = build_training_block(hits)
        assert_true("草莓蛋糕" in block and "你记得的事" in block, "training block")

        personas = PersonaStore()
        persona = personas.get("arona")
        compact = build_compact_system_prompt(persona, [], training_block=block)
        assert_true("草莓蛋糕" in compact, "compact prompt includes training block")

        # Chunking should split a long document and retain an overlap.
        pieces = chunk_text("句子。" * 500, size=200, overlap=40)
        assert_true(len(pieces) > 1 and all(pieces), "long document chunking")

        # Inject through the real service retrieval path.
        config = ConfigStore(base / "config.json")
        config.data["training"].update({"enabled": True, "top_k": 3, "max_chars": 1200})
        config.save()
        memory = MemoryStore(base / "memory.db")
        service = BotService(config, PersonaStore(), StickerStore(), memory, store)
        context, count, _ = service._training_context("阿洛娜喜欢什么")
        assert_true(count >= 1 and "草莓蛋糕" in context, "service training context")
        config.set_active_profile("demo-local")
        debug = service.debug_chat("阿洛娜喜欢什么", persist=False)
        assert_true(debug["training_chunks"] >= 1, "debug chat retrieves training")
        config.data["training"]["enabled"] = False
        context2, count2, _ = service._training_context("阿洛娜喜欢什么")
        assert_true(not context2 and count2 == 0, "training can be disabled")
        memory.close()

        assert_true(store.delete_document(first["doc_id"]), "delete document")
        assert_true(store.stats()["docs"] == 0, "document removed")
        store.close()

    print("TRAINING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
