# src/storage/snapshot.py
"""
Snapshot – optional periodic state snapshot for faster cold-start.

Design rules (DSD §4.7):
  • Snapshots are supplementary – event log is always the SoT.
  • Directory: data/snapshots/YYYY-MM/snapshot-YYYY-MM-DD-HHMM.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
from enum import Enum


def _snapshot_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Cannot serialize {type(obj)}")


class SnapshotManager:
    """Write and read system-state snapshots."""

    def __init__(self, base_dir: str | Path = "data/snapshots"):
        self._base_dir = Path(base_dir)

    def save(self, state: dict[str, Any]) -> Path:
        """
        Save a state dict to a timestamped JSON file.
        Returns the path written.
        """
        now = datetime.now(timezone.utc)
        month_dir = now.strftime("%Y-%m")
        filename = now.strftime("snapshot-%Y-%m-%d-%H%M.json")
        path = self._base_dir / month_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, default=_snapshot_default, ensure_ascii=False, indent=2)
        return path

    def load_latest(self) -> dict[str, Any] | None:
        """Load the most recent snapshot, or None if none exist."""
        if not self._base_dir.exists():
            return None

        all_files: list[Path] = []
        for month_dir in sorted(self._base_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for snap in sorted(month_dir.glob("snapshot-*.json"), reverse=True):
                all_files.append(snap)

        if not all_files:
            return None

        latest = all_files[0]
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
