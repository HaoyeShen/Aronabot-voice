"""A tiny thread-safe log ring buffer used by the GUI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LogItem:
    seq: int
    time: str
    level: str
    logger: str
    message: str

    def as_text(self) -> str:
        return f"[{self.time}] {self.level:<7} {self.message}"


class LogBus(logging.Handler):
    def __init__(self, capacity: int = 2500) -> None:
        super().__init__()
        self._items: deque[LogItem] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + self.formatException(record.exc_info)
            item_time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            with self._lock:
                self._seq += 1
                item = LogItem(
                    seq=self._seq,
                    time=item_time,
                    level=record.levelname,
                    logger=record.name,
                    message=msg,
                )
                self._items.append(item)
        except Exception:
            self.handleError(record)

    def snapshot(self) -> list[LogItem]:
        with self._lock:
            return list(self._items)

    def since(self, seq: int) -> list[LogItem]:
        with self._lock:
            return [item for item in self._items if item.seq > seq]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def clear_view(self) -> None:
        with self._lock:
            self._items.clear()


BUS = LogBus()
