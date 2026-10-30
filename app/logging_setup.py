"""Logging configuration."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .logbus import BUS
from .paths import LOG_FILE, ensure_dirs

_CONFIGURED = False


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[37m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41;97m",
    }

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if sys.stderr and sys.stderr.isatty():
            color = self.COLORS.get(record.levelname, "")
            return f"{color}{text}\033[0m" if color else text
        return text


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(_ColorFormatter("[%(asctime)s] %(levelname)-7s %(message)s",
                                         datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_500_000, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)
    root.addHandler(BUS)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    _CONFIGURED = True
