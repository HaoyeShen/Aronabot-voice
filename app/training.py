"""Local document training / retrieval store.

Ollama does not expose weight fine-tuning.  This module implements the
practical local equivalent: uploaded documents are copied into the project,
parsed, chunked, indexed with SQLite FTS5, and retrieved into the prompt at
reply time.  The model weights are never modified, and no document leaves the
machine unless the selected model/API itself is remote.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import textwrap
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from xml.etree import ElementTree as ET

from .paths import DATA, ensure_dirs

log = logging.getLogger(__name__)

TRAINING_DIR = DATA / "training"
TRAINING_FILES = TRAINING_DIR / "files"
TRAINING_DB = DATA / "training.db"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yaml", ".yml",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".docx", ".pdf"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
CHUNK_SIZE = 700
CHUNK_OVERLAP = 110


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or "document"


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _flatten_json(value: object, out: list[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        if value.strip():
            out.append(value)
    elif isinstance(value, dict):
        preferred = ("content", "text", "prompt", "response", "instruction",
                     "input", "output", "question", "answer", "user", "assistant")
        for key in preferred:
            if key in value:
                _flatten_json(value[key], out, depth + 1)
        if not out:
            for item in value.values():
                _flatten_json(item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_json(item, out, depth + 1)


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX 中没有 word/document.xml") from exc
    root = ET.fromstring(raw)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        pieces = [node.text or "" for node in paragraph.iter(namespace + "t")]
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    # Tables are not always covered by paragraph iteration in older Word files;
    # include their text once more without duplicating body paragraphs.
    for table in root.iter(namespace + "tbl"):
        for row in table.iter(namespace + "tr"):
            cells = ["".join(n.text or "" for n in cell.iter(namespace + "t")).strip()
                     for cell in row.iter(namespace + "tc")]
            line = " | ".join(cell for cell in cells if cell)
            if line and line not in paragraphs:
                paragraphs.append(line)
    return "\n".join(paragraphs)


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("读取 PDF 需要 pypdf：请运行 pip install pypdf") from exc
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(pages)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _docx_text(path)
    if suffix == ".pdf":
        return _pdf_text(path)
    raw = _decode_bytes(path.read_bytes())
    if suffix in (".html", ".htm"):
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        raw = html.unescape(raw)
    elif suffix == ".csv" or suffix == ".tsv":
        delimiter = "\t" if suffix == ".tsv" else ","
        rows = []
        for row in csv.reader(io.StringIO(raw), delimiter=delimiter):
            clean = [cell.strip() for cell in row if cell.strip()]
            if clean:
                rows.append(" | ".join(clean))
        raw = "\n".join(rows)
    elif suffix == ".json":
        try:
            values: list[str] = []
            _flatten_json(json.loads(raw), values)
            if values:
                raw = "\n\n".join(values)
        except (ValueError, TypeError):
            pass
    elif suffix == ".jsonl":
        values = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _flatten_json(json.loads(line), values)
            except (ValueError, TypeError):
                values.append(line)
        if values:
            raw = "\n\n".join(values)
    return raw


def chunk_text(text: str, size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split long text on paragraph/sentence boundaries with overlap."""
    text = (text or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if not text:
        return []
    text = text[:MAX_TEXT_CHARS]
    chunks: list[str] = []
    start = 0
    length = len(text)
    separators = ("\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",")
    while start < length:
        end = min(length, start + size)
        if end < length:
            floor = start + int(size * 0.55)
            best = -1
            for sep in separators:
                pos = text.rfind(sep, floor, end)
                if pos > best:
                    best = pos + len(sep)
            if best > floor:
                end = best
        body = text[start:end].strip()
        if len(body) >= 4:
            chunks.append(body)
        if end >= length:
            break
        start = max(start + 1, end - overlap)
    return chunks


class TrainingStore:
    """SQLite-backed document store with Chinese-friendly trigram retrieval."""

    def __init__(self, path: Path = TRAINING_DB, files_dir: Path = TRAINING_FILES) -> None:
        ensure_dirs()
        self.path = path
        self.files_dir = files_dir
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS training_docs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL DEFAULT 0,
                chars INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS training_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_training_chunks_doc
                ON training_chunks(doc_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS training_fts USING fts5(
                content, doc_id UNINDEXED, seq UNINDEXED, tokenize='trigram'
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------ files
    @staticmethod
    def supported(path: Path) -> bool:
        return path.suffix.lower() in SUPPORTED_EXTENSIONS

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def add_file(self, source: str | Path) -> dict:
        path = Path(source)
        if not path.is_file():
            return {"ok": False, "name": path.name, "error": "文件不存在"}
        if not self.supported(path):
            return {"ok": False, "name": path.name,
                    "error": "不支持该格式，支持 txt/md/json/jsonl/csv/docx/pdf 等"}
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                return {"ok": False, "name": path.name,
                        "error": f"文件超过 {MAX_FILE_BYTES // 1024 // 1024} MB 限制"}
            digest = self._sha256(path)
            with self._lock:
                existing = self._conn.execute(
                    "SELECT * FROM training_docs WHERE sha256=?", (digest,)
                ).fetchone()
            if existing:
                return {"ok": True, "duplicate": True, "doc": dict(existing),
                        "name": path.name, "chunks": existing["chunk_count"]}
            text = extract_text(path)
            chunks = chunk_text(text)
            if not chunks:
                return {"ok": False, "name": path.name, "error": "没有提取到可用文字"}
            doc_id = uuid.uuid4().hex[:12]
            stored = f"{digest[:12]}_{_safe_name(path.name)}"
            target = self.files_dir / stored
            if not target.exists():
                shutil.copy2(path, target)
            with self._lock:
                try:
                    self._conn.execute(
                        """INSERT INTO training_docs
                           (id, name, stored_name, sha256, size, chars, chunk_count,
                            created_at, status)
                           VALUES (?,?,?,?,?,?,?,?, 'ready')""",
                        (doc_id, path.name, stored, digest, size, len(text),
                         len(chunks), _now()),
                    )
                    self._insert_chunks(doc_id, chunks)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
            return {"ok": True, "duplicate": False, "doc_id": doc_id,
                    "name": path.name, "chunks": len(chunks), "chars": len(text)}
        except Exception as exc:
            log.warning("训练资料解析失败：%s · %s", path.name, exc)
            return {"ok": False, "name": path.name, "error": str(exc)}

    def _insert_chunks(self, doc_id: str, chunks: Iterable[str]) -> None:
        for seq, content in enumerate(chunks):
            cur = self._conn.execute(
                "INSERT INTO training_chunks(doc_id, seq, content) VALUES (?,?,?)",
                (doc_id, seq, content),
            )
            self._conn.execute(
                "INSERT INTO training_fts(rowid, content, doc_id, seq) VALUES (?,?,?,?)",
                (cur.lastrowid, content, doc_id, seq),
            )

    def add_files(self, paths: Iterable[str | Path],
                  progress: Callable[[int, int, str], None] | None = None) -> dict:
        items = [Path(path) for path in paths]
        added: list[dict] = []
        duplicates: list[dict] = []
        errors: list[dict] = []
        total = len(items)
        for index, path in enumerate(items, start=1):
            result = self.add_file(path)
            if result.get("ok") and result.get("duplicate"):
                duplicates.append(result)
            elif result.get("ok"):
                added.append(result)
            else:
                errors.append(result)
            if progress:
                try:
                    progress(index, total, path.name)
                except Exception:
                    pass
        return {"added": added, "duplicates": duplicates, "errors": errors}

    # ------------------------------------------------------------------ CRUD
    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM training_docs ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS docs, COALESCE(SUM(chunk_count),0) AS chunks,
                          COALESCE(SUM(chars),0) AS chars FROM training_docs"""
            ).fetchone()
        return {"docs": int(row["docs"]), "chunks": int(row["chunks"]),
                "chars": int(row["chars"])}

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT stored_name FROM training_docs WHERE id=?", (doc_id,)
            ).fetchone()
            if not row:
                return False
            self._delete_chunks(doc_id)
            self._conn.execute("DELETE FROM training_docs WHERE id=?", (doc_id,))
            self._conn.commit()
        try:
            (self.files_dir / row["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def _delete_chunks(self, doc_id: str) -> None:
        ids = [row["id"] for row in self._conn.execute(
            "SELECT id FROM training_chunks WHERE doc_id=?", (doc_id,)
        ).fetchall()]
        self._conn.executemany("DELETE FROM training_fts WHERE rowid=?", [(i,) for i in ids])
        self._conn.execute("DELETE FROM training_chunks WHERE doc_id=?", (doc_id,))

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM training_fts")
            self._conn.execute("DELETE FROM training_chunks")
            self._conn.execute("DELETE FROM training_docs")
            self._conn.commit()
        for path in self.files_dir.glob("*"):
            try:
                if path.is_file():
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def reindex(self, progress: Callable[[int, int, str], None] | None = None) -> dict:
        docs = self.list_documents()
        total = len(docs)
        rebuilt = 0
        failed: list[dict] = []
        with self._lock:
            self._conn.execute("DELETE FROM training_fts")
            self._conn.execute("DELETE FROM training_chunks")
            self._conn.commit()
        for index, doc in enumerate(docs, start=1):
            try:
                path = self.files_dir / doc["stored_name"]
                text = extract_text(path)
                chunks = chunk_text(text)
                if not chunks:
                    raise ValueError("没有提取到可用文字")
                with self._lock:
                    self._insert_chunks(doc["id"], chunks)
                    self._conn.execute(
                        """UPDATE training_docs SET chars=?, chunk_count=?, status='ready',
                           error='' WHERE id=?""",
                        (len(text), len(chunks), doc["id"]),
                    )
                    self._conn.commit()
                rebuilt += 1
            except Exception as exc:
                with self._lock:
                    self._conn.execute(
                        "UPDATE training_docs SET status='error', error=? WHERE id=?",
                        (str(exc), doc["id"]),
                    )
                    self._conn.commit()
                failed.append({"name": doc["name"], "error": str(exc)})
            if progress:
                try:
                    progress(index, total, doc["name"])
                except Exception:
                    pass
        return {"docs": rebuilt, "failed": failed}

    # --------------------------------------------------------------- retrieval
    @staticmethod
    def _terms(query: str) -> tuple[list[str], list[str]]:
        """Return (FTS trigram terms, fallback substring terms)."""
        fts_terms: list[str] = []
        like_terms: list[str] = []
        for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]{2,}", query or ""):
            if re.fullmatch(r"[\u4e00-\u9fff]+", run):
                if len(run) >= 2:
                    like_terms.append(run)
                if len(run) <= 3:
                    if len(run) == 3:
                        fts_terms.append(run)
                else:
                    for i in range(len(run) - 2):
                        fts_terms.append(run[i:i + 3])
            else:
                if len(run) >= 3:
                    fts_terms.append(run)
                if len(run) >= 2:
                    like_terms.append(run)
        def unique(items: list[str], limit: int) -> list[str]:
            seen: set[str] = set()
            out = []
            for item in items:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(item)
                if len(out) >= limit:
                    break
            return out
        return unique(fts_terms, 20), unique(like_terms, 10)

    def search(self, query: str, limit: int = 3,
               max_chars: int = 1800) -> list[dict]:
        query = (query or "").strip()
        if len(query) < 2:
            return []
        limit = max(1, min(8, int(limit)))
        max_chars = max(200, min(6000, int(max_chars)))
        fts_terms, like_terms = self._terms(query)
        candidates: list[dict] = []
        if fts_terms:
            match = " OR ".join(f'"{term}"' for term in fts_terms)
            try:
                with self._lock:
                    rows = self._conn.execute(
                        """SELECT c.id AS chunk_id, c.doc_id, c.seq, c.content,
                                  d.name, bm25(training_fts) AS rank
                           FROM training_fts
                           JOIN training_chunks c ON c.id = training_fts.rowid
                           JOIN training_docs d ON d.id = c.doc_id
                           WHERE training_fts MATCH ?
                           ORDER BY rank LIMIT ?""",
                        (match, max(20, limit * 8)),
                    ).fetchall()
                candidates = [dict(row) for row in rows]
            except sqlite3.Error as exc:
                log.debug("训练资料 FTS 查询失败：%s", exc)
        if not candidates and like_terms:
            clauses = " OR ".join("content LIKE ?" for _ in like_terms)
            params = [f"%{term}%" for term in like_terms]
            with self._lock:
                rows = self._conn.execute(
                    f"""SELECT c.id AS chunk_id, c.doc_id, c.seq, c.content, d.name
                        FROM training_chunks c JOIN training_docs d ON d.id=c.doc_id
                        WHERE {clauses} LIMIT ?""",
                    (*params, max(20, limit * 8)),
                ).fetchall()
            for row in rows:
                item = dict(row)
                item["rank"] = 0.0
                candidates.append(item)
        if not candidates:
            return []
        # Deduplicate by document/sequence, then trim to the character budget.
        seen: set[tuple[str, int]] = set()
        results: list[dict] = []
        used = 0
        for item in candidates:
            key = (str(item.get("doc_id")), int(item.get("seq", 0)))
            if key in seen:
                continue
            seen.add(key)
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            remaining = max_chars - used
            if remaining <= 60:
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip()
            used += len(content)
            rank = float(item.get("rank", 0.0))
            results.append({
                "doc_id": key[0],
                "seq": key[1],
                "name": str(item.get("name", "")),
                "content": content,
                "score": round(-rank, 4) if rank else 0.0,
            })
            if len(results) >= limit:
                break
        return results

    def close(self) -> None:
        with self._lock:
            self._conn.close()
