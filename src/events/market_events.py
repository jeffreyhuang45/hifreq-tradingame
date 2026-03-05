# src/events/market_events.py
"""Market data events."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.common.types import Money, Qty, Symbol
from src.events.base_event import BaseEvent, EventType, register_event


@register_event
@dataclass(frozen=True)
class MarketDataUpdatedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.MARKET_DATA_UPDATED, init=False)
    symbol: Symbol = field(default="")
    bid_price: Money = field(default=Money(0))
    bid_qty: Qty = field(default=Qty(0))
    ask_price: Money = field(default=Money(0))
    ask_qty: Qty = field(default=Qty(0))
    last_trade_price: Money | None = field(default=None)
    last_trade_qty: Qty | None = field(default=None)
    volume: Qty = field(default=Qty(0))
    trades_count: int = field(default=0)
