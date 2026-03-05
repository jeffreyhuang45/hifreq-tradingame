# src/events/order_events.py
"""Order lifecycle events."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from src.common.enums import OrderSide, OrderType, RejectionReason  # canonical
from src.common.types import AccountID, Money, OrderID, Qty, Symbol
from src.events.base_event import BaseEvent, EventType, register_event

# Re-export so existing ``from src.events.order_events import OrderSide``
# keeps working everywhere without changes.
__all__ = [
    "OrderSide",
    "OrderType",
    "RejectionReason",
    "OrderPlacedEvent",
    "OrderAcceptedEvent",
    "OrderRejectedEvent",
    "OrderCanceledEvent",
    "OrderRoutedEvent",
    "OrderFilledEvent",
    "CancelRejectedEvent",
    "OrderRemainOnBookEvent",
]


@register_event
@dataclass(frozen=True)
class OrderPlacedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ORDER_PLACED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    account_id: AccountID = field(default="")
    symbol: Symbol = field(default="")
    side: OrderSide = field(default=OrderSide.BUY)
    order_type: OrderType = field(default=OrderType.LIMIT)
    qty: Qty = field(default=Qty(0))
    price: Money | None = field(default=None)


@register_event
@dataclass(frozen=True)
class OrderAcceptedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ORDER_ACCEPTED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))


@register_event
@dataclass(frozen=True)
class OrderRejectedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ORDER_REJECTED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    reason: RejectionReason = field(default=RejectionReason.INVALID_ORDER)


@register_event
@dataclass(frozen=True)
class OrderCanceledEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ORDER_CANCELED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))


@register_event
@dataclass(frozen=True)
class OrderRoutedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ORDER_ROUTED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    symbol: Symbol = field(default="")


@register_event
@dataclass(frozen=True)
class OrderFilledEvent(BaseEvent):
    """Emitted by OMS when a trade execution updates order filled_qty / status."""
    event_type: EventType = field(default=EventType.ORDER_FILLED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    filled_qty: Qty = field(default=Qty(0))       # qty filled in this trade
    cumulative_qty: Qty = field(default=Qty(0))    # total filled so far
    status: str = field(default="PARTIALLY_FILLED")  # PARTIALLY_FILLED or FILLED


@register_event
@dataclass(frozen=True)
class CancelRejectedEvent(BaseEvent):
    """Emitted when a cancel request is rejected (order not in cancelable state)."""
    event_type: EventType = field(default=EventType.CANCEL_REJECTED, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    reason: str = field(default="Order is not in a cancelable state")


@register_event
@dataclass(frozen=True)
class OrderRemainOnBookEvent(BaseEvent):
    """Emitted by ME when an order is not fully filled and remains on the book (DSD §9)."""
    event_type: EventType = field(default=EventType.ORDER_REMAIN_ON_BOOK, init=False)
    order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    symbol: Symbol = field(default="")
    side: OrderSide = field(default=OrderSide.BUY)
    remaining_qty: Qty = field(default=Qty(0))
    original_qty: Qty = field(default=Qty(0))
