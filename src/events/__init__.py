# src/events/__init__.py
"""
Canonical Event Package (DSD §2.6).

Importing this package guarantees that **every** event class is
registered in the ``_EVENT_REGISTRY``, regardless of which
individual submodule the caller imports.

Usage:
    from src.events import event_from_dict, get_event_registry
    from src.events import OrderPlacedEvent, TradeExecutedEvent  # etc.

The full event catalog:
    13 event types across 4 submodules (order, trade, market, account).
"""

# ── Base infrastructure ──────────────────────────────────────
from src.events.base_event import (            # noqa: F401
    BaseEvent,
    EventType,
    event_from_dict,
    get_event_registry,
    register_event,
    _json_default,
)

# ── Order lifecycle events ───────────────────────────────────
from src.events.order_events import (          # noqa: F401
    CancelRejectedEvent,
    OrderAcceptedEvent,
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
    OrderRemainOnBookEvent,
    OrderRoutedEvent,
)

# ── Trade events ─────────────────────────────────────────────
from src.events.trade_events import (          # noqa: F401
    TradeExecutedEvent,
)

# ── Market data events ───────────────────────────────────────
from src.events.market_events import (         # noqa: F401
    MarketDataUpdatedEvent,
)

# ── Account events ───────────────────────────────────────────
from src.events.account_events import (        # noqa: F401
    AccountDeletedEvent,
    AccountUpdatedEvent,
    TradeSettlementEvent,
)

__all__ = [
    # Base
    "BaseEvent",
    "EventType",
    "event_from_dict",
    "get_event_registry",
    "register_event",
    # Order events
    "OrderPlacedEvent",
    "OrderAcceptedEvent",
    "OrderRejectedEvent",
    "OrderCanceledEvent",
    "OrderRoutedEvent",
    "OrderFilledEvent",
    "CancelRejectedEvent",
    "OrderRemainOnBookEvent",
    # Trade events
    "TradeExecutedEvent",
    # Market events
    "MarketDataUpdatedEvent",
    # Account events
    "AccountUpdatedEvent",
    "TradeSettlementEvent",
    "AccountDeletedEvent",
]
