# src/storage/event_writer.py
"""
Data Pump – Event Writer.

Design rules (DSD §4.7):
  • Append-only NDJSON event log.
  • NOT on the critical path – writes happen asynchronously.
  • Buffer + batch flush (every N events or T seconds).
  • Directory layout: data/events/YYYY-MM/events-YYYY-MM-DD.log
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from src.events.base_event import BaseEvent, _json_default


class EventWriter:
    """
    Async-capable event writer that buffers events and periodically
    flushes them to an append-only NDJSON file.
    """

    def __init__(
        self,
        base_dir: str | Path = "data/events",
        batch_size: int = 100,
        flush_interval_s: float = 1.0,
    ):
        self._base_dir = Path(base_dir)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s

        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def base_dir(self) -> Path:
        """Public accessor for the event log base directory."""
        return self._base_dir

    # ── Public API ───────────────────────────────────────────

    def push(self, event: BaseEvent) -> None:
        """Add an event to the buffer (thread-safe).

        NEVER flushes inline — the background thread handles all I/O.
        This ensures push() is safe to call on the critical path
        (DSD §1.6 #9, #16: Critical Path 禁做 File I/O).
        """
        with self._lock:
            self._buffer.append(event.to_dict())

    def flush(self) -> int:
        """Force-flush the buffer. Returns number of events written."""
        with self._lock:
            return self._flush_locked()

    def start_background(self) -> None:
        """Start a background thread that periodically flushes."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background flushing and do a final flush."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()

    # ── Internals ────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            time.sleep(min(self._flush_interval_s, 0.1))
            # Flush if interval elapsed OR buffer is at/above batch_size
            should_flush = False
            with self._lock:
                elapsed = time.monotonic() - self._last_flush >= self._flush_interval_s
                buffer_full = len(self._buffer) >= self._batch_size
                should_flush = elapsed or buffer_full
            if should_flush:
                self.flush()

    def _flush_locked(self) -> int:
        """Must be called while holding self._lock."""
        if not self._buffer:
            return 0

        count = len(self._buffer)
        lines = [
            json.dumps(d, default=_json_default, ensure_ascii=False)
            for d in self._buffer
        ]
        self._buffer.clear()
        self._last_flush = time.monotonic()

        # Determine file path based on first event's timestamp
        first = lines[0]
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

        return count

    def _resolve_path(self) -> Path:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        month_dir = now.strftime("%Y-%m")
        filename = now.strftime("events-%Y-%m-%d.log")
        return self._base_dir / month_dir / filename
