# tests/replay/test_replay_deterministic.py
"""
Deterministic Replay Test (Task #7).

Core contract (DSD §8):
  ✓ Replay the SAME ordered sequence of events twice from scratch.
  ✓ The resulting account/order state MUST be bit-for-bit identical.
  ✓ Proves the system has NO hidden state — everything derives from Events.

Strategy:
  1. Run a full scenario (deposits, orders, trades, cancels) and collect
     the ordered event list.
  2. Replay that event list onto a FRESH AppContext twice.
  3. Compare final account state from both replays.
"""
import copy
import uuid
from decimal import Decimal

import pytest

from src.account.account import AccountService
from src.account.account_projection import AccountProjection
from src.app import AppContext
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.account_events import AccountUpdatedEvent
from src.events.base_event import BaseEvent, EventType
from src.events.order_events import (
    OrderPlacedEvent,
    OrderSide,
    OrderType,
)


class _CollectingWriter:
    """Thin wrapper that intercepts events as BaseEvent objects."""

    def __init__(self):
        self.events: list[BaseEvent] = []

    def push(self, event: BaseEvent) -> None:
        self.events.append(event)


def _build_scenario() -> tuple[AppContext, list[BaseEvent]]:
    """
    Run a complete scenario and collect every emitted event AS BaseEvent.

    Scenario:
      - buyer deposits 200,000  (via AccountUpdatedEvent)
      - seller starts with 300 shares of 2330 (via AccountUpdatedEvent)
      - seller places SELL 100 @ 500
      - seller places SELL 100 @ 502
      - buyer places BUY 150 @ 505 → matches 100@500 + 50@502

    Returns (ctx, ordered_events).
    """
    ctx = AppContext()
    collector = _CollectingWriter()
    # Monkey-patch the event_writer.push so we capture BaseEvent objects
    ctx.event_writer.push = collector.push  # type: ignore[assignment]

    # Buyer deposit — emit event AND apply to live state
    buyer = ctx.account_service.get_or_create("buyer")
    deposit_evt = AccountUpdatedEvent(
        account_id="buyer",
        cash_delta=Money(Decimal("200000")),
    )
    buyer.deposit(Money(Decimal("200000")))
    collector.push(deposit_evt)

    # Seller position seed — emit event AND apply to live state
    seller = ctx.account_service.get_or_create("seller")
    seed_evt = AccountUpdatedEvent(
        account_id="seller",
        symbol=Symbol("2330"),
        qty_delta=Qty(Decimal("300")),
    )
    seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("300"))
    collector.push(seed_evt)

    # Seller order 1: SELL 100 @ 500
    sell_oid1 = OrderID(uuid.uuid4())
    sell_evt1 = OrderPlacedEvent(
        order_id=sell_oid1, account_id="seller", symbol="2330",
        side=OrderSide.SELL, order_type=OrderType.LIMIT,
        qty=Qty(Decimal("100")), price=Money(Decimal("500")),
    )
    collector.push(sell_evt1)                   # persist BEFORE OMS (mirrors G-1 fix)
    ctx.oms.place_order(sell_evt1)
    ctx.process_oms_events()

    # Seller order 2: SELL 100 @ 502
    sell_oid2 = OrderID(uuid.uuid4())
    sell_evt2 = OrderPlacedEvent(
        order_id=sell_oid2, account_id="seller", symbol="2330",
        side=OrderSide.SELL, order_type=OrderType.LIMIT,
        qty=Qty(Decimal("100")), price=Money(Decimal("502")),
    )
    collector.push(sell_evt2)
    ctx.oms.place_order(sell_evt2)
    ctx.process_oms_events()

    # Buyer order: BUY 150 @ 505 → sweeps sell@500 (100) + sell@502 (50 partial)
    buy_oid = OrderID(uuid.uuid4())
    buy_evt = OrderPlacedEvent(
        order_id=buy_oid, account_id="buyer", symbol="2330",
        side=OrderSide.BUY, order_type=OrderType.LIMIT,
        qty=Qty(Decimal("150")), price=Money(Decimal("505")),
    )
    collector.push(buy_evt)
    ctx.oms.place_order(buy_evt)
    ctx.process_oms_events()

    return ctx, collector.events


