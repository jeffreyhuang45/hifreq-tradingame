# src/storage/event_loader.py
"""
Event Loader – reads NDJSON event log files for cold-start replay.

Design rules (DSD §4.7):
  • Read events in chronological order.
  • Support reading from a specific snapshot point forward.
  • Directory layout: data/events/YYYY-MM/events-YYYY-MM-DD.log
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.events.base_event import BaseEvent, event_from_dict


class EventLoader:
    """Loads events from NDJSON log files."""

    def __init__(self, base_dir: str | Path = "data/events"):
        self._base_dir = Path(base_dir)

    def load_all(self) -> list[dict[str, Any]]:
        """
        Load all event dicts from every log file, sorted by filename
        (which embeds the date).
        """
        if not self._base_dir.exists():
            return []

        all_dicts: list[dict[str, Any]] = []
        # Sort month dirs then day files
        for month_dir in sorted(self._base_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for log_file in sorted(month_dir.glob("events-*.log")):
                all_dicts.extend(self._read_file(log_file))
        return all_dicts

    def load_events(self) -> list[BaseEvent]:
        """Load and deserialize all events."""
        dicts = self.load_all()
        events: list[BaseEvent] = []
        for d in dicts:
            try:
                events.append(event_from_dict(d))
            except (ValueError, KeyError, TypeError):
                # Skip malformed events during replay
                pass
        return events

    # ── Snapshot-aware loading (DSD §4.7 G-4) ────────────────

    def load_after(self, after: datetime) -> list[dict[str, Any]]:
        """
        Load event dicts whose 'occurred_at' is strictly **after** *after*.
        Used to replay only events recorded since the last snapshot.
        """
        all_dicts = self.load_all()
        ts_cutoff = after.isoformat()
        return [
            d for d in all_dicts
            if d.get("occurred_at", "") > ts_cutoff
        ]

    def load_events_after(self, after: datetime) -> list[BaseEvent]:
        """Deserialize events that occurred strictly after *after*."""
        dicts = self.load_after(after)
        events: list[BaseEvent] = []
        for d in dicts:
            try:
                events.append(event_from_dict(d))
            except (ValueError, KeyError, TypeError):
                pass
        return events

    @staticmethod
    def _read_file(path: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip malformed lines
        return result
