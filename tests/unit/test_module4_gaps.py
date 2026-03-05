# tests/unit/test_module4_gaps.py
"""
Tests for Module 4 (OMS) gap fixes:
  G-1: Circuit Breaker rejects MARKET orders when MD is unavailable
  G-2: CancelRejectedEvent published for non-cancelable orders
  G-3: Bounded context documentation (verified by existence)
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from src.account.account import AccountService
from src.common.circuit_breaker import CircuitBreaker, CircuitState
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.base_event import EventType
from src.events.order_events import (
    CancelRejectedEvent,
    OrderPlacedEvent,
    OrderSide,
    OrderType,
    RejectionReason,
)
from src.oms.order import Order, OrderStatus
from src.oms.order_repository import OrderRepository
from src.oms.order_state_machine import OrderStateMachine
from src.oms.oms_service import OMSService


# ═══════════════════════════════════════════════════════════════
# G-1: Circuit Breaker rejects MARKET orders
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreakerMarketOrders:
    def test_rejection_reason_enum_exists(self):
        """MARKET_DATA_UNAVAILABLE rejection reason must be in the enum."""
        assert RejectionReason.MARKET_DATA_UNAVAILABLE == "MARKET_DATA_UNAVAILABLE"

    def test_circuit_breaker_is_open_property(self):
        """Verify CB exposes is_open correctly."""
        from src.common.circuit_breaker import CircuitOpenError

        cb = CircuitBreaker(name="test", failure_threshold=2)
        assert cb.is_open is False
        assert cb.state == CircuitState.CLOSED

        # Force OPEN by triggering failures
        for _ in range(3):
            try:
                cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            except (RuntimeError, CircuitOpenError):
                pass
        assert cb.is_open is True


# ═══════════════════════════════════════════════════════════════
# G-2: CancelRejectedEvent for non-cancelable orders
# ═══════════════════════════════════════════════════════════════

class TestCancelRejection:
    def _make_oms(self) -> tuple[OMSService, AccountService, OrderRepository]:
        acct_svc = AccountService()
        repo = OrderRepository()
        oms = OMSService(order_repository=repo, fund_checker=acct_svc)
        return oms, acct_svc, repo

    def test_cancel_rejected_event_published_for_filled_order(self):
        """Canceling a FILLED order publishes CancelRejectedEvent."""
        oms, acct_svc, repo = self._make_oms()

        # Create and manually fill an order
        oid = OrderID(uuid.uuid4())
        order = Order(
            order_id=oid,
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
            status=OrderStatus.FILLED,
        )
        repo.add(order)

        result = oms.cancel_order(oid)
        assert result is False

        events = oms.drain_events()
        assert len(events) == 1
        assert events[0].event_type == EventType.CANCEL_REJECTED
        assert isinstance(events[0], CancelRejectedEvent)
        assert "FILLED" in events[0].reason

    def test_cancel_rejected_event_published_for_rejected_order(self):
        """Canceling a REJECTED order publishes CancelRejectedEvent."""
        oms, acct_svc, repo = self._make_oms()

        oid = OrderID(uuid.uuid4())
        order = Order(
            order_id=oid,
            account_id="user1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
            status=OrderStatus.REJECTED,
        )
        repo.add(order)

        result = oms.cancel_order(oid)
        assert result is False

        events = oms.drain_events()
        assert len(events) == 1
        assert events[0].event_type == EventType.CANCEL_REJECTED

    def test_successful_cancel_returns_true(self):
        """A valid cancel on a ROUTED order returns True."""
        oms, acct_svc, repo = self._make_oms()
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
        oms.drain_events()  # clear accept/route events

        result = oms.cancel_order(oid)
        assert result is True

        events = oms.drain_events()
        assert any(e.event_type == EventType.ORDER_CANCELED for e in events)

    def test_cancel_rejected_event_serialization(self):
        """CancelRejectedEvent can be serialized and deserialized."""
        import json
        from src.events.base_event import event_from_dict

        evt = CancelRejectedEvent(
            order_id=OrderID(uuid.uuid4()),
            reason="Order in FILLED state cannot be canceled",
        )
        json_str = evt.to_json()
        data = json.loads(json_str)
        restored = event_from_dict(data)

        assert restored.event_type == EventType.CANCEL_REJECTED
        assert restored.order_id == evt.order_id
        assert restored.reason == evt.reason


# ═══════════════════════════════════════════════════════════════
# G-3: Bounded Context (docstring verification)
# ═══════════════════════════════════════════════════════════════

class TestBoundedContext:
    def test_order_docstring_documents_bounded_context(self):
        """Order class docstring must document bounded context constraint."""
        assert "Bounded Context" in Order.__doc__
        assert "OMS" in Order.__doc__
