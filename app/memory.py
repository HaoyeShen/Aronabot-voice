"""Local SQLite conversation memory.

The database is local-only.  It stores enough history to give the model a
consistent 20+ message context window, plus the data shown on the monitor page.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import DATABASE, ensure_dirs

log = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, path: Path = DATABASE) -> None:
        ensure_dirs()
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                sender TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                msg_type TEXT NOT NULL DEFAULT 'text',
                sticker_name TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                profile_name TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                created_iso TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat, id DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external
                ON messages(chat, external_id) WHERE external_id <> '';
            CREATE TABLE IF NOT EXISTS seen (
                fingerprint TEXT PRIMARY KEY,
                chat TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_summaries (
                chat TEXT PRIMARY KEY,
                summary TEXT NOT NULL DEFAULT '',
                covered_id INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                updated_iso TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'reply',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                created_iso TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_token_usage_created
                ON token_usage(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_token_usage_model
                ON token_usage(model, created_at DESC);
            """
        )
        self._conn.commit()

    @staticmethod
    def _iso(ts: float | None = None) -> str:
        return datetime.fromtimestamp(ts or time.time()).isoformat(timespec="seconds")

    @staticmethod
    def fingerprint(chat: str, sender: str, content: str, msg_type: str,
                    external_id: str = "") -> str:
        raw = external_id or f"{chat}\x1f{sender}\x1f{msg_type}\x1f{content}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()

    def seen(self, fingerprint: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM seen WHERE fingerprint=?", (fingerprint,)).fetchone()
            return row is not None

    def mark_seen(self, fingerprint: str, chat: str) -> bool:
        """Return True if newly marked, False if it was already seen."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO seen(fingerprint, chat, ts) VALUES (?,?,?)",
                (fingerprint, chat, time.time()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def add_user(self, chat: str, content: str, sender: str = "", msg_type: str = "text",
                 external_id: str = "", meta: dict | None = None) -> int | None:
        created = time.time()
        # Only stable, sender-provided IDs are used for dedupe.  Content-only
        # fallback IDs must not suppress a repeated "在吗" forever.
        stable_id = external_id if external_id.startswith("wx") else ""
        fp = self.fingerprint(chat, sender, content, msg_type, stable_id)
        with self._lock:
            if stable_id:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO seen(fingerprint, chat, ts) VALUES (?,?,?)",
                    (fp, chat, created),
                )
                if cur.rowcount == 0:
                    return None
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO messages
                   (chat, role, sender, content, msg_type, external_id, meta, created_at, created_iso)
                   VALUES (?, 'user', ?, ?, ?, ?, ?, ?, ?)""",
                (chat, sender, content, msg_type, stable_id,
                 json.dumps(meta or {}, ensure_ascii=False), created, self._iso(created)),
            )
            self._conn.commit()
            return int(cur.lastrowid) if cur.rowcount else None

    def add_outgoing(self, chat: str, content: str, sender: str = "老师（本人手动）",
                     msg_type: str = "text", external_id: str = "",
                     timestamp: float | None = None,
                     origin: str = "manual") -> int | None:
        """Store a message sent by the user's own WeChat account.

        It is deliberately stored as a *user* turn with an explicit metadata
        marker so the model understands that it is the teacher speaking
        manually, not an ARONA reply.  Outgoing messages never trigger a reply.
        """
        created = timestamp or time.time()
        stable_id = external_id if external_id.startswith("wx") else f"out:{uuid.uuid4().hex}"
        fp = self.fingerprint(chat, sender, content, msg_type,
                              external_id if external_id.startswith("wx") else stable_id)
        meta = {"direction": "out", "manual": origin in ("manual", "self"), "origin": origin}
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO seen(fingerprint, chat, ts) VALUES (?,?,?)",
                (fp, chat, created),
            )
            if cur.rowcount == 0:
                return None
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO messages
                   (chat, role, sender, content, msg_type, external_id, meta,
                    created_at, created_iso)
                   VALUES (?, 'user', ?, ?, ?, ?, ?, ?, ?)""",
                (chat, sender or "老师（本人手动）", content, msg_type, stable_id,
                 json.dumps(meta, ensure_ascii=False), created, self._iso(created)),
            )
            self._conn.commit()
            return int(cur.lastrowid) if cur.rowcount else None

    def add_imported_assistant(self, chat: str, content: str, sender: str = "",
                               msg_type: str = "text", external_id: str = "",
                               timestamp: float | None = None) -> int | None:
        """Store a message the user sent from their own WeChat client."""
        created = timestamp or time.time()
        stable_id = external_id if external_id.startswith("wx") else f"import:{uuid.uuid4().hex}"
        fp = self.fingerprint(chat, sender, content, msg_type,
                              external_id if external_id.startswith("wx") else stable_id)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO seen(fingerprint, chat, ts) VALUES (?,?,?)",
                (fp, chat, created),
            )
            if cur.rowcount == 0:
                return None
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO messages
                   (chat, role, sender, content, msg_type, external_id, created_at, created_iso)
                   VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)""",
                (chat, sender or "我", content, msg_type, stable_id, created, self._iso(created)),
            )
            self._conn.commit()
            return int(cur.lastrowid) if cur.rowcount else None

    def add_assistant(self, chat: str, content: str, sticker_name: str = "",
                      profile_name: str = "", latency_ms: int = 0,
                      reply_to: int | None = None, meta: dict | None = None) -> int:
        created = time.time()
        meta_dict = dict(meta or {})
        if reply_to is not None:
            meta_dict["reply_to"] = reply_to
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO messages
                   (chat, role, sender, content, msg_type, sticker_name, profile_name,
                    latency_ms, meta, created_at, created_iso)
                   VALUES (?, 'assistant', '阿洛娜', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat, content, "sticker" if sticker_name else "text", sticker_name,
                 profile_name, latency_ms, json.dumps(meta_dict, ensure_ascii=False),
                 created, self._iso(created)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_system(self, chat: str, content: str) -> int:
        created = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO messages(chat, role, sender, content, msg_type, created_at, created_iso)
                   VALUES (?, 'system', 'system', ?, 'system', ?, ?)""",
                (chat, content, created, self._iso(created)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, chat: str, limit: int = 30, include_system: bool = False) -> list[dict]:
        limit = max(1, min(200, int(limit)))
        clauses = "chat=? AND role IN ('user','assistant')" if not include_system else "chat=?"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE {clauses} ORDER BY id DESC LIMIT ?",
                (chat, limit),
            ).fetchall()
        return [self._row(row) for row in reversed(rows)]

    def recent_after(self, chat: str, after_id: int = 0,
                     limit: int = 30) -> list[dict]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages
                   WHERE chat=? AND role IN ('user','assistant') AND id>?
                   ORDER BY id DESC LIMIT ?""",
                (chat, max(0, int(after_id or 0)), limit),
            ).fetchall()
        return [self._row(row) for row in reversed(rows)]

    def context_chars(self, chat: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)),0) AS n FROM messages "
                "WHERE chat=? AND role IN ('user','assistant')", (chat,)
            ).fetchone()
        return int(row["n"] or 0)

    def get_summary(self, chat: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT summary, covered_id, updated_at, updated_iso FROM chat_summaries WHERE chat=?",
                (chat,),
            ).fetchone()
        return dict(row) if row else {"summary": "", "covered_id": 0,
                                      "updated_at": 0.0, "updated_iso": ""}

    def set_summary(self, chat: str, summary: str, covered_id: int) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO chat_summaries(chat,summary,covered_id,updated_at,updated_iso)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(chat) DO UPDATE SET
                     summary=excluded.summary, covered_id=excluded.covered_id,
                     updated_at=excluded.updated_at, updated_iso=excluded.updated_iso""",
                (chat, str(summary or ""), max(0, int(covered_id or 0)), now, self._iso(now)),
            )
            self._conn.commit()

    def clear_summary(self, chat: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chat_summaries WHERE chat=?", (chat,))
            self._conn.commit()

    def recent_messages(self, limit: int = 40) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [self._row(row) for row in reversed(rows)]

    def recent_replies(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.id, a.chat, a.content, a.sticker_name, a.profile_name, a.latency_ms,
                          a.created_iso, a.meta,
                          (SELECT u.content FROM messages u
                            WHERE u.chat=a.chat AND u.id < a.id AND u.role='user'
                            ORDER BY u.id DESC LIMIT 1) AS user_content,
                          (SELECT u.sender FROM messages u
                            WHERE u.chat=a.chat AND u.id < a.id AND u.role='user'
                            ORDER BY u.id DESC LIMIT 1) AS user_sender
                   FROM messages a WHERE a.role='assistant'
                   ORDER BY a.id DESC LIMIT ?""",
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_user_time(self, chat: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) AS ts FROM messages WHERE chat=? AND role='user'", (chat,)
            ).fetchone()
        return float(row["ts"] or 0.0)

    def context_count(self, chat: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE chat=? AND role IN ('user','assistant')",
                (chat,),
            ).fetchone()
        return int(row["n"])

    def chats(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT chat, COUNT(*) AS count, MAX(created_at) AS last_ts
                   FROM messages WHERE role IN ('user','assistant')
                   GROUP BY chat ORDER BY last_ts DESC LIMIT ?""",
                (max(1, limit),),
            ).fetchall()
        return [{"chat": r["chat"], "count": r["count"],
                 "last": datetime.fromtimestamp(r["last_ts"]).strftime("%m-%d %H:%M")}
                for r in rows]

    def record_usage(self, chat: str, provider: str, model: str,
                     prompt_tokens: int, completion_tokens: int,
                     kind: str = "reply") -> dict:
        """Persist actual model token usage (never health probes)."""
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = prompt + completion
        created = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO token_usage
                   (chat, kind, provider, model, prompt_tokens, completion_tokens,
                    total_tokens, created_at, created_iso)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (chat, kind, provider, model, prompt, completion, total,
                 created, self._iso(created)),
            )
            self._conn.commit()
        return {"prompt": prompt, "completion": completion, "total": total,
                "provider": provider, "model": model, "kind": kind}

    def usage_totals(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens),0) AS prompt,
                          COALESCE(SUM(completion_tokens),0) AS completion,
                          COALESCE(SUM(total_tokens),0) AS total
                   FROM token_usage"""
            ).fetchone()
            trow = self._conn.execute(
                """SELECT COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens),0) AS prompt,
                          COALESCE(SUM(completion_tokens),0) AS completion,
                          COALESCE(SUM(total_tokens),0) AS total
                   FROM token_usage WHERE substr(created_iso,1,10)=?""",
                (today,),
            ).fetchone()
        return {
            "requests": int(row["requests"] or 0),
            "prompt": int(row["prompt"] or 0),
            "completion": int(row["completion"] or 0),
            "total": int(row["total"] or 0),
            "today_requests": int(trow["requests"] or 0),
            "today_prompt": int(trow["prompt"] or 0),
            "today_completion": int(trow["completion"] or 0),
            "today_total": int(trow["total"] or 0),
        }

    def usage_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM token_usage ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
            users = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE role='user'").fetchone()["n"]
            replies = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE role='assistant'").fetchone()["n"]
            today = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE role='assistant' AND created_at>=?",
                (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),),
            ).fetchone()["n"]
        return {"total": total, "users": users, "replies": replies, "today": today}

    def integrity(self) -> tuple[bool, str]:
        try:
            with self._lock:
                result = self._conn.execute("PRAGMA quick_check").fetchone()[0]
            return result == "ok", str(result)
        except sqlite3.Error as exc:
            return False, str(exc)

    def snapshot_chat(self, chat: str) -> dict:
        """Return a serializable snapshot for admin-session rollback."""
        with self._lock:
            messages = [dict(row) for row in self._conn.execute(
                "SELECT * FROM messages WHERE chat=? ORDER BY id", (chat,))]
            seen = [dict(row) for row in self._conn.execute(
                "SELECT * FROM seen WHERE chat=?", (chat,))]
            summary = self._conn.execute(
                "SELECT summary, covered_id, updated_at, updated_iso FROM chat_summaries WHERE chat=?",
                (chat,),
            ).fetchone()
        return {"messages": messages, "seen": seen,
                "summary": dict(summary) if summary else None}

    def restore_chat(self, chat: str, snapshot: dict) -> None:
        """Restore a snapshot taken before an admin session."""
        messages = snapshot.get("messages", []) if isinstance(snapshot, dict) else []
        seen = snapshot.get("seen", []) if isinstance(snapshot, dict) else []
        summary = snapshot.get("summary") if isinstance(snapshot, dict) else None
        columns = ("id", "chat", "role", "sender", "content", "msg_type",
                   "sticker_name", "external_id", "profile_name", "latency_ms",
                   "meta", "created_at", "created_iso")
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE chat=?", (chat,))
            self._conn.execute("DELETE FROM seen WHERE chat=?", (chat,))
            self._conn.execute("DELETE FROM chat_summaries WHERE chat=?", (chat,))
            for row in messages:
                values = tuple(row.get(column) for column in columns)
                placeholders = ",".join("?" for _ in columns)
                self._conn.execute(
                    f"INSERT INTO messages({','.join(columns)}) VALUES({placeholders})",
                    values,
                )
            for row in seen:
                self._conn.execute(
                    "INSERT OR REPLACE INTO seen(fingerprint,chat,ts) VALUES(?,?,?)",
                    (row.get("fingerprint"), chat, row.get("ts", time.time())),
                )
            if isinstance(summary, dict) and summary.get("summary"):
                self._conn.execute(
                    "INSERT OR REPLACE INTO chat_summaries(chat,summary,covered_id,updated_at,updated_iso) "
                    "VALUES(?,?,?,?,?)",
                    (chat, summary.get("summary", ""), int(summary.get("covered_id", 0) or 0),
                     float(summary.get("updated_at", time.time()) or time.time()),
                     str(summary.get("updated_iso", self._iso()))),
                )
            self._conn.commit()

    def clear(self, chat: str | None = None) -> int:
        with self._lock:
            if chat:
                cur = self._conn.execute("DELETE FROM messages WHERE chat=?", (chat,))
                self._conn.execute("DELETE FROM seen WHERE chat=?", (chat,))
                self._conn.execute("DELETE FROM chat_summaries WHERE chat=?", (chat,))
            else:
                cur = self._conn.execute("DELETE FROM messages")
                self._conn.execute("DELETE FROM seen")
                self._conn.execute("DELETE FROM chat_summaries")
            self._conn.commit()
            return cur.rowcount

    def set_kv(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_kv(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except json.JSONDecodeError:
            data["meta"] = {}
        return data

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
