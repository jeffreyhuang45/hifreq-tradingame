# src/common/operation_log.py
"""
Operation & System Log – ring-buffer based structured logging.

DSD §3 Observability:
  • Client operation logs: timestamp, user_id, operation, path, status, detail
  • System logs: timestamp, module, level, message
  • Both stored in fixed-size ring buffers (in-memory, queryable via API)
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Operation Log (client actions) ───────────────────────────

@dataclass
class OperationLogEntry:
    """A single client operation record."""
    timestamp: str
    user_id: str
    operation: str       # HTTP method + short description
    path: str            # request path
    status_code: int
    detail: str = ""


class OperationLogBuffer:
    """Thread-safe ring buffer for client operation logs."""

    def __init__(self, max_size: int = 2000) -> None:
        self._buffer: deque[OperationLogEntry] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def append(self, entry: OperationLogEntry) -> None:
        with self._lock:
            self._buffer.append(entry)

    def query(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent entries, optionally filtered by user_id."""
        with self._lock:
            items = list(self._buffer)
        if user_id:
            items = [e for e in items if e.user_id == user_id]
        return [asdict(e) for e in items[-limit:]]


# Singleton
operation_log = OperationLogBuffer()


# ── System Log (structured log handler) ──────────────────────

@dataclass
class SystemLogEntry:
    """A single structured system log record."""
    timestamp: str
    module: str
    level: str
    message: str


class SystemLogBuffer:
    """Thread-safe ring buffer for structured system logs."""

    def __init__(self, max_size: int = 2000) -> None:
        self._buffer: deque[SystemLogEntry] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def append(self, entry: SystemLogEntry) -> None:
        with self._lock:
            self._buffer.append(entry)

    def query(
        self,
        level: str | None = None,
        module: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buffer)
        if level:
            items = [e for e in items if e.level == level.upper()]
        if module:
            items = [e for e in items if module.lower() in e.module.lower()]
        return [asdict(e) for e in items[-limit:]]


# Singleton
system_log = SystemLogBuffer()


class RingBufferLogHandler(logging.Handler):
    """
    Python logging.Handler that captures log records into the
    SystemLogBuffer for API exposure.
    """

    def __init__(self, buffer: SystemLogBuffer | None = None) -> None:
        super().__init__()
        self._buffer = buffer or system_log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = SystemLogEntry(
                timestamp=datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat(),
                module=record.name,
                level=record.levelname,
                message=self.format(record),
            )
            self._buffer.append(entry)
        except Exception:
            self.handleError(record)


def install_system_log_handler() -> None:
    """Install the ring-buffer handler on the root logger."""
    handler = RingBufferLogHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
