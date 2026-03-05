# src/common/enums.py
"""
Canonical domain enumerations — the single source of truth.

Every module that needs OrderSide, OrderType, OrderStatus, or
RejectionReason MUST import from here (or from a re-exporting
module such as order_events / order that delegates here).

DSD §2.5: domain enums live in common, NOT in the event layer.
"""
from __future__ import annotations

from enum import Enum


class OrderSide(str, Enum):
    """Direction of an order."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order pricing strategy."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    """Lifecycle state of an order (FSM)."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    ROUTED = "ROUTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class RejectionReason(str, Enum):
    """Why an order was rejected."""
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    INVALID_ORDER = "INVALID_ORDER"
    SYSTEM_OVERLOAD = "SYSTEM_OVERLOAD"
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
