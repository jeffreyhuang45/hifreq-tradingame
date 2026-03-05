# tests/unit/test_oms.py
"""
Unit tests for OMS (Task #2).

Validates:
  ✓ State machine transitions (valid & invalid)
  ✓ Risk checks (insufficient cash / position → REJECTED)
  ✓ Idempotent event processing
  ✓ Cancel flow
"""
import uuid
from decimal import Decimal

import pytest

from src.account.account import AccountService
from src.common.errors import InvalidOrderStateError
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.base_event import EventType
from src.events.order_events import (
    OrderPlacedEvent,
    OrderSide,
    OrderType,
)
from src.oms.oms_service import OMSService
from src.oms.order import OrderStatus
from src.oms.order_repository import OrderRepository
from src.oms.order_state_machine import OrderStateMachine


class TestOrderStateMachine:
    def test_valid_transition_pending_to_accepted(self):
        from src.oms.order import Order
        order = Order(
            order_id=uuid.uuid4(),
            account_id="a1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
        )
        OrderStateMachine.transition(order, OrderStatus.ACCEPTED)
        assert order.status == OrderStatus.ACCEPTED

    def test_invalid_transition_raises(self):
        from src.oms.order import Order
        order = Order(
            order_id=uuid.uuid4(),
            account_id="a1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
        )
        with pytest.raises(InvalidOrderStateError):
            OrderStateMachine.transition(order, OrderStatus.FILLED)

    def test_terminal_states_cannot_transition(self):
        from src.oms.order import Order
        order = Order(
            order_id=uuid.uuid4(),
            account_id="a1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
            status=OrderStatus.FILLED,
        )
        with pytest.raises(InvalidOrderStateError):
            OrderStateMachine.transition(order, OrderStatus.CANCELED)


class TestOMSService:
    def _make_oms(self) -> tuple[OMSService, AccountService]:
        acct_svc = AccountService()
        repo = OrderRepository()
        oms = OMSService(order_repository=repo, fund_checker=acct_svc)
        return oms, acct_svc

    def test_place_order_accepted(self):
        oms, acct_svc = self._make_oms()
        acct = acct_svc.get_or_create("user1")
        acct.deposit(Money(Decimal("100000")))

        oid = OrderID(uuid.uuid4())
        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")),
            price=Money(Decimal("500")),
        )
        oms.place_order(evt)
        events = oms.drain_events()

        types = [e.event_type for e in events]
        assert EventType.ORDER_ACCEPTED in types
        assert EventType.ORDER_ROUTED in types

    def test_place_order_rejected_insufficient_cash(self):
        oms, acct_svc = self._make_oms()
        acct_svc.get_or_create("user1")  # no deposit → 0 cash

        evt = OrderPlacedEvent(
            order_id=uuid.uuid4(),
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")),
            price=Money(Decimal("500")),
        )
        oms.place_order(evt)
        events = oms.drain_events()

        types = [e.event_type for e in events]
        assert EventType.ORDER_REJECTED in types

    def test_idempotent_place_order(self):
        oms, acct_svc = self._make_oms()
        acct = acct_svc.get_or_create("user1")
        acct.deposit(Money(Decimal("100000")))

        evt = OrderPlacedEvent(
            order_id=uuid.uuid4(),
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")),
            price=Money(Decimal("500")),
        )
        oms.place_order(evt)
        oms.drain_events()

        # Process the same event again
        oms.place_order(evt)
        events = oms.drain_events()
        assert len(events) == 0  # no duplicate processing

    def test_cancel_order(self):
        oms, acct_svc = self._make_oms()
        acct = acct_svc.get_or_create("user1")
        acct.deposit(Money(Decimal("100000")))

        oid = OrderID(uuid.uuid4())
        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")),
            price=Money(Decimal("500")),
        )
        oms.place_order(evt)
        oms.drain_events()

        oms.cancel_order(oid)
        events = oms.drain_events()
        types = [e.event_type for e in events]
        assert EventType.ORDER_CANCELED in types

        # Cash should be unlocked
        assert acct.cash_available == Money(Decimal("100000"))
