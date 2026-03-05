# src/events/account_events.py
"""Account state-change events."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from src.common.types import AccountID, Money, OrderID, Qty, Symbol
from src.events.base_event import BaseEvent, EventType, register_event


@register_event
@dataclass(frozen=True)
class AccountUpdatedEvent(BaseEvent):
    event_type: EventType = field(default=EventType.ACCOUNT_UPDATED, init=False)
    account_id: AccountID = field(default="")
    cash_delta: Money = field(default=Money(0))
    locked_delta: Money = field(default=Money(0))
    symbol: Symbol = field(default="")
    qty_delta: Qty = field(default=Qty(0))


@register_event
@dataclass(frozen=True)
class TradeSettlementEvent(BaseEvent):
    """Emitted when a trade is settled between buyer and seller accounts.

    Makes trade settlement fully replayable from the event log (DSD §1.6 #8, #15).
    """
    event_type: EventType = field(default=EventType.TRADE_SETTLEMENT, init=False)
    buyer_account_id: AccountID = field(default="")
    seller_account_id: AccountID = field(default="")
    buyer_order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    seller_order_id: OrderID = field(default_factory=lambda: UUID(int=0))
    symbol: Symbol = field(default="")
    price: Money = field(default=Money(0))
    qty: Qty = field(default=Qty(0))


@register_event
@dataclass(frozen=True)
class AccountDeletedEvent(BaseEvent):
    """Emitted when an account is deleted (DSD §1.6 #4: every mutation → event).

    On replay, the account is removed so cold-start state matches runtime.
    """
    event_type: EventType = field(default=EventType.ACCOUNT_DELETED, init=False)
    account_id: AccountID = field(default="")
