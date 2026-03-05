# tests/integration/test_cancel_flow.py
"""
Integration test — cancel order flow with fund unlock verification.

Validates (DSD §8):
  ✓ Canceling an open BUY order unlocks cash
  ✓ Canceling an open SELL order unlocks shares
  ✓ Cannot cancel a filled order
"""
import uuid
from decimal import Decimal

from src.account.account import AccountService
from src.app import AppContext
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.base_event import EventType
from src.events.order_events import (
    OrderPlacedEvent,
    OrderSide,
    OrderType,
)
from src.oms.order import OrderStatus


class TestCancelFlow:
    def test_cancel_buy_unlocks_cash(self):
        ctx = AppContext()
        buyer = ctx.account_service.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))

        oid = OrderID(uuid.uuid4())
        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="buyer",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
        )
        ctx.oms.place_order(evt)
        ctx.process_oms_events()

        # Before cancel: 50000 locked
        assert buyer.cash_available == Money(Decimal("50000"))
        assert buyer.cash_locked == Money(Decimal("50000"))

        # Cancel
        ctx.oms.cancel_order(oid)

        # After cancel: fully unlocked
        assert buyer.cash_available == Money(Decimal("100000"))
        assert buyer.cash_locked == Money(Decimal("0"))

        order = ctx.order_repo.get(oid)
        assert order.status == OrderStatus.CANCELED

    def test_cancel_sell_unlocks_shares(self):
        ctx = AppContext()
        seller = ctx.account_service.get_or_create("seller")
        pos = seller.get_position(Symbol("2330"))
        pos.qty_available = Qty(Decimal("200"))

        oid = OrderID(uuid.uuid4())
        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="seller",
            symbol="2330",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
        )
        ctx.oms.place_order(evt)
        ctx.process_oms_events()

        # Before cancel: 100 locked
        assert pos.qty_available == Qty(Decimal("100"))
        assert pos.qty_locked == Qty(Decimal("100"))

        ctx.oms.cancel_order(oid)

        # After cancel: fully unlocked
        assert pos.qty_available == Qty(Decimal("200"))
        assert pos.qty_locked == Qty(Decimal("0"))

    def test_cannot_cancel_filled_order(self):
        """Canceling a filled order returns False and publishes CancelRejectedEvent."""
        ctx = AppContext()
        acct_svc = ctx.account_service

        buyer = acct_svc.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))
        seller = acct_svc.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("100"))

        # Place sell then buy → immediate match
        sell_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=sell_oid, account_id="seller", symbol="2330",
            side=OrderSide.SELL, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        buy_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=buy_oid, account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        assert ctx.order_repo.get(sell_oid).status == OrderStatus.FILLED

        # Try to cancel the filled order — returns False, publishes CancelRejectedEvent
        result = ctx.oms.cancel_order(sell_oid)
        assert result is False
        assert ctx.order_repo.get(sell_oid).status == OrderStatus.FILLED
        cancel_events = ctx.oms.drain_events()
        assert any(e.event_type == EventType.CANCEL_REJECTED for e in cancel_events)
