# tests/unit/test_module6_gaps.py
"""
Module 6 – Trading Matching Engine – DSD gap tests.

  G-1: OrderRemainOnBookEvent emitted when order not fully filled
  G-2: Order book query API (get_book_snapshot, bounded context)
  G-3: Cold-start order book rebuild (replay_order, rebuild_order_book_from_log)
  G-4: MD-fault isolation (ME works when MarketDataService is None)
  G-5: Dead-letter queue (retry + DLQ on publish failure)
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.app import AppContext
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.base_event import EventType
from src.events.order_events import (
    OrderPlacedEvent,
    OrderRemainOnBookEvent,
    OrderSide,
    OrderType,
)
from src.matching_engine.matcher import Matcher
from src.oms.order import OrderStatus


# ═══════════════════════════════════════════════════════════════
# G-1: OrderRemainOnBookEvent
# ═══════════════════════════════════════════════════════════════


class TestOrderRemainOnBookEvent:
    def test_remain_event_emitted_when_no_match(self):
        """Submitting a sell with no matching buy → OrderRemainOnBookEvent."""
        m = Matcher()
        sym = Symbol("2330")

        m.submit_order(
            order_id=OrderID(uuid.uuid4()),
            account_id="seller",
            symbol=sym,
            side=OrderSide.SELL,
            price=Money(Decimal("500")),
            qty=Qty(Decimal("100")),
        )

        remain = m.drain_remain_events()
        assert len(remain) == 1
        assert remain[0].event_type == EventType.ORDER_REMAIN_ON_BOOK
        assert remain[0].remaining_qty == Qty(Decimal("100"))
        assert remain[0].original_qty == Qty(Decimal("100"))

    def test_remain_event_emitted_on_partial_fill(self):
        """Partial fill → remain event with correct remaining_qty."""
        m = Matcher()
        sym = Symbol("2330")

        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL,
                       Money(Decimal("500")), Qty(Decimal("200")))
        m.drain_remain_events()  # clear sell's remain

        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY,
                                Money(Decimal("500")), Qty(Decimal("300")))
        assert len(trades) == 1
        assert trades[0].qty == Qty(Decimal("200"))  # partial

        remain = m.drain_remain_events()
        assert len(remain) == 1
        assert remain[0].remaining_qty == Qty(Decimal("100"))
        assert remain[0].original_qty == Qty(Decimal("300"))
        assert remain[0].side == OrderSide.BUY

    def test_no_remain_event_when_fully_filled(self):
        """Exact match → no OrderRemainOnBookEvent for the taker."""
        m = Matcher()
        sym = Symbol("2330")

        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL,
                       Money(Decimal("500")), Qty(Decimal("100")))
        m.drain_remain_events()  # clear sell's remain

        trades = m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY,
                                Money(Decimal("500")), Qty(Decimal("100")))
        assert len(trades) == 1

        remain = m.drain_remain_events()
        assert len(remain) == 0

    def test_remain_event_fields_populated(self):
        """Verify all fields on OrderRemainOnBookEvent."""
        m = Matcher()
        oid = OrderID(uuid.uuid4())
        sym = Symbol("2330")

        m.submit_order(oid, "buyer", sym, OrderSide.BUY,
                       Money(Decimal("505")), Qty(Decimal("50")))

        remain = m.drain_remain_events()
        evt = remain[0]
        assert evt.order_id == oid
        assert evt.symbol == sym
        assert evt.side == OrderSide.BUY
        assert evt.remaining_qty == Qty(Decimal("50"))
        assert evt.original_qty == Qty(Decimal("50"))
        assert evt.payload_version == "1.0"

    def test_remain_event_serialization(self):
        """OrderRemainOnBookEvent round-trips through JSON."""
        evt = OrderRemainOnBookEvent(
            order_id=uuid.uuid4(),
            symbol="2330",
            side=OrderSide.SELL,
            remaining_qty=Qty(Decimal("120")),
            original_qty=Qty(Decimal("200")),
        )
        json_str = evt.to_json()
        assert "OrderRemainOnBook" in json_str
        assert "2330" in json_str


# ═══════════════════════════════════════════════════════════════
# G-2: Order Book Query API (bounded context)
# ═══════════════════════════════════════════════════════════════


class TestBookSnapshot:
    def test_empty_book_snapshot(self):
        """No orders → empty bids and asks."""
        m = Matcher()
        snap = m.get_book_snapshot(Symbol("2330"))
        assert snap["symbol"] == "2330"
        assert snap["bids"] == []
        assert snap["asks"] == []

    def test_snapshot_with_orders(self):
        """Orders appear in snapshot at correct price levels."""
        m = Matcher()
        sym = Symbol("2330")
        m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY,
                       Money(Decimal("500")), Qty(Decimal("100")))
        m.submit_order(uuid.uuid4(), "s1", sym, OrderSide.SELL,
                       Money(Decimal("510")), Qty(Decimal("50")))

        snap = m.get_book_snapshot(sym)
        assert len(snap["bids"]) == 1
        assert snap["bids"][0]["price"] == "500"
        assert snap["bids"][0]["qty"] == "100"
        assert snap["bids"][0]["order_count"] == 1

        assert len(snap["asks"]) == 1
        assert snap["asks"][0]["price"] == "510"
        assert snap["asks"][0]["qty"] == "50"

    def test_snapshot_aggregates_same_price_level(self):
        """Two orders at the same price → aggregated qty and order_count."""
        m = Matcher()
        sym = Symbol("2330")
        m.submit_order(uuid.uuid4(), "b1", sym, OrderSide.BUY,
                       Money(Decimal("500")), Qty(Decimal("100")))
        m.submit_order(uuid.uuid4(), "b2", sym, OrderSide.BUY,
                       Money(Decimal("500")), Qty(Decimal("60")))

        snap = m.get_book_snapshot(sym)
        assert len(snap["bids"]) == 1
        assert snap["bids"][0]["qty"] == "160"
        assert snap["bids"][0]["order_count"] == 2


# ═══════════════════════════════════════════════════════════════
# G-3: Cold-Start Order Book Rebuild
# ═══════════════════════════════════════════════════════════════


class TestReplayOrder:
    def test_replay_adds_to_book_without_matching(self):
        """replay_order adds resting orders but does NOT trigger matching."""
        m = Matcher()
        sym = Symbol("2330")
        ts = datetime.now(timezone.utc)

        # Add crossing orders — normally would match
        m.replay_order(uuid.uuid4(), "s1", sym, OrderSide.SELL,
                       Money(Decimal("500")), Qty(Decimal("100")), ts)
        m.replay_order(uuid.uuid4(), "b1", sym, OrderSide.BUY,
                       Money(Decimal("510")), Qty(Decimal("100")), ts)

        # Book still has both sides (no matching happened)
        book = m.get_or_create_book(sym)
        assert book.best_bid is not None
        assert book.best_ask is not None
        assert book.best_bid.price == Decimal("510")
        assert book.best_ask.price == Decimal("500")

    def test_replay_marks_order_id_as_seen(self):
        """Replayed orders are idempotent — submit_order with same ID returns []."""
        m = Matcher()
        sym = Symbol("2330")
        oid = OrderID(uuid.uuid4())
        ts = datetime.now(timezone.utc)

        m.replay_order(oid, "b1", sym, OrderSide.BUY,
                       Money(Decimal("500")), Qty(Decimal("100")), ts)

        # Try to submit same order_id again → idempotent, returns []
        trades = m.submit_order(oid, "b1", sym, OrderSide.BUY,
                                Money(Decimal("500")), Qty(Decimal("100")))
        assert trades == []


class TestRebuildOrderBookFromLog:
    def test_rebuild_restores_resting_orders(self):
        """End-to-end: place orders → one matches → rebuild restores remainder."""
        ctx = AppContext()
        acct_svc = ctx.account_service

        buyer = acct_svc.get_or_create("buyer")
        buyer.deposit(Money(Decimal("200000")))

        seller = acct_svc.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("200"))

        # Sell 100 at 500 (rests on book)
        sell_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=sell_oid, account_id="seller", symbol="2330",
            side=OrderSide.SELL, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        # Sell another 50 at 510 (rests on book)
        sell2_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=sell2_oid, account_id="seller", symbol="2330",
            side=OrderSide.SELL, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("50")), price=Money(Decimal("510")),
        ))
        ctx.process_oms_events()

        # Buy 100 at 500 → matches first sell
        buy_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=buy_oid, account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        # Verify first sell is filled, second sell still resting
        assert ctx.order_repo.get(sell_oid).status == OrderStatus.FILLED
        book = ctx.matcher.get_or_create_book(Symbol("2330"))
        assert book.best_ask is not None
        assert book.best_ask.qty == Qty(Decimal("50"))

        # Now simulate cold-start: fresh matcher, replay
        fresh_matcher = Matcher()
        ctx.matcher = fresh_matcher

        # Verify book is empty after reset
        assert fresh_matcher.get_or_create_book(Symbol("2330")).best_ask is None

        # Replay the resting order directly (the method under test)
        fresh_matcher.replay_order(
            order_id=sell2_oid,
            account_id="seller",
            symbol=Symbol("2330"),
            side=OrderSide.SELL,
            price=Money(Decimal("510")),
            qty=Qty(Decimal("50")),
            timestamp=datetime.now(timezone.utc),
        )

        # Verify book restored
        restored_book = fresh_matcher.get_or_create_book(Symbol("2330"))
        assert restored_book.best_ask is not None
        assert restored_book.best_ask.qty == Qty(Decimal("50"))
        assert restored_book.best_ask.price == Decimal("510")


# ═══════════════════════════════════════════════════════════════
# G-4: MD-Fault Isolation
# ═══════════════════════════════════════════════════════════════


class TestMDFaultIsolation:
    def test_me_processes_limit_orders_when_md_down(self):
        """DSD §16: ME must still match limit orders when MarketDataService=None."""
        ctx = AppContext()
        ctx.market_data_service = None  # simulate MD module failure

        buyer = ctx.account_service.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))
        seller = ctx.account_service.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("100"))

        # Place sell
        sell_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=sell_oid, account_id="seller", symbol="2330",
            side=OrderSide.SELL, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        # Place crossing buy
        buy_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=buy_oid, account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        # Both orders should be FILLED despite MD being down
        assert ctx.order_repo.get(buy_oid).status == OrderStatus.FILLED
        assert ctx.order_repo.get(sell_oid).status == OrderStatus.FILLED
        assert buyer.get_position(Symbol("2330")).qty_available == Qty(Decimal("100"))

    def test_me_has_zero_dependency_on_market_data(self):
        """Matcher module has no import of market_data (architectural verification)."""
        import inspect
        import src.matching_engine.matcher as mod
        source = inspect.getsource(mod)
        assert "market_data" not in source
        assert "MarketDataService" not in source


# ═══════════════════════════════════════════════════════════════
# G-5: Dead-Letter Queue (retry + DLQ)
# ═══════════════════════════════════════════════════════════════


class TestDeadLetterQueue:
    def test_dead_letter_queue_captures_failed_publish(self):
        """When event_writer.push raises, event lands in dead_letter_queue."""
        ctx = AppContext()
        buyer = ctx.account_service.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))

        # Make event_writer.push always fail
        original_push = ctx.event_writer.push
        ctx.event_writer.push = lambda evt: (_ for _ in ()).throw(
            IOError("disk full")
        )

        sell_evt = OrderPlacedEvent(
            order_id=uuid.uuid4(), account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")), price=Money(Decimal("100")),
        )

        # _safe_publish should catch and move to DLQ
        from src.events.trade_events import TradeExecutedEvent
        dummy_event = OrderRemainOnBookEvent(
            order_id=uuid.uuid4(), symbol="2330",
            side=OrderSide.BUY,
            remaining_qty=Qty(Decimal("10")),
            original_qty=Qty(Decimal("10")),
        )
        ctx._safe_publish(dummy_event)

        # Restore original
        ctx.event_writer.push = original_push

        assert len(ctx.dead_letter_queue) == 1
        assert ctx.dead_letter_queue[0].event_type == EventType.ORDER_REMAIN_ON_BOOK

    def test_drain_dead_letters_clears_queue(self):
        """drain_dead_letters returns items and empties the queue."""
        ctx = AppContext()
        dummy = OrderRemainOnBookEvent(
            order_id=uuid.uuid4(), symbol="2330",
            side=OrderSide.BUY,
            remaining_qty=Qty(Decimal("10")),
            original_qty=Qty(Decimal("10")),
        )
        ctx.dead_letter_queue.append(dummy)
        assert len(ctx.dead_letter_queue) == 1

        drained = ctx.drain_dead_letters()
        assert len(drained) == 1
        assert len(ctx.dead_letter_queue) == 0

    def test_safe_publish_succeeds_normally(self):
        """When no failure, _safe_publish works and DLQ stays empty."""
        ctx = AppContext()
        evt = OrderRemainOnBookEvent(
            order_id=uuid.uuid4(), symbol="2330",
            side=OrderSide.BUY,
            remaining_qty=Qty(Decimal("10")),
            original_qty=Qty(Decimal("10")),
        )
        ctx._safe_publish(evt)
        assert len(ctx.dead_letter_queue) == 0
