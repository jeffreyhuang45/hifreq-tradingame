# src/events/trade_events.py
"""Trade execution events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from src.common.enums import OrderSide
from src.common.types import Money, OrderID, Qty, Symbol, TradeID
from src.events.base_event import BaseEvent, EventType, register_event


@register_event
@dataclass(frozen=True)
class TradeExecutedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.TRADE_EXECUTED, init=False)
    trade_id: TradeID = field(default_factory=lambda: UUID(int=0))
    symbol: Symbol = field(default="")
    price: Money = field(default=Money(0))
    qty: Qty = field(default=Qty(0))
    buy_order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    sell_order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    maker_side: OrderSide = field(default=OrderSide.BUY)
    taker_side: OrderSide = field(default=OrderSide.SELL)
    matched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
