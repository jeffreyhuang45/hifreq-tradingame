# src/common/circuit_breaker.py
"""
Circuit Breaker pattern (DSD §4.4).

States:
  CLOSED      – normal operation, requests pass through.
  OPEN        – too many failures; requests are rejected immediately.
  HALF_OPEN   – after a cool-down, one probe request is allowed through.

Usage:
    cb = CircuitBreaker(failure_threshold=5, reset_timeout_s=60)
    result = cb.call(some_function, arg1, arg2)
"""
from __future__ import annotations

import time
import threading
from enum import Enum
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when the circuit is open and requests are being rejected."""

    def __init__(self, name: str = ""):
        msg = f"Circuit breaker '{name}' is OPEN – service unavailable"
        super().__init__(msg)


class CircuitBreaker:
    """Thread-safe circuit breaker."""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        reset_timeout_s: float = 60.0,
    ):
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute *func* through the circuit breaker.
        Raises CircuitOpenError if the circuit is open.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(self.name)

        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    async def async_call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Async variant of call()."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(self.name)

        try:
            result = await func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    # ── Internal ─────────────────────────────────────────────

    def _on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN

    def _maybe_transition_to_half_open(self) -> None:
        """Must be called while holding self._lock."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._reset_timeout_s:
                self._state = CircuitState.HALF_OPEN
                self._failure_count = 0