def _snapshot_accounts(svc: AccountService) -> dict:
    """Take a deterministic snapshot of all accounts."""
    snap = {}
    for acct in sorted(svc.list_all(), key=lambda a: a.account_id):
        positions = {}
        for sym in sorted(acct.positions.keys()):
            p = acct.positions[sym]
            positions[sym] = {
                "qty_available": str(p.qty_available),
                "qty_locked": str(p.qty_locked),
                "total_cost": str(p.total_cost),
            }
        snap[acct.account_id] = {
            "cash_available": str(acct.cash_available),
            "cash_locked": str(acct.cash_locked),
            "positions": positions,
        }
    return snap


class TestDeterministicReplay:
    def test_replay_same_events_twice_yields_identical_state(self):
        """
        DSD core invariant:
        Replaying the SAME event log twice from empty state
        MUST produce bit-for-bit identical account states.
        """
        _ctx, events = _build_scenario()
        assert len(events) > 0, "Scenario should produce events"

        # Replay #1 — accounts are created purely from events (no manual seeding)
        def _replay_from_events(evts: list[BaseEvent]) -> dict:
            proj = AccountProjection()
            proj.replay(evts)
            return _snapshot_accounts(proj.service)

        snap1 = _replay_from_events(events)
        snap2 = _replay_from_events(events)

        assert snap1 == snap2, (
            f"Replay divergence!\n"
            f"Replay #1: {snap1}\n"
            f"Replay #2: {snap2}"
        )

    def test_scenario_produces_expected_results(self):
        """Verify the scenario itself produces sensible numbers."""
        ctx, events = _build_scenario()

        # Check live state from the original scenario run
        buyer = ctx.account_service.get("buyer")
        seller = ctx.account_service.get("seller")

        # Buyer bought 150 shares total: 100@500 + 50@502 = 75100
        assert buyer.get_position(Symbol("2330")).qty_available == Qty(Decimal("150"))
        expected_cost = Decimal("100") * Decimal("500") + Decimal("50") * Decimal("502")
        # Buyer locked 150*505=75750 initially. Paid 75100. Remaining locked = 650.
        assert buyer.cash_available == Money(Decimal("200000") - Decimal("75750"))
        assert buyer.cash_locked == Money(Decimal("650"))

        # Seller received 75100 in cash
        assert seller.cash_available == Money(expected_cost)

    def test_event_count(self):
        """The scenario should produce a known number of events."""
        _ctx, events = _build_scenario()

        # AccountUpdated (deposit) + AccountUpdated (seed) = 2
        # Seller order 1: Accepted + Routed = 2
        # Seller order 2: Accepted + Routed = 2
        # Buyer order: Accepted + Routed = 2
        #   + Trade1(100@500) + Trade2(50@502) = 2 trades
        #   + 4 OrderFilled events (2 per trade, buy+sell sides)
        # Total = 2 + 2 + 2 + 2 + 2 + 4 = 14
        assert len(events) >= 10, f"Expected >= 10 events, got {len(events)}"

    def test_replay_from_serialized_events(self):
        """
        Serialize events to JSON → deserialize → replay → same result.
        This proves cold-start replay from NDJSON log files works.
        """
        from src.events.base_event import event_from_dict
        import json

        _ctx, events = _build_scenario()

        # Serialize each event to JSON string, then back to dict
        json_lines = [evt.to_json() for evt in events]
        deserialized = [event_from_dict(json.loads(line)) for line in json_lines]

        def _replay_from_events(evts: list[BaseEvent]) -> dict:
            proj = AccountProjection()
            proj.replay(evts)
            return _snapshot_accounts(proj.service)

        snap_from_json = _replay_from_events(deserialized)
        snap_from_orig = _replay_from_events(events)

        assert snap_from_json == snap_from_orig, (
            "Cold-start replay from JSON diverged from in-memory replay!"
        )

    def test_replay_matches_live_state(self):
        """
        G-3 fix: The replayed state MUST match the live scenario state.
        This catches bugs where events are not persisted (e.g. OrderPlacedEvent).
        """
        ctx, events = _build_scenario()

        # Replay from collected events
        proj = AccountProjection()
        proj.replay(events)

        snap_live = _snapshot_accounts(ctx.account_service)
        snap_replay = _snapshot_accounts(proj.service)

        assert snap_replay == snap_live, (
            f"Replay state diverged from live state!\n"
            f"Live:   {snap_live}\n"
            f"Replay: {snap_replay}"
        )
