# src/oms/oms_service.py
"""
OMS Service – Order Management System.
Single authority over order lifecycle.
Does NOT import account or matching_engine modules directly.
Communicates via events only.
"""
from __future__ import annotations

import uuid
from collections import deque
from decimal import Decimal
from typing import Protocol

from src.common.errors import (
    InsufficientCashError,
    InsufficientPositionError,
    InvalidOrderStateError,
)
from src.common.enums import OrderSide, RejectionReason
from src.common.types import AccountID, Money, OrderID, Qty, Symbol
from src.events.base_event import BaseEvent
from src.events.order_events import (
    CancelRejectedEvent,
    OrderAcceptedEvent,
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
    OrderRoutedEvent,
)
from src.events.trade_events import TradeExecutedEvent
from src.oms.order import Order, OrderStatus
from src.oms.order_repository import OrderRepository
from src.oms.order_state_machine import OrderStateMachine


class FundChecker(Protocol):
    """Thin protocol so OMS never imports the Account module."""

    def lock_funds(
        self,
        account_id: AccountID,
        order_id: OrderID,
        symbol: Symbol,
        side: OrderSide,
        qty: Qty,
        price: Money | None,
    ) -> None: ...

    def unlock_funds(
        self,
        account_id: AccountID,
        order_id: OrderID,
    ) -> None: ...


class OMSService:
    """
    Order Management System service.
    Manages the full order lifecycle and publishes events.
    """

    def __init__(
        self,
        order_repository: OrderRepository,
        fund_checker: FundChecker,
    ):
        self._order_repo = order_repository
        self._fund_checker = fund_checker
        self._event_log: deque[BaseEvent] = deque()
        self._processed_event_ids: set[uuid.UUID] = set()  # idempotency guard

        # ── Observability counters (M-4) ─────────────────────
        self.accepted_count: int = 0
        self.rejected_count: int = 0
        self.trade_count: int = 0

    # ── Commands ──────────────────────────────────────────────

    def place_order(self, event: OrderPlacedEvent) -> None:
        """Handle an OrderPlacedEvent: validate, lock funds, route."""
        if event.event_id in self._processed_event_ids:
            return  # idempotent – already processed
        self._processed_event_ids.add(event.event_id)

        order = Order(
            order_id=event.order_id,
            account_id=event.account_id,
            symbol=event.symbol,
            side=event.side,
            order_type=event.order_type,
            qty=event.qty,
            price=event.price,
        )
        self._order_repo.add(order)

        try:
            self._fund_checker.lock_funds(
                account_id=order.account_id,
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=order.price,
            )
        except (InsufficientCashError, InsufficientPositionError) as exc:
            OrderStateMachine.transition(order, OrderStatus.REJECTED)
            reason = (
                RejectionReason.INSUFFICIENT_CASH
                if isinstance(exc, InsufficientCashError)
                else RejectionReason.INSUFFICIENT_POSITION
            )
            self._publish(
                OrderRejectedEvent(
                    order_id=order.order_id,
                    reason=reason,
                    correlation_id=event.correlation_id,
                )
            )
            self.rejected_count += 1
            return

        # Accepted
        OrderStateMachine.transition(order, OrderStatus.ACCEPTED)
        self._publish(
            OrderAcceptedEvent(
                order_id=order.order_id,
                correlation_id=event.correlation_id,
            )
        )
        self.accepted_count += 1

        # Route to matching engine
        OrderStateMachine.transition(order, OrderStatus.ROUTED)
        self._publish(
            OrderRoutedEvent(
                order_id=order.order_id,
                symbol=order.symbol,
                correlation_id=event.correlation_id,
            )
        )

    def cancel_order(self, order_id: OrderID) -> bool:
        """Cancel an open order and unlock funds. Returns False if not cancelable."""
        order = self._order_repo.get(order_id)
        if not order.is_cancelable:
            self._publish(CancelRejectedEvent(
                order_id=order.order_id,
                reason=f"Order in {order.status.value} state cannot be canceled",
            ))
            return False
        self._fund_checker.unlock_funds(order.account_id, order.order_id)
        OrderStateMachine.transition(order, OrderStatus.CANCELED)
        self._publish(OrderCanceledEvent(order_id=order.order_id))
        return True

    def on_trade_executed(self, event: TradeExecutedEvent) -> None:
        """React to a TradeExecutedEvent from the Matching Engine."""
        if event.event_id in self._processed_event_ids:
            return
        self._processed_event_ids.add(event.event_id)
        self.trade_count += 1

        for oid in (event.buy_order_id, event.sell_order_id):
            order = self._order_repo.get(oid)
            order.filled_qty = Qty(order.filled_qty + event.qty)
            if order.filled_qty >= order.qty:
                OrderStateMachine.transition(order, OrderStatus.FILLED)
            elif order.status == OrderStatus.ROUTED:
                OrderStateMachine.transition(order, OrderStatus.PARTIALLY_FILLED)

            # Emit OrderFilledEvent so fill state is replayable (C-4 fix)
            self._publish(OrderFilledEvent(
                order_id=oid,
                filled_qty=event.qty,
                cumulative_qty=order.filled_qty,
                status=order.status.value,
                correlation_id=event.correlation_id,
            ))

    def on_market_fill(
        self,
        order_id: OrderID,
        fill_qty: Qty,
        fill_price: "Money",
    ) -> None:
        """Handle a fill from Engine A (order matched against market data).

        Unlike on_trade_executed, this only updates one order (no counter-party).
        """
        order = self._order_repo.get(order_id)
        order.filled_qty = Qty(order.filled_qty + fill_qty)
        if order.filled_qty >= order.qty:
            OrderStateMachine.transition(order, OrderStatus.FILLED)
        elif order.status == OrderStatus.ROUTED:
            OrderStateMachine.transition(order, OrderStatus.PARTIALLY_FILLED)
        self.trade_count += 1

        self._publish(OrderFilledEvent(
            order_id=order_id,
            filled_qty=fill_qty,
            cumulative_qty=order.filled_qty,
            status=order.status.value,
        ))

    # ── Event bus helpers ─────────────────────────────────────

    def _publish(self, event: BaseEvent) -> None:
        self._event_log.append(event)

    def drain_events(self) -> list[BaseEvent]:
        """Return and clear pending events."""
        events = list(self._event_log)
        self._event_log.clear()
        return events
