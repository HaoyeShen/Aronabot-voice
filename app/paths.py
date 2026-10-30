"""Filesystem locations used by ARONA."""

from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):  # pragma: no cover - only for optional packaging
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parents[1]
    BUNDLE = ROOT

ASSETS = BUNDLE / "assets"
DATA = ROOT / "data"
STICKER_DIR = DATA / "stickers"
LOGS = ROOT / "logs"
CONFIG_FILE = DATA / "config.json"
PERSONAS_FILE = DATA / "personas.json"
STICKERS_FILE = DATA / "stickers.json"
DATABASE = DATA / "arona.db"
LOG_FILE = LOGS / "arona.log"
BACKUP_DIR = DATA / "backups"


def ensure_dirs() -> None:
    for folder in (DATA, STICKER_DIR, LOGS, BACKUP_DIR):
        folder.mkdir(parents=True, exist_ok=True)
