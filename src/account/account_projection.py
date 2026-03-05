# src/account/account_projection.py
"""
Account Projection – rebuilds account state entirely from events.

Design rules (DSD §4.3):
  • Account state MUST be fully reproducible by replaying events.
  • No hidden state – every mutation is traced to an event.
  • This module consumes: AccountUpdatedEvent, OrderPlacedEvent,
    OrderRejectedEvent, OrderCanceledEvent, TradeExecutedEvent.
"""
from __future__ import annotations

from decimal import Decimal

from src.account.account import AccountService
from src.common.types import Money, Qty, Symbol
from src.events.account_events import AccountUpdatedEvent, AccountDeletedEvent, TradeSettlementEvent
from src.events.base_event import BaseEvent
from src.common.enums import OrderSide
from src.events.order_events import (
    OrderCanceledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
)
from src.events.trade_events import TradeExecutedEvent


class AccountProjection:
    """
    Stateless projection: replay a list of events onto a fresh
    AccountService to reconstruct full account state.
    """

    def __init__(self, account_service: AccountService | None = None):
        self._svc = account_service or AccountService()

    @property
    def service(self) -> AccountService:
        return self._svc

    def apply(self, event: BaseEvent) -> None:
        """Apply a single event to the projection."""
        if isinstance(event, AccountUpdatedEvent):
            self._on_account_updated(event)
        elif isinstance(event, OrderPlacedEvent):
            self._on_order_placed(event)
        elif isinstance(event, OrderRejectedEvent):
            self._on_order_rejected(event)
        elif isinstance(event, OrderCanceledEvent):
            self._on_order_canceled(event)
        elif isinstance(event, TradeExecutedEvent):
            self._on_trade_executed(event)
        elif isinstance(event, TradeSettlementEvent):
            self._on_trade_settlement(event)
        elif isinstance(event, AccountDeletedEvent):
            self._on_account_deleted(event)
        # Other events (OrderAccepted, OrderRouted, OrderFilled,
        # MarketData) are informational for Account – no state change.

    def replay(self, events: list[BaseEvent]) -> AccountService:
        """Replay a full event log and return the resulting service."""
        for evt in events:
            self.apply(evt)
        return self._svc

    # ── Private handlers ─────────────────────────────────────

    def _on_account_updated(self, evt: AccountUpdatedEvent) -> None:
        """Handle deposit, withdrawal, and position seeding events."""
        acct = self._svc.get_or_create(evt.account_id)

        # Apply cash delta (positive = deposit, negative = withdrawal)
        if evt.cash_delta > 0:
            acct.deposit(Money(evt.cash_delta))
        elif evt.cash_delta < 0:
            acct.withdraw(Money(-evt.cash_delta))

        # Apply position qty delta (for initial share seeding)
        if evt.symbol and evt.qty_delta != 0:
            pos = acct.get_position(Symbol(evt.symbol))
            pos.qty_available = Qty(pos.qty_available + evt.qty_delta)

    def _on_order_placed(self, evt: OrderPlacedEvent) -> None:
        # Ensure the account exists (deposit may have already created it)
        self._svc.get_or_create(evt.account_id)
        # Locking is done by OMS calling AccountService.lock_funds,
        # which happens synchronously before OrderAcceptedEvent.
        # During replay we re-apply the lock here.
        try:
            self._svc.lock_funds(
                account_id=evt.account_id,
                order_id=evt.order_id,
                symbol=evt.symbol,
                side=evt.side,
                qty=evt.qty,
                price=evt.price,
            )
        except Exception:
            pass  # Will be followed by an OrderRejectedEvent

    def _on_order_rejected(self, evt: OrderRejectedEvent) -> None:
        # If funds were tentatively locked, unlock them.
        # During replay the order_id may not exist in locks if the
        # lock itself failed – unlock_funds handles that gracefully.
        # We need the account_id; during replay we can iterate.
        for acct in self._svc.list_all():
            if acct.has_order_lock(evt.order_id):
                self._svc.unlock_funds(acct.account_id, evt.order_id)
                return

    def _on_order_canceled(self, evt: OrderCanceledEvent) -> None:
        for acct in self._svc.list_all():
            if acct.has_order_lock(evt.order_id):
                self._svc.unlock_funds(acct.account_id, evt.order_id)
                return

    def _on_trade_executed(self, evt: TradeExecutedEvent) -> None:
        # TradeExecutedEvent is now INFORMATIONAL during replay.
        # Settlement is handled exclusively by TradeSettlementEvent
        # (DSD §1.6 #8, #15) to avoid double-settlement.
        pass

    def _find_account_for_order(self, order_id) -> str | None:
        for acct in self._svc.list_all():
            if acct.has_order_lock(order_id):
                return acct.account_id
        return None

    def _on_trade_settlement(self, evt: TradeSettlementEvent) -> None:
        """Handle explicit settlement event (DSD §1.6 #8, #15).

        This replays the settlement from the event log so account state
        is fully reproducible without relying on TradeExecutedEvent +
        order-lock heuristics.
        """
        self._svc.get_or_create(evt.buyer_account_id)
        self._svc.get_or_create(evt.seller_account_id)
        self._svc.settle_trade(
            buyer_account_id=evt.buyer_account_id,
            seller_account_id=evt.seller_account_id,
            buyer_order_id=evt.buyer_order_id,
            seller_order_id=evt.seller_order_id,
            symbol=evt.symbol,
            price=evt.price,
            qty=evt.qty,
        )

    def _on_account_deleted(self, evt: AccountDeletedEvent) -> None:
        """Remove account during replay (DSD §1.6 #4)."""
        self._svc.delete_account(evt.account_id)
