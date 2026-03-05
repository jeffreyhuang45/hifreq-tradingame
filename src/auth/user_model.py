# src/auth/user_model.py
"""
User entity and in-memory repository persisted to JSON file.

Roles:
  • "admin"  – can CRUD all users, view system stats
  • "user"   – regular trader, can only change own password
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


@dataclass
class User:
    user_id: str
    username: str
    password_hash: str
    salt: str
    role: str = "user"                       # "admin" | "user"
    display_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "active"                    # "active" | "disabled" | "frozen"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "password_hash" and k != "salt"}

    def to_full_dict(self) -> dict:
        return asdict(self)


class UserRepository:
    """In-memory user store with JSON file persistence."""

    def __init__(self, data_dir: str | Path = "data"):
        self._data_dir = Path(data_dir)
        self._users: Dict[str, User] = {}            # user_id -> User
        self._username_idx: Dict[str, str] = {}       # username -> user_id
        self._load()

    # ── CRUD ─────────────────────────────────────────────────

    def add(self, user: User) -> None:
        if user.username in self._username_idx:
            raise ValueError(f"Username '{user.username}' already exists")
        self._users[user.user_id] = user
        self._username_idx[user.username] = user.user_id
        self._save()

    def get_by_id(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        uid = self._username_idx.get(username)
        return self._users.get(uid) if uid else None

    def list_all(self) -> List[User]:
        return list(self._users.values())

    def update(self, user: User) -> None:
        if user.user_id not in self._users:
            raise ValueError(f"User '{user.user_id}' not found")
        old = self._users[user.user_id]
        if old.username != user.username:
            del self._username_idx[old.username]
            self._username_idx[user.username] = user.user_id
        self._users[user.user_id] = user
        self._save()

    def delete(self, user_id: str) -> bool:
        user = self._users.pop(user_id, None)
        if user is None:
            return False
        self._username_idx.pop(user.username, None)
        self._save()
        return True

    # ── Persistence ──────────────────────────────────────────

    def _path(self) -> Path:
        return self._data_dir / "users.json"

    def _save(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = [u.to_full_dict() for u in self._users.values()]
        with open(self._path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                user = User(**d)
                self._users[user.user_id] = user
                self._username_idx[user.username] = user.user_id
        except Exception:
            pass  # start fresh if file is corrupted
