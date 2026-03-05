# src/events/base_event.py
"""
Canonical Event Model (DSD §2.6).

Immutable base event, EventType enum, JSON-safe serialization,
and type-coercing deserialization.

All events inherit from BaseEvent and are frozen dataclasses.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from src.common.clock import SYSTEM_CLOCK
from src.common.types import CorrelationID, EventID


class EventType(str, Enum):
    ORDER_PLACED = "OrderPlaced"
    ORDER_ACCEPTED = "OrderAccepted"
    ORDER_REJECTED = "OrderRejected"
    ORDER_CANCELED = "OrderCanceled"
    ORDER_ROUTED = "OrderRouted"
    ORDER_FILLED = "OrderFilled"
    CANCEL_REJECTED = "CancelRejected"
    ORDER_REMAIN_ON_BOOK = "OrderRemainOnBook"
    TRADE_EXECUTED = "TradeExecuted"
    ACCOUNT_UPDATED = "AccountUpdated"
    TRADE_SETTLEMENT = "TradeSettlement"
    ACCOUNT_DELETED = "AccountDeleted"
    MARKET_DATA_UPDATED = "MarketDataUpdated"


# ── JSON helpers ──────────────────────────────────────────────
def _json_default(obj: Any) -> Any:
    """Custom serializer for JSON encoder."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


@dataclass(frozen=True)
class BaseEvent:
    """
    Canonical base for every domain event.
    Subclasses MUST declare their own payload fields
    **before** the optional defaults below.
    """
    event_id: EventID = field(default_factory=uuid.uuid4)
    event_type: EventType = field(init=False)  # set by subclass
    occurred_at: datetime = field(default_factory=SYSTEM_CLOCK.now)
    correlation_id: CorrelationID = field(default_factory=uuid.uuid4)
    producer: str = field(default="System")
    payload_version: str = field(default="1.0")

    # ── Serialization ────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        """Convert event to a JSON-safe plain dict.

        All values are primitive Python types (str, int, float, None,
        list, dict) — safe to pass directly to ``json.dumps()``
        without a custom *default* hook.
        """
        raw = asdict(self)
        return _sanitize(raw)

    def to_json(self) -> str:
        """Serialize to a single-line JSON string (NDJSON compatible)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ── JSON sanitizer ───────────────────────────────────────────

def _sanitize(obj: Any) -> Any:
    """Recursively convert non-JSON-safe types to primitives."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


# ── Registry for deserialization ─────────────────────────────
_EVENT_REGISTRY: dict[str, type[BaseEvent]] = {}


def register_event(cls: type[BaseEvent]) -> type[BaseEvent]:
    """Class decorator that registers an event type for from_dict()."""
    # Access event_type default from the dataclass field
    for f in fields(cls):
        if f.name == "event_type" and f.default is not f.default_factory:
            _EVENT_REGISTRY[f.default.value] = cls
            break
    return cls


def get_event_registry() -> dict[str, type[BaseEvent]]:
    """Return a **copy** of the event registry (EventType value → class).

    Useful for introspection, testing, and validation.
    """
    return dict(_EVENT_REGISTRY)


# ── Enum coercion map ────────────────────────────────────────
# Lazy-initialised on first call to event_from_dict so that the
# enum module is already imported by the time we need it.
_ENUM_COERCION: dict[str, type[Enum]] | None = None


def _get_enum_coercion() -> dict[str, type[Enum]]:
    global _ENUM_COERCION
    if _ENUM_COERCION is None:
        from src.common.enums import (
            OrderSide,
            OrderStatus,
            OrderType,
            RejectionReason,
        )
        _ENUM_COERCION = {
            "OrderSide": OrderSide,
            "OrderType": OrderType,
            "OrderStatus": OrderStatus,
            "RejectionReason": RejectionReason,
        }
    return _ENUM_COERCION


def event_from_dict(data: dict[str, Any]) -> BaseEvent:
    """Reconstruct a concrete event from a dict.

    Performs full type coercion:
      • UUID fields  → ``uuid.UUID``
      • datetime     → ``datetime.fromisoformat``
      • Decimal/Money/Qty → ``Decimal``
      • Enum fields  → proper Enum member (OrderSide, OrderType, etc.)
    """
    event_type_str = data.get("event_type")
    cls = _EVENT_REGISTRY.get(event_type_str)
    if cls is None:
        raise ValueError(f"Unknown event_type: {event_type_str}")

    enum_map = _get_enum_coercion()

    # Build kwargs matching the dataclass fields
    kwargs: dict[str, Any] = {}
    cls_fields = {f.name: f for f in fields(cls)}
    for name, fld in cls_fields.items():
        if name == "event_type":
            continue  # auto-set by subclass
        if name not in data:
            continue
        val = data[name]
        ftype = fld.type if isinstance(fld.type, str) else ""

        # ── UUID coercion ────────────────────────────────
        if ftype in ("EventID", "CorrelationID", "OrderID", "TradeID") or (
            "UUID" in ftype
        ):
            val = uuid.UUID(val) if isinstance(val, str) else val

        # ── datetime coercion ────────────────────────────
        elif "datetime" in ftype.lower():
            val = datetime.fromisoformat(val) if isinstance(val, str) else val

        # ── Decimal / Money / Qty coercion ───────────────
        elif any(t in ftype for t in ("Decimal", "Money", "Qty")):
            if val is not None:
                val = Decimal(val) if not isinstance(val, Decimal) else val

        # ── Enum coercion ────────────────────────────────
        elif ftype in enum_map:
            if isinstance(val, str):
                val = enum_map[ftype](val)

        # ── Optional[Enum] coercion (e.g. "Money | None") ─
        else:
            for enum_name, enum_cls in enum_map.items():
                if enum_name in ftype and isinstance(val, str):
                    try:
                        val = enum_cls(val)
                    except (ValueError, KeyError):
                        pass
                    break

        kwargs[name] = val
    return cls(**kwargs)
