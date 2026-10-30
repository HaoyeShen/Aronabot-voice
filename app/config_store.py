"""JSON-backed configuration, persona, and sticker stores."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .defaults import ARONA_PERSONA, PLANA_PERSONA, DEFAULT_CONFIG, default_stickers
from .paths import CONFIG_FILE, PERSONAS_FILE, STICKERS_FILE, STICKER_DIR, ROOT, ensure_dirs

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _backup_corrupt(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.broken-{stamp}{path.suffix}")
    try:
        path.replace(target)
        log.warning("配置文件损坏，已备份到 %s", target)
    except OSError:
        pass


def deep_merge(base: dict, override: dict) -> dict:
    """Merge override into a copy of base, ignoring unknown/removed structures."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE) -> None:
        ensure_dirs()
        self.path = path
        self._lock = threading.RLock()
        self.data: dict = {}
        self.load()

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                self.data = copy.deepcopy(DEFAULT_CONFIG)
                self._normalise()
                self.save()
                return self.data
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if not isinstance(raw, dict):
                    raise ValueError("config root is not an object")
                self.data = deep_merge(DEFAULT_CONFIG, raw)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.error("读取配置失败：%s", exc)
                _backup_corrupt(self.path)
                self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._normalise()
            return self.data

    def _normalise(self) -> None:
        data = self.data
        for section in ("bot", "admin", "wechat", "training", "ui", "privacy"):
            if not isinstance(data.get(section), dict):
                data[section] = copy.deepcopy(DEFAULT_CONFIG[section])
        admin = data.setdefault("admin", {})
        admin["password"] = str(admin.get("password") or "arona-admin")
        allowed = admin.get("allowed")
        if isinstance(allowed, str):
            allowed = [line.strip() for line in allowed.splitlines() if line.strip()]
        if not isinstance(allowed, list):
            allowed = ["__self__"]
        admin["allowed"] = [str(item).strip() for item in allowed if str(item).strip()] or ["__self__"]

        news = data.setdefault("news", {})
        news["enabled"] = bool(news.get("enabled", False))
        news["send_time"] = str(news.get("send_time") or "06:50")
        try:
            _hour, _minute = news["send_time"].split(":", 1)
            if not (0 <= int(_hour) <= 23 and 0 <= int(_minute) <= 59):
                raise ValueError
        except (TypeError, ValueError):
            news["send_time"] = "06:50"
        news["target_chat"] = str(news.get("target_chat") or "").strip()
        try:
            news["max_items"] = max(1, min(3, int(news.get("max_items", 2) or 2)))
        except (TypeError, ValueError):
            news["max_items"] = 2
        try:
            news["timeout"] = max(5.0, min(20.0, float(news.get("timeout", 10) or 10)))
        except (TypeError, ValueError):
            news["timeout"] = 10
        news["use_model_summary"] = bool(news.get("use_model_summary", True))
        try:
            admin["off_seconds"] = max(10, min(86400, int(admin.get("off_seconds", 3600))))
        except (TypeError, ValueError):
            admin["off_seconds"] = 3600
        bot = data.setdefault("bot", {})
        bot["auto_start"] = bool(bot.get("auto_start", True))
        bot["group_at_always_reply"] = bool(bot.get("group_at_always_reply", True))
        bot["group_probability_reply"] = bool(bot.get("group_probability_reply", True))
        try:
            group_probability = float(bot.get("group_reply_probability", 0.3))
        except (TypeError, ValueError):
            group_probability = 0.3
        bot["group_reply_probability"] = max(0.0, min(1.0, group_probability))
        bot["group_at_include_self_name"] = bool(bot.get("group_at_include_self_name", True))
        try:
            bot["stale_reply_minutes"] = max(1, min(1440, int(bot.get("stale_reply_minutes", 10))))
        except (TypeError, ValueError):
            bot["stale_reply_minutes"] = 10
        bot["quote_reply"] = bool(bot.get("quote_reply", False))
        bot["voice_enabled"] = bool(bot.get("voice_enabled", False))
        try:
            bot["voice_probability"] = max(0.0, min(1.0, float(bot.get("voice_probability", 0.5))))
        except (TypeError, ValueError):
            bot["voice_probability"] = 0.5
        bot["voice_name"] = str(bot.get("voice_name") or "arona")
        bot["replace_punctuation"] = bool(bot.get("replace_punctuation", False))
        bot["trigger_enabled"] = bool(bot.get("trigger_enabled", True))
        keywords = bot.get("trigger_keywords")
        if isinstance(keywords, str):
            keywords = [line.strip() for line in keywords.replace("，", ",").split(",") if line.strip()]
        if not isinstance(keywords, list):
            keywords = ["阿洛娜", "Arona", "ARONA"]
        bot["trigger_keywords"] = [str(k).strip() for k in keywords if str(k).strip()] or ["阿洛娜"]
        bot["context_summary_enabled"] = bool(bot.get("context_summary_enabled", True))
        for key_name, default_value, low, high in (
            ("context_summary_threshold_chars", 6000, 2000, 50000),
            ("context_summary_keep_messages", 12, 4, 60),
            ("context_summary_interval_seconds", 300, 30, 86400),
            ("context_summary_max_chars", 500, 100, 2000),
        ):
            try:
                value = int(bot.get(key_name, default_value))
            except (TypeError, ValueError):
                value = default_value
            bot[key_name] = max(low, min(high, value))
        try:
            bot["context_limit"] = max(20, min(100, int(bot.get("context_limit", 30))))
        except (TypeError, ValueError):
            bot["context_limit"] = 30
        for key, default, lo, hi in (
            ("max_reply_chars", 23, 8, 1800),
            ("max_sentence_chars", 23, 8, 80),
            ("sticker_probability", 0.35, 0.0, 1.0),
            ("reply_delay_min", 1.0, 0.0, 30.0),
            ("reply_delay_max", 2.8, 0.0, 30.0),
            ("per_chat_cooldown", 6.0, 0.0, 600.0),
            ("global_cooldown", 1.5, 0.0, 120.0),
            ("max_stickers_per_reply", 1, 0, 3),
            ("max_consecutive_replies", 6, 1, 999),
        ):
            try:
                bot[key] = max(lo, min(hi, float(bot.get(key, default)) if isinstance(default, float)
                                       else int(bot.get(key, default))))
            except (TypeError, ValueError):
                bot[key] = default
        if bot["reply_delay_max"] < bot["reply_delay_min"]:
            bot["reply_delay_max"] = bot["reply_delay_min"]
        if not isinstance(data.get("model_profiles"), list) or not data["model_profiles"]:
            data["model_profiles"] = copy.deepcopy(DEFAULT_CONFIG["model_profiles"])
        ids = {p.get("id") for p in data["model_profiles"] if isinstance(p, dict)}
        if data.get("active_profile_id") not in ids:
            data["active_profile_id"] = data["model_profiles"][0].get("id")
        if not isinstance(data.get("targets"), list):
            data["targets"] = []
        seen: set[str] = set()
        clean_targets = []
        for t in data["targets"]:
            if not isinstance(t, dict) or not str(t.get("name", "")).strip():
                continue
            name = str(t["name"]).strip()
            if name in seen:
                continue
            seen.add(name)
            try:
                target_context = int(t.get("context_limit") or bot["context_limit"])
            except (TypeError, ValueError):
                target_context = int(bot["context_limit"])
            try:
                target_probability = float(t.get("reply_probability", 1.0))
            except (TypeError, ValueError):
                target_probability = 1.0
            try:
                target_group_probability = float(t.get(
                    "group_reply_probability",
                    bot.get("group_reply_probability", 0.3),
                ))
            except (TypeError, ValueError):
                target_group_probability = float(bot.get("group_reply_probability", 0.3))
            try:
                news_time = str(t.get("news_time") or "06:50")
                _nh, _nm = news_time.split(":", 1)
                if not (0 <= int(_nh) <= 23 and 0 <= int(_nm) <= 59):
                    raise ValueError
            except (TypeError, ValueError):
                news_time = "06:50"
            clean_targets.append({
                "id": t.get("id") or uuid.uuid4().hex[:12],
                "name": name,
                "type": t.get("type", "friend") if t.get("type") in ("friend", "group") else "friend",
                "enabled": bool(t.get("enabled", True)),
                "persona_id": t.get("persona_id") or data.get("active_persona_id", "arona"),
                "require_at": bool(t.get("require_at", True)),
                "context_limit": max(20, min(100, target_context)),
                "reply_probability": max(0.0, min(1.0, target_probability)),
                "group_reply_probability": max(0.0, min(1.0, target_group_probability)),
                "news_enabled": bool(t.get("news_enabled", False)),
                "news_time": news_time,
                "note": str(t.get("note", "")),
                "added_at": t.get("added_at") or _now(),
            })
        data["targets"] = clean_targets
        # Migrate the old single global news target to the new per-group switch
        # once, without overriding groups the user already configured.
        legacy_news = data.setdefault("news", {})
        if any(t.get("news_enabled") for t in clean_targets):
            legacy_news["per_target"] = True
            legacy_news["enabled"] = False
        if (legacy_news.get("enabled") and legacy_news.get("target_chat")
                and not any(t.get("news_enabled") for t in clean_targets)):
            for target in clean_targets:
                if target["name"] == str(legacy_news.get("target_chat")):
                    target["news_enabled"] = True
                    target["news_time"] = str(legacy_news.get("send_time") or "06:50")
                    legacy_news["per_target"] = True
                    legacy_news["enabled"] = False
                    break
        data["wechat"]["backend"] = data["wechat"].get("backend", "auto")
        if data["wechat"]["backend"] not in ("auto", "wechatauto"):
            data["wechat"]["backend"] = "auto"
        try:
            data["wechat"]["poll_interval"] = max(
                2.0, min(30.0, float(data["wechat"].get("poll_interval", 2.5)))
            )
        except (TypeError, ValueError):
            data["wechat"]["poll_interval"] = 2.5
        training = data["training"]
        training["enabled"] = bool(training.get("enabled", True))
        try:
            training["top_k"] = max(1, min(8, int(training.get("top_k", 3))))
        except (TypeError, ValueError):
            training["top_k"] = 3
        try:
            training["max_chars"] = max(400, min(6000, int(training.get("max_chars", 1800))))
        except (TypeError, ValueError):
            training["max_chars"] = 1800
        data["privacy"]["screenshots"] = False
        data["privacy"]["screen_recording"] = False
        data["privacy"]["mode"] = "uiautomation_only"

    def save(self) -> None:
        with self._lock:
            self._normalise()
            _atomic_write_json(self.path, self.data)

    def reload(self) -> dict:
        return self.load()

    @property
    def bot(self) -> dict:
        return self.data["bot"]

    @property
    def wechat(self) -> dict:
        return self.data["wechat"]

    @property
    def training(self) -> dict:
        return self.data["training"]

    @property
    def ui(self) -> dict:
        return self.data["ui"]

    def active_profile(self) -> dict:
        pid = self.data.get("active_profile_id")
        for profile in self.data.get("model_profiles", []):
            if profile.get("id") == pid:
                return profile
        return self.data["model_profiles"][0]

    def profile_by_id(self, profile_id: str) -> dict | None:
        return next((p for p in self.data.get("model_profiles", [])
                     if p.get("id") == profile_id), None)

    def upsert_profile(self, profile: dict) -> dict:
        with self._lock:
            profiles = self.data.setdefault("model_profiles", [])
            existing = next((p for p in profiles if p.get("id") == profile.get("id")), None)
            if existing:
                existing.update(profile)
            else:
                profile.setdefault("id", uuid.uuid4().hex[:12])
                profiles.append(profile)
            self.save()
            return profile

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            profiles = self.data.get("model_profiles", [])
            if len(profiles) <= 1:
                return False
            keep = [p for p in profiles if p.get("id") != profile_id]
            if len(keep) == len(profiles):
                return False
            self.data["model_profiles"] = keep
            if self.data.get("active_profile_id") == profile_id:
                self.data["active_profile_id"] = keep[0]["id"]
            self.save()
            return True

    def set_active_profile(self, profile_id: str) -> None:
        if self.profile_by_id(profile_id):
            self.data["active_profile_id"] = profile_id
            self.save()

    def targets(self) -> list[dict]:
        return self.data.get("targets", [])

    def enabled_targets(self) -> list[dict]:
        return [t for t in self.targets() if t.get("enabled")]

    def target_by_name(self, name: str) -> dict | None:
        return next((t for t in self.targets() if t.get("name") == name), None)

    def add_target(self, name: str, kind: str = "friend", persona_id: str = "arona",
                   require_at: bool = True, note: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("监听对象名称不能为空")
        with self._lock:
            if self.target_by_name(name):
                raise ValueError("该监听对象已存在")
            target = {
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "type": kind,
                "enabled": True,
                "persona_id": persona_id,
                "require_at": require_at,
                "context_limit": self.bot.get("context_limit", 30),
                "reply_probability": 1.0,
                "note": note,
                "added_at": _now(),
            }
            self.data["targets"].append(target)
            self.save()
            # save() normalises targets into fresh dicts; return the live one.
            return self.target_by_name(name) or target

    def update_target(self, target_id: str, patch: dict) -> None:
        for target in self.data.get("targets", []):
            if target.get("id") == target_id:
                target.update(patch)
                target["name"] = str(target.get("name", "")).strip()
                self.save()
                return
        raise KeyError("监听对象不存在")

    def delete_target(self, target_id: str) -> bool:
        with self._lock:
            before = len(self.targets())
            self.data["targets"] = [t for t in self.targets() if t.get("id") != target_id]
            if len(self.data["targets"]) == before:
                return False
            self.save()
            return True

    def export_public(self) -> dict:
        """Configuration without encrypted API keys (for backups/diagnostics)."""
        data = copy.deepcopy(self.data)
        for profile in data.get("model_profiles", []):
            profile["api_key_enc"] = "***" if profile.get("api_key_enc") else ""
        if isinstance(data.get("admin"), dict):
            data["admin"]["password"] = "***"
        return data


class PersonaStore:
    def __init__(self, path: Path = PERSONAS_FILE) -> None:
        ensure_dirs()
        self.path = path
        self._lock = threading.RLock()
        self.items: list[dict] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.items = copy.deepcopy([ARONA_PERSONA, PLANA_PERSONA])
                self.save()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    raw = raw.get("personas", [])
                if not isinstance(raw, list) or not raw:
                    raise ValueError("persona list is empty")
                cleaned = []
                for item in raw:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    item.setdefault("id", uuid.uuid4().hex[:12])
                    item.setdefault("title", "自定义角色")
                    item.setdefault("description", "")
                    item.setdefault("tone", "")
                    item.setdefault("speech_style", "")
                    item.setdefault("system_prompt", "")
                    item.setdefault("boundaries", "")
                    item.setdefault("sticker_guidance", "")
                    item.setdefault("catchphrases", [])
                    item.setdefault("examples", [])
                    item.setdefault("builtin", False)
                    item.setdefault("created_at", _now())
                    item.setdefault("updated_at", item["created_at"])
                    cleaned.append(item)
                self.items = cleaned or copy.deepcopy([ARONA_PERSONA, PLANA_PERSONA])
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.error("读取人格配置失败：%s", exc)
                _backup_corrupt(self.path)
                self.items = copy.deepcopy([ARONA_PERSONA, PLANA_PERSONA])
            self.save()

    def save(self) -> None:
        with self._lock:
            _atomic_write_json(self.path, self.items)

    def list(self) -> list[dict]:
        return copy.deepcopy(self.items)

    def get(self, persona_id: str | None) -> dict:
        with self._lock:
            for item in self.items:
                if item.get("id") == persona_id:
                    return copy.deepcopy(item)
            return copy.deepcopy(self.items[0])

    def upsert(self, persona: dict) -> dict:
        with self._lock:
            existing = next((p for p in self.items if p.get("id") == persona.get("id")), None)
            if existing:
                persona["updated_at"] = _now()
                existing.update(persona)
                result = existing
            else:
                persona["id"] = persona.get("id") or uuid.uuid4().hex[:12]
                persona["created_at"] = persona.get("created_at") or _now()
                persona["updated_at"] = _now()
                persona.setdefault("builtin", False)
                self.items.append(persona)
                result = persona
            self.save()
            return copy.deepcopy(result)

    def delete(self, persona_id: str) -> bool:
        with self._lock:
            if len(self.items) <= 1:
                return False
            keep = [p for p in self.items if p.get("id") != persona_id]
            if len(keep) == len(self.items):
                return False
            self.items = keep
            self.save()
            return True

    def reset_builtin(self, persona_id: str) -> dict:
        source = PLANA_PERSONA if persona_id == "plana" else ARONA_PERSONA
        return self.upsert(copy.deepcopy(source))

    def reset_arona(self) -> dict:
        return self.upsert(copy.deepcopy(ARONA_PERSONA))


class StickerStore:
    def __init__(self, path: Path = STICKERS_FILE) -> None:
        ensure_dirs()
        self.path = path
        self._lock = threading.RLock()
        self.items: list[dict] = []
        self.load()

    def _resolve(self, file: str) -> Path:
        p = Path(file)
        return p if p.is_absolute() else ROOT / p

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.items = default_stickers()
                self.save()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    raw = raw.get("stickers", [])
                if not isinstance(raw, list):
                    raise ValueError("sticker list is invalid")
                cleaned = []
                for item in raw:
                    if not isinstance(item, dict) or not item.get("name") or not item.get("file"):
                        continue
                    item.setdefault("id", Path(str(item["file"])).stem or uuid.uuid4().hex[:8])
                    item.setdefault("tags", [])
                    item.setdefault("description", "")
                    item.setdefault("emotion", "开心")
                    item.setdefault("enabled", True)
                    item.setdefault("builtin", False)
                    item.setdefault("created_at", _now())
                    item.setdefault("updated_at", item["created_at"])
                    if isinstance(item.get("tags"), str):
                        item["tags"] = [s.strip() for s in re.split(r"[,，、\s]+", item["tags"])
                                        if s.strip()]
                    cleaned.append(item)
                self.items = cleaned
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.error("读取表情包索引失败：%s", exc)
                _backup_corrupt(self.path)
                self.items = default_stickers()
            self.save()

    def save(self) -> None:
        with self._lock:
            _atomic_write_json(self.path, self.items)

    def list(self) -> list[dict]:
        return copy.deepcopy(self.items)

    def enabled(self) -> list[dict]:
        return [copy.deepcopy(s) for s in self.items
                if s.get("enabled") and self._resolve(s.get("file", "")).is_file()]

    def get(self, sticker_id: str) -> dict | None:
        item = next((s for s in self.items if s.get("id") == sticker_id), None)
        return copy.deepcopy(item) if item else None

    def find_by_name(self, name: str) -> dict | None:
        candidate = (name or "").strip().strip("[]【】")
        if not candidate:
            return None
        for item in self.items:
            if item.get("name") == candidate or item.get("id") == candidate:
                return copy.deepcopy(item)
        for item in self.items:
            if candidate.lower() in str(item.get("name", "")).lower():
                return copy.deepcopy(item)
        return None

    def path_for(self, item: dict) -> Path:
        return self._resolve(str(item.get("file", "")))

    def add_from_file(self, source: str | Path, name: str, emotion: str = "开心",
                      tags: Iterable[str] = (), description: str = "") -> dict:
        src = Path(source)
        if not src.is_file():
            raise ValueError("表情包文件不存在")
        if src.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("仅支持 PNG / JPG / WEBP / GIF / BMP 图片")
        name = name.strip()
        if not name:
            raise ValueError("表情包名称不能为空")
        with self._lock:
            if any(s.get("name") == name for s in self.items):
                raise ValueError("已经存在同名表情包")
            sticker_id = f"custom_{uuid.uuid4().hex[:10]}"
            suffix = src.suffix.lower()
            dest = STICKER_DIR / f"{sticker_id}{suffix}"
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            item = {
                "id": sticker_id,
                "name": name,
                "file": str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest),
                "tags": [str(t).strip() for t in tags if str(t).strip()],
                "description": description.strip(),
                "emotion": emotion.strip() or "开心",
                "enabled": True,
                "builtin": False,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self.items.append(item)
            self.save()
            return copy.deepcopy(item)

    def update(self, sticker_id: str, patch: dict, new_file: str | Path | None = None) -> dict:
        with self._lock:
            item = next((s for s in self.items if s.get("id") == sticker_id), None)
            if not item:
                raise KeyError("表情包不存在")
            if patch.get("name"):
                duplicate = next((s for s in self.items
                                  if s.get("id") != sticker_id and s.get("name") == patch["name"]), None)
                if duplicate:
                    raise ValueError("已经存在同名表情包")
            if new_file:
                src = Path(new_file)
                if not src.is_file():
                    raise ValueError("表情包文件不存在")
                if src.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise ValueError("仅支持 PNG / JPG / WEBP / GIF / BMP 图片")
                old_path = self.path_for(item)
                dest = STICKER_DIR / f"{sticker_id}{src.suffix.lower()}"
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                if old_path.exists() and old_path.resolve() != dest.resolve() and old_path.is_relative_to(STICKER_DIR):
                    try:
                        old_path.unlink()
                    except OSError:
                        log.warning("无法删除旧表情包文件：%s", old_path)
                item["file"] = str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest)
            for key in ("name", "emotion", "description", "enabled"):
                if key in patch:
                    item[key] = patch[key]
            if "tags" in patch:
                tags = patch["tags"]
                if isinstance(tags, str):
                    tags = [s.strip() for s in re.split(r"[,，、]+", tags) if s.strip()]
                item["tags"] = [str(t).strip() for t in tags if str(t).strip()]
            item["updated_at"] = _now()
            self.save()
            return copy.deepcopy(item)

    def delete(self, sticker_id: str) -> bool:
        with self._lock:
            item = next((s for s in self.items if s.get("id") == sticker_id), None)
            if not item:
                return False
            self.items = [s for s in self.items if s.get("id") != sticker_id]
            file_path = self.path_for(item)
            try:
                if file_path.is_file() and file_path.is_relative_to(STICKER_DIR):
                    file_path.unlink()
            except OSError:
                log.warning("表情包索引已删除，但文件删除失败：%s", file_path)
            self.save()
            return True

    def scan_folder(self) -> int:
        """Add image files that exist on disk but are missing from the index."""
        known = {self.path_for(item).resolve() for item in self.items}
        added = 0
        for path in STICKER_DIR.iterdir():
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if path.resolve() in known:
                continue
            name = path.stem
            sticker_id = f"scan_{uuid.uuid4().hex[:10]}"
            self.items.append({
                "id": sticker_id,
                "name": name,
                "file": str(path.relative_to(ROOT)),
                "tags": [],
                "description": "从表情包目录自动发现。",
                "emotion": "开心",
                "enabled": True,
                "builtin": False,
                "created_at": _now(),
                "updated_at": _now(),
            })
            added += 1
        if added:
            self.save()
        return added
