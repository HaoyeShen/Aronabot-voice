"""Editable voice presets used by the WecVoice-auto sender."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from .paths import DATA

log = logging.getLogger(__name__)

VOICE_FILE = DATA / "voice_presets.json"

BUILTIN_PRESETS = [
    {
        "id": "arona",
        "label": "阿洛娜（近似音）",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": "+2%",
        "pitch": "+22Hz",
        "volume": "+0%",
        "description": "明亮轻盈、略带上扬的中文少女近似音；非角色原声。",
        "builtin": True,
    },
    {
        "id": "plana",
        "label": "普拉娜（近似音）",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "-8%",
        "pitch": "-6Hz",
        "volume": "-5%",
        "description": "平静克制、稍慢轻柔的中文近似音；非角色原声。",
        "builtin": True,
    },
]


class VoicePresetStore:
    """Built-ins plus user-created presets stored in data/voice_presets.json."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else VOICE_FILE
        self._lock = threading.RLock()
        self.items: list[dict] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            items = []
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        raw = raw.get("presets", [])
                    if isinstance(raw, list):
                        items = [item for item in raw if isinstance(item, dict)]
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    log.warning("读取音色配置失败：%s", exc)
            by_id = {str(item.get("id") or ""): item for item in items}
            merged = []
            for builtin in BUILTIN_PRESETS:
                current = by_id.pop(builtin["id"], builtin)
                # Upgrade untouched old defaults, preserving edited/custom voices.
                old = (("zh-CN-XiaoyiNeural", "+5%", "+12Hz", "+0%")
                       if builtin["id"] == "arona" else
                       ("zh-CN-XiaoxiaoNeural", "-5%", "+0Hz", "+0%"))
                if tuple(current.get(k) for k in ("voice", "rate", "pitch", "volume")) == old:
                    current = {**current, **builtin}
                current = {**builtin, **current, "builtin": True}
                merged.append(current)
            for item in items:
                if str(item.get("id") or "") in {b["id"] for b in BUILTIN_PRESETS}:
                    continue
                if item.get("label") and item.get("voice"):
                    merged.append({**item, "builtin": False})
            self.items = merged
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"presets": self.items}, ensure_ascii=False,
                                          indent=2) + "\n", encoding="utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                log.warning("保存音色配置失败：%s", exc)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.items]

    def get(self, preset_id: str | None) -> dict:
        with self._lock:
            for item in self.items:
                if item.get("id") == preset_id:
                    return dict(item)
            return dict(self.items[0])

    def upsert(self, preset: dict) -> dict:
        with self._lock:
            current = next((item for item in self.items
                            if item.get("id") == preset.get("id")), None)
            if current is None:
                preset = dict(preset)
                preset["id"] = preset.get("id") or uuid.uuid4().hex[:10]
                preset["builtin"] = False
                self.items.append(preset)
                result = preset
            else:
                current.update(preset)
                result = current
            self.save()
            return dict(result)

    def delete(self, preset_id: str) -> bool:
        with self._lock:
            item = next((item for item in self.items if item.get("id") == preset_id), None)
            if item is None or item.get("builtin"):
                return False
            self.items = [value for value in self.items if value.get("id") != preset_id]
            self.save()
            return True
