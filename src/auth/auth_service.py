# src/auth/auth_service.py
"""
Authentication service – password hashing, token creation / validation.

Uses stdlib only (hashlib + hmac) to avoid heavy crypto dependencies.
Tokens are HS256-signed JWTs (via PyJWT when available, fallback to
a simple base64 + HMAC scheme).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import base64
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from src.auth.user_model import User, UserRepository

# ── Configuration ────────────────────────────────────────────
SECRET_KEY = "hifreq-tradingame-secret-2026"
TOKEN_EXPIRY_HOURS = 24
ALGORITHM = "HS256"
DEFAULT_PASSWORD = "changeme"  # well-known default for password reset

# ── Password helpers ─────────────────────────────────────────

def _hash_password(password: str, salt: str) -> str:
    """PBKDF2-SHA256, 100 000 iterations."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return dk.hex()


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), stored_hash)


# ── JWT helpers (stdlib fallback) ────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def _create_jwt(payload: dict, secret: str = SECRET_KEY) -> str:
    """Create a minimal JWT (HS256) without external libraries."""
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def _decode_jwt(token: str, secret: str = SECRET_KEY) -> dict:
    """Decode and verify a minimal JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    h, p, s = parts
    # Verify signature
    signing_input = f"{h}.{p}"
    expected_sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(s)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid token signature")
    payload = json.loads(_b64url_decode(p))
    # Check expiry
    if "exp" in payload and payload["exp"] < time.time():
        raise ValueError("Token expired")
    return payload


# ── AuthService ──────────────────────────────────────────────

class AuthService:
    """Manages registration, login, token validation, password changes."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._repo = user_repo

    # ── Registration ─────────────────────────────────────────

    def register(
        self,
        username: str,
        password: str,
        role: str = "user",
        display_name: str = "",
    ) -> User:
        salt = secrets.token_hex(16)
        pw_hash = _hash_password(password, salt)
        user = User(
            user_id=str(uuid.uuid4()),
            username=username,
            password_hash=pw_hash,
            salt=salt,
            role=role,
            display_name=display_name or username,
        )
        self._repo.add(user)
        return user

    # ── Login ────────────────────────────────────────────────

    def login(self, username: str, password: str) -> str:
        """Returns a JWT token on success, raises ValueError otherwise."""
        user = self._repo.get_by_username(username)
        if user is None:
            raise ValueError("Invalid username or password")
        if not _verify_password(password, user.salt, user.password_hash):
            raise ValueError("Invalid username or password")
        return self._issue_token(user)

    # ── Token ────────────────────────────────────────────────

    def validate_token(self, token: str) -> User:
        """Returns the User if the token is valid, raises ValueError otherwise."""
        payload = _decode_jwt(token)
        user = self._repo.get_by_id(payload.get("user_id", ""))
        if user is None:
            raise ValueError("User not found")
        if getattr(user, "status", "active") != "active":
            raise ValueError(f"Account is {user.status}")
        return user

    def _issue_token(self, user: User) -> str:
        payload = {
            "user_id": user.user_id,
            "username": user.username,
            "role": user.role,
            "exp": int(time.time()) + TOKEN_EXPIRY_HOURS * 3600,
        }
        return _create_jwt(payload)

    # ── Password management ──────────────────────────────────

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        if not _verify_password(old_password, user.salt, user.password_hash):
            raise ValueError("Current password is incorrect")
        new_salt = secrets.token_hex(16)
        user.salt = new_salt
        user.password_hash = _hash_password(new_password, new_salt)
        self._repo.update(user)

    def admin_reset_password(self, user_id: str, new_password: str | None = None) -> None:
        """Admin resets another user's password. None → use DEFAULT_PASSWORD."""
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        pw = new_password or DEFAULT_PASSWORD
        new_salt = secrets.token_hex(16)
        user.salt = new_salt
        user.password_hash = _hash_password(pw, new_salt)
        self._repo.update(user)

    # ── Admin CRUD ───────────────────────────────────────────

    def admin_update_user(self, user_id: str, **kwargs: Any) -> User:
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        if "display_name" in kwargs:
            user.display_name = kwargs["display_name"]
        if "role" in kwargs:
            user.role = kwargs["role"]
        if "username" in kwargs and kwargs["username"] != user.username:
            user.username = kwargs["username"]
        if "status" in kwargs:
            user.status = kwargs["status"]
        self._repo.update(user)
        return user

    def admin_delete_user(self, user_id: str) -> bool:
        return self._repo.delete(user_id)

    def ensure_admin_exists(self) -> None:
        """Create default admin account if none exists."""
        if self._repo.get_by_username("admin") is None:
            self.register("admin", "admin123", role="admin", display_name="系統管理員")
