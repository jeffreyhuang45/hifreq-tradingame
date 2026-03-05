# tests/unit/test_matching_engine.py
"""
Unit tests for Matching Engine (Task #3).

Validates:
  ✓ Price priority (better price matched first)
  ✓ Time priority (FIFO at same price)
  ✓ Trade price = Maker (resting order) price
  ✓ Partial fills
  ✓ No double match (idempotent order_id)
  ✓ Cancel removes from book
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.common.types import Money, OrderID, Qty, Symbol, TradeID
from src.events.order_events import OrderSide
from src.matching_engine.matcher import Matcher


class TestPriceTimePriority:
    def test_exact_match(self):
        m = Matcher()
        sym = Symbol("2330")

        # Resting sell at 500
        trades = m.submit_order(
            order_id=OrderID(uuid.uuid4()),
            account_id="seller",
            symbol=sym,
            side=OrderSide.SELL,
            price=Money(Decimal("500")),
            qty=Qty(Decimal("100")),
        )
        assert trades == []  # no match yet

        # Incoming buy at 500
        trades = m.submit_order(
            order_id=OrderID(uuid.uuid4()),
            account_id="buyer",
            symbol=sym,
            side=OrderSide.BUY,
            price=Money(Decimal("500")),
            qty=Qty(Decimal("100")),
        )
        assert len(trades) == 1
        t = trades[0]
        assert t.price == Money(Decimal("500"))
        assert t.qty == Qty(Decimal("100"))

    def test_price_priority_buy_gets_best_ask(self):
        m = Matcher()
        sym = Symbol("2330")

        # Two sells: 502 and 500
        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL, Money(Decimal("502")), Qty(Decimal("50")))
        m.submit_order(uuid.uuid4(), "s2", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("50")))

        # Buy at 505 → should match with 500 first (lowest ask)
        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY, Money(Decimal("505")), Qty(Decimal("100")))
        assert len(trades) == 2
        assert trades[0].price == Money(Decimal("500"))  # best ask matched first
        assert trades[1].price == Money(Decimal("502"))  # next best ask

    def test_time_priority_fifo(self):
        m = Matcher()
        sym = Symbol("2330")

        # Two sells at same price, different times
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        m.submit_order(first_id, "s1", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("50")))
        m.submit_order(second_id, "s2", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("50")))

        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY, Money(Decimal("500")), Qty(Decimal("50")))
        assert len(trades) == 1
        assert trades[0].sell_order_id == first_id  # FIFO

    def test_partial_fill(self):
        m = Matcher()
        sym = Symbol("2330")

        sell_id = uuid.uuid4()
        m.submit_order(sell_id, "s1", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("200")))

        # Buy only 80 → partial fill
        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY, Money(Decimal("500")), Qty(Decimal("80")))
        assert len(trades) == 1
        assert trades[0].qty == Qty(Decimal("80"))

        # Remaining sell qty should be 120
        book = m.get_or_create_book(sym)
        assert book.best_ask is not None
        assert book.best_ask.qty == Qty(Decimal("120"))

    def test_no_match_when_price_doesnt_cross(self):
        m = Matcher()
        sym = Symbol("2330")

        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL, Money(Decimal("510")), Qty(Decimal("100")))
        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY, Money(Decimal("500")), Qty(Decimal("100")))
        assert trades == []  # no match

    def test_idempotent_order_id(self):
        m = Matcher()
        sym = Symbol("2330")
        oid = uuid.uuid4()

        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("100")))

        trades1 = m.submit_order(oid, "b1", sym, OrderSide.BUY, Money(Decimal("500")), Qty(Decimal("100")))
        trades2 = m.submit_order(oid, "b1", sym, OrderSide.BUY, Money(Decimal("500")), Qty(Decimal("100")))

        assert len(trades1) == 1
        assert len(trades2) == 0  # idempotent

    def test_cancel_order(self):
        m = Matcher()
        sym = Symbol("2330")
        oid = uuid.uuid4()

        m.submit_order(oid, "s1", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("100")))
        assert m.cancel_order(sym, oid) is True

        book = m.get_or_create_book(sym)
        assert book.best_ask is None  # removed

    def test_trade_price_equals_maker(self):
        """Trade price = the resting (maker) order's price."""
        m = Matcher()
        sym = Symbol("2330")

        # Seller rests at 500 (maker)
        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL, Money(Decimal("500")), Qty(Decimal("100")))

        # Buyer comes in at 510 (taker) → should match at 500 (maker price)
        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY, Money(Decimal("510")), Qty(Decimal("100")))
        assert trades[0].price == Money(Decimal("500"))
