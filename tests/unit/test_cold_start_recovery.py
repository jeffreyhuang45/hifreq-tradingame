# tests/unit/test_cold_start_recovery.py
"""
Cold-Start Recovery Flow — Comprehensive Tests.

Validates the 4-step cold-start specification:
  1. 讀取最新快照（如果存在）→ fast restore
  2. 重放事件日誌 → replay events after snapshot
  3. 狀態重建 → order book, account balances/positions, order status/trades
  4. 健康檢查 → verify readability, queues, consistency, block on failure

Coverage:
  A. Full flow: snapshot → replay → rebuild → health check
  B. Snapshot acceleration: only replays events after snapshot_ts
  C. State rebuild: order book, accounts, order status, trade records
  D. Trade settlement replay: buy/sell → positions and cash correct
  E. Idempotency: double cold_start is a no-op (G-10)
  F. Error counting: skipped events are counted (G-8)
  G. Health check extended: event_log_readable, internal_queues,
     order_book_consistency (G-2, G-11, G-12)
  H. Startup blocking: pre_flight failure blocks system (G-1)
  I. No double I/O: rebuild_order_book uses preloaded_events (G-4)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.app import AppContext
from src.common.enums import OrderSide, OrderStatus, OrderType
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.account_events import (
    AccountUpdatedEvent,
    TradeSettlementEvent,
)
from src.events.base_event import BaseEvent, _json_default
from src.events.order_events import (
    OrderAcceptedEvent,
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
    OrderRoutedEvent,
)
from src.events.trade_events import TradeExecutedEvent
from src.storage.event_loader import EventLoader
from src.storage.event_writer import EventWriter
from src.storage.snapshot import SnapshotManager


# ── Helpers ──────────────────────────────────────────────────

def _write_ndjson(path: Path, events: list[BaseEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(evt.to_json() + "\n")


def _make_ctx(tmp_path: Path) -> AppContext:
    return AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))


T_BASE = datetime(2025, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


def _full_trade_scenario(tmp_path: Path):
    """Create a full buy-sell-settle event log and return (ctx_args, oids)."""
    events_dir = tmp_path / "events"
    buyer_id = "buyer-1"
    seller_id = "seller-1"
    buy_oid = uuid.uuid4()
    sell_oid = uuid.uuid4()
    trade_id = uuid.uuid4()

    evts: list[BaseEvent] = [
        # 1. Deposit cash for buyer and seller
        AccountUpdatedEvent(
            account_id=buyer_id,
            cash_delta=Decimal("1000000"),
            occurred_at=T_BASE,
        ),
        AccountUpdatedEvent(
            account_id=seller_id,
            cash_delta=Decimal("500000"),
            occurred_at=T_BASE + timedelta(seconds=1),
        ),
        # 2. Seed seller with shares
        AccountUpdatedEvent(
            account_id=seller_id,
            symbol=Symbol("2330"),
            qty_delta=Qty(Decimal("1000")),
            occurred_at=T_BASE + timedelta(seconds=2),
        ),
        # 3. Place buy order
        OrderPlacedEvent(
            order_id=buy_oid,
            account_id=buyer_id,
            symbol=Symbol("2330"),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("600")),
            occurred_at=T_BASE + timedelta(seconds=3),
        ),
        OrderAcceptedEvent(
            order_id=buy_oid,
            occurred_at=T_BASE + timedelta(seconds=4),
        ),
        OrderRoutedEvent(
            order_id=buy_oid,
            symbol=Symbol("2330"),
            occurred_at=T_BASE + timedelta(seconds=5),
        ),
        # 4. Place sell order
        OrderPlacedEvent(
            order_id=sell_oid,
            account_id=seller_id,
            symbol=Symbol("2330"),
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("600")),
            occurred_at=T_BASE + timedelta(seconds=6),
        ),
        OrderAcceptedEvent(
            order_id=sell_oid,
            occurred_at=T_BASE + timedelta(seconds=7),
        ),
        OrderRoutedEvent(
            order_id=sell_oid,
            symbol=Symbol("2330"),
            occurred_at=T_BASE + timedelta(seconds=8),
        ),
        # 5. Trade executed
        TradeExecutedEvent(
            trade_id=trade_id,
            symbol=Symbol("2330"),
            price=Money(Decimal("600")),
            qty=Qty(Decimal("100")),
            buy_order_id=buy_oid,
            sell_order_id=sell_oid,
            maker_side=OrderSide.BUY,
            taker_side=OrderSide.SELL,
            occurred_at=T_BASE + timedelta(seconds=9),
        ),
        # 6. Order filled events
        OrderFilledEvent(
            order_id=buy_oid,
            filled_qty=Qty(Decimal("100")),
            cumulative_qty=Qty(Decimal("100")),
            status="FILLED",
            occurred_at=T_BASE + timedelta(seconds=10),
        ),
        OrderFilledEvent(
            order_id=sell_oid,
            filled_qty=Qty(Decimal("100")),
            cumulative_qty=Qty(Decimal("100")),
            status="FILLED",
            occurred_at=T_BASE + timedelta(seconds=11),
        ),
        # 7. Trade settlement
        TradeSettlementEvent(
            buyer_account_id=buyer_id,
            seller_account_id=seller_id,
            buyer_order_id=buy_oid,
            seller_order_id=sell_oid,
            symbol=Symbol("2330"),
            price=Money(Decimal("600")),
            qty=Qty(Decimal("100")),
            occurred_at=T_BASE + timedelta(seconds=12),
        ),
    ]

    log_path = events_dir / "2025-03" / "events-2025-03-01.log"
    _write_ndjson(log_path, evts)

    return {
        "events_dir": events_dir,
        "buyer_id": buyer_id,
        "seller_id": seller_id,
        "buy_oid": buy_oid,
        "sell_oid": sell_oid,
        "trade_id": trade_id,
        "num_events": len(evts),
    }


# ═══════════════════════════════════════════════════════════════
# A. Full flow: snapshot → replay → rebuild → health check
# ═══════════════════════════════════════════════════════════════

class TestFullColdStartFlow:

    def test_empty_system_cold_start_then_preflight(self, tmp_path):
        """Cold start on empty system succeeds and passes health check."""
        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=tmp_path / "events"),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )
        assert summary["events_replayed"] == 0
        assert summary["snapshot_loaded"] is False
        assert summary["errors_skipped"] == 0

        pf = ctx.pre_flight_check()
        assert pf["all_passed"] is True

    def test_full_trade_flow_cold_start(self, tmp_path):
        """Full buy-sell-settle scenario is correctly rebuilt from event log."""
        scenario = _full_trade_scenario(tmp_path)
        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=scenario["events_dir"]),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )
        assert summary["events_replayed"] == scenario["num_events"]
        assert summary["accounts_restored"] >= 2
        assert summary["orders_restored"] >= 2
        assert summary["errors_skipped"] == 0

        # Health check should pass
        pf = ctx.pre_flight_check()
        assert pf["all_passed"] is True

    def test_summary_keys_complete(self, tmp_path):
        """cold_start summary contains all expected keys."""
        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=tmp_path / "events"),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )
        expected = {"snapshot_loaded", "events_replayed", "accounts_restored",
                    "orders_restored", "resting_orders", "errors_skipped"}
        assert expected == set(summary.keys())


# ═══════════════════════════════════════════════════════════════
# B. Snapshot acceleration
# ═══════════════════════════════════════════════════════════════

class TestSnapshotAcceleration:

    def test_snapshot_skips_old_events(self, tmp_path):
        """Only events after snapshot_ts are replayed."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        t1 = T_BASE
        t_snap = T_BASE + timedelta(hours=2)
        t2 = T_BASE + timedelta(hours=4)

        evt_before = AccountUpdatedEvent(
            account_id="old", cash_delta=Decimal("100"), occurred_at=t1,
        )
        evt_after = AccountUpdatedEvent(
            account_id="new", cash_delta=Decimal("200"), occurred_at=t2,
        )
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log",
                      [evt_before, evt_after])

        sm = SnapshotManager(base_dir=snap_dir)
        sm.save({
            "snapshot_ts": t_snap.isoformat(),
            "accounts": [{"account_id": "old", "cash_available": "100"}],
        })

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=sm,
        )
        assert summary["snapshot_loaded"] is True
        assert summary["events_replayed"] == 1  # only evt_after

    def test_snapshot_restores_positions(self, tmp_path):
        """Snapshot restores position qty and cost before replaying events."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        # No events after snapshot
        (events_dir / "2025-03").mkdir(parents=True)
        (events_dir / "2025-03" / "events-2025-03-01.log").write_text("")

        sm = SnapshotManager(base_dir=snap_dir)
        sm.save({
            "snapshot_ts": (T_BASE + timedelta(hours=10)).isoformat(),
            "accounts": [{
                "account_id": "u1",
                "cash_available": "50000",
                "cash_locked": "10000",
                "positions": [{
                    "symbol": "2330",
                    "qty_available": "500",
                    "qty_locked": "100",
                    "total_cost": "300000",
                }],
            }],
        })

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=sm,
        )
        assert summary["snapshot_loaded"] is True

        acct = ctx.account_service.get_or_create("u1")
        assert acct.cash_available == Money(Decimal("50000"))
        assert acct.cash_locked == Money(Decimal("10000"))
        pos = acct.get_position(Symbol("2330"))
        assert pos.qty_available == Qty(Decimal("500"))
        assert pos.qty_locked == Qty(Decimal("100"))
        assert pos.total_cost == Money(Decimal("300000"))


# ═══════════════════════════════════════════════════════════════
# C. State rebuild: order book, accounts, order status
# ═══════════════════════════════════════════════════════════════

class TestStateRebuild:

    def test_order_book_resting_order_restored(self, tmp_path):
        """An open limit order is placed back on the ME book during cold start."""
        events_dir = tmp_path / "events"
        oid = uuid.uuid4()

        evts = [
            AccountUpdatedEvent(
                account_id="user1", cash_delta=Decimal("1000000"),
                occurred_at=T_BASE,
            ),
            OrderPlacedEvent(
                order_id=oid, account_id="user1",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("200")), price=Money(Decimal("550")),
                occurred_at=T_BASE + timedelta(seconds=1),
            ),
            OrderAcceptedEvent(order_id=oid, occurred_at=T_BASE + timedelta(seconds=2)),
            OrderRoutedEvent(order_id=oid, symbol=Symbol("2330"),
                             occurred_at=T_BASE + timedelta(seconds=3)),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        assert summary["resting_orders"] == 1

        # Verify the order is on the ME book
        snap = ctx.matcher.get_book_snapshot(Symbol("2330"))
        assert len(snap["bids"]) == 1
        assert snap["bids"][0]["price"] == "550"

    def test_canceled_order_not_on_book(self, tmp_path):
        """A canceled order should NOT be on the ME book after cold start."""
        events_dir = tmp_path / "events"
        oid = uuid.uuid4()

        evts = [
            AccountUpdatedEvent(
                account_id="user1", cash_delta=Decimal("1000000"),
                occurred_at=T_BASE,
            ),
            OrderPlacedEvent(
                order_id=oid, account_id="user1",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("200")), price=Money(Decimal("550")),
                occurred_at=T_BASE + timedelta(seconds=1),
            ),
            OrderCanceledEvent(order_id=oid, occurred_at=T_BASE + timedelta(seconds=2)),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        assert summary["resting_orders"] == 0
        order = ctx.order_repo.get(oid)
        assert order.status == OrderStatus.CANCELED

    def test_partially_filled_order_has_remaining_qty(self, tmp_path):
        """Partially filled order has correct remaining qty on book."""
        events_dir = tmp_path / "events"
        buy_oid = uuid.uuid4()
        sell_oid = uuid.uuid4()

        evts = [
            AccountUpdatedEvent(
                account_id="buyer", cash_delta=Decimal("1000000"),
                occurred_at=T_BASE,
            ),
            AccountUpdatedEvent(
                account_id="seller", cash_delta=Decimal("500000"),
                occurred_at=T_BASE + timedelta(seconds=1),
            ),
            AccountUpdatedEvent(
                account_id="seller", symbol=Symbol("2330"),
                qty_delta=Qty(Decimal("1000")),
                occurred_at=T_BASE + timedelta(seconds=2),
            ),
            OrderPlacedEvent(
                order_id=buy_oid, account_id="buyer",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("200")), price=Money(Decimal("600")),
                occurred_at=T_BASE + timedelta(seconds=3),
            ),
            OrderPlacedEvent(
                order_id=sell_oid, account_id="seller",
                symbol=Symbol("2330"), side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("50")), price=Money(Decimal("600")),
                occurred_at=T_BASE + timedelta(seconds=4),
            ),
            # Trade: 50 shares filled out of 200 buy order
            TradeExecutedEvent(
                trade_id=uuid.uuid4(),
                symbol=Symbol("2330"),
                price=Money(Decimal("600")),
                qty=Qty(Decimal("50")),
                buy_order_id=buy_oid,
                sell_order_id=sell_oid,
                occurred_at=T_BASE + timedelta(seconds=5),
            ),
            OrderFilledEvent(
                order_id=buy_oid,
                filled_qty=Qty(Decimal("50")),
                cumulative_qty=Qty(Decimal("50")),
                status="PARTIALLY_FILLED",
                occurred_at=T_BASE + timedelta(seconds=6),
            ),
            OrderFilledEvent(
                order_id=sell_oid,
                filled_qty=Qty(Decimal("50")),
                cumulative_qty=Qty(Decimal("50")),
                status="FILLED",
                occurred_at=T_BASE + timedelta(seconds=7),
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        # buy_oid should be resting with 150 remaining
        assert summary["resting_orders"] == 1
        book = ctx.matcher.get_book_snapshot(Symbol("2330"))
        assert len(book["bids"]) == 1
        assert book["bids"][0]["qty"] == "150"

        # Order status should be PARTIALLY_FILLED
        buy_order = ctx.order_repo.get(buy_oid)
        assert buy_order.status == OrderStatus.PARTIALLY_FILLED
        assert buy_order.filled_qty == Qty(Decimal("50"))

    def test_order_status_rebuilt_through_lifecycle(self, tmp_path):
        """All order status transitions are correctly replayed."""
        events_dir = tmp_path / "events"
        oid = uuid.uuid4()

        evts = [
            AccountUpdatedEvent(
                account_id="u1", cash_delta=Decimal("1000000"),
                occurred_at=T_BASE,
            ),
            OrderPlacedEvent(
                order_id=oid, account_id="u1",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")), price=Money(Decimal("500")),
                occurred_at=T_BASE + timedelta(seconds=1),
            ),
            OrderAcceptedEvent(order_id=oid, occurred_at=T_BASE + timedelta(seconds=2)),
            OrderRoutedEvent(order_id=oid, symbol=Symbol("2330"),
                             occurred_at=T_BASE + timedelta(seconds=3)),
            OrderFilledEvent(
                order_id=oid,
                filled_qty=Qty(Decimal("100")),
                cumulative_qty=Qty(Decimal("100")),
                status="FILLED",
                occurred_at=T_BASE + timedelta(seconds=4),
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        order = ctx.order_repo.get(oid)
        assert order.status == OrderStatus.FILLED
        assert order.filled_qty == Qty(Decimal("100"))


# ═══════════════════════════════════════════════════════════════
# D. Trade settlement replay — positions and cash
# ═══════════════════════════════════════════════════════════════

class TestTradeSettlementReplay:

    def test_buyer_receives_shares_after_settlement_replay(self, tmp_path):
        """After cold start with TradeSettlementEvent, buyer has shares."""
        scenario = _full_trade_scenario(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.cold_start(
            event_loader=EventLoader(base_dir=scenario["events_dir"]),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        buyer = ctx.account_service.get_or_create(scenario["buyer_id"])
        pos = buyer.get_position(Symbol("2330"))
        # Buyer should have 100 shares (from settlement)
        assert pos.total >= Decimal("100")

    def test_seller_shares_reduced_after_settlement_replay(self, tmp_path):
        """After cold start with TradeSettlementEvent, seller's shares are reduced."""
        scenario = _full_trade_scenario(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.cold_start(
            event_loader=EventLoader(base_dir=scenario["events_dir"]),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        seller = ctx.account_service.get_or_create(scenario["seller_id"])
        pos = seller.get_position(Symbol("2330"))
        # Seller started with 1000, sold 100 → should have ~900
        # (some qty may be locked during replay, but total should be 900)
        assert pos.total == Qty(Decimal("900"))

    def test_buyer_cash_reduced_after_settlement(self, tmp_path):
        """Buyer's cash is reduced by trade cost after settlement replay."""
        scenario = _full_trade_scenario(tmp_path)
        ctx = _make_ctx(tmp_path)
        ctx.cold_start(
            event_loader=EventLoader(base_dir=scenario["events_dir"]),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        buyer = ctx.account_service.get_or_create(scenario["buyer_id"])
        # Buyer deposited 1,000,000 and bought 100 shares @ 600 = 60,000 cost
        # Cash should be 1,000,000 - 60,000 = 940,000
        total_cash = buyer.cash_available + buyer.cash_locked
        assert total_cash == Money(Decimal("940000"))


# ═══════════════════════════════════════════════════════════════
# E. Idempotency (G-10)
# ═══════════════════════════════════════════════════════════════

class TestColdStartIdempotency:

    def test_double_cold_start_is_noop(self, tmp_path):
        """Calling cold_start() twice returns 'skipped' on second call."""
        events_dir = tmp_path / "events"
        evts = [
            AccountUpdatedEvent(
                account_id="u1", cash_delta=Decimal("500"),
                occurred_at=T_BASE,
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        loader = EventLoader(base_dir=events_dir)
        snap_mgr = SnapshotManager(base_dir=tmp_path / "snap")

        s1 = ctx.cold_start(event_loader=loader, snapshot_manager=snap_mgr)
        assert s1["events_replayed"] == 1

        s2 = ctx.cold_start(event_loader=loader, snapshot_manager=snap_mgr)
        assert s2.get("skipped") is True
        assert s2.get("reason") == "already_initialized"

    def test_account_not_double_deposited(self, tmp_path):
        """Double cold_start does not double-apply deposits."""
        events_dir = tmp_path / "events"
        evts = [
            AccountUpdatedEvent(
                account_id="u1", cash_delta=Decimal("1000"),
                occurred_at=T_BASE,
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        loader = EventLoader(base_dir=events_dir)
        snap_mgr = SnapshotManager(base_dir=tmp_path / "snap")

        ctx.cold_start(event_loader=loader, snapshot_manager=snap_mgr)
        acct = ctx.account_service.get_or_create("u1")
        cash_after_first = acct.cash_available

        ctx.cold_start(event_loader=loader, snapshot_manager=snap_mgr)
        assert acct.cash_available == cash_after_first  # unchanged


# ═══════════════════════════════════════════════════════════════
# F. Error counting (G-8)
# ═══════════════════════════════════════════════════════════════

class TestErrorCounting:

    def test_orphan_accepted_event_counted(self, tmp_path):
        """OrderAcceptedEvent for non-existent order is counted as error."""
        events_dir = tmp_path / "events"
        evts = [
            # OrderAccepted without preceding OrderPlaced
            OrderAcceptedEvent(
                order_id=uuid.uuid4(),
                occurred_at=T_BASE,
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        assert summary["errors_skipped"] == 1

    def test_orphan_filled_event_counted(self, tmp_path):
        """OrderFilledEvent for non-existent order is counted as error."""
        events_dir = tmp_path / "events"
        evts = [
            OrderFilledEvent(
                order_id=uuid.uuid4(),
                filled_qty=Qty(Decimal("100")),
                cumulative_qty=Qty(Decimal("100")),
                status="FILLED",
                occurred_at=T_BASE,
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        assert summary["errors_skipped"] == 1

    def test_multiple_orphan_events_all_counted(self, tmp_path):
        """Multiple orphan status events are each counted."""
        events_dir = tmp_path / "events"
        evts = [
            OrderAcceptedEvent(order_id=uuid.uuid4(), occurred_at=T_BASE),
            OrderRejectedEvent(order_id=uuid.uuid4(), occurred_at=T_BASE + timedelta(seconds=1)),
            OrderCanceledEvent(order_id=uuid.uuid4(), occurred_at=T_BASE + timedelta(seconds=2)),
            OrderRoutedEvent(order_id=uuid.uuid4(), symbol=Symbol("2330"),
                             occurred_at=T_BASE + timedelta(seconds=3)),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        assert summary["errors_skipped"] == 4


# ═══════════════════════════════════════════════════════════════
# G. Extended health checks (G-2, G-11, G-12)
# ═══════════════════════════════════════════════════════════════

class TestExtendedHealthChecks:

    def test_all_check_keys_present(self, tmp_path):
        """pre_flight_check returns all 7 check keys + all_passed."""
        ctx = _make_ctx(tmp_path)
        pf = ctx.pre_flight_check()
        expected = {
            "event_log_dir", "event_writer", "matching_engine",
            "account_service", "event_log_readable", "internal_queues",
            "order_book_consistency", "all_passed",
        }
        assert expected == set(pf.keys())

    def test_event_log_readable_on_valid_logs(self, tmp_path):
        """event_log_readable is True when log files exist and are readable."""
        evt_dir = tmp_path / "events"
        _write_ndjson(
            evt_dir / "2025-03" / "events-2025-03-01.log",
            [AccountUpdatedEvent(account_id="u1", cash_delta=Decimal("100"))],
        )

        ctx = AppContext(event_writer=EventWriter(base_dir=evt_dir))
        pf = ctx.pre_flight_check()
        assert pf["event_log_readable"] is True

    def test_internal_queues_healthy(self, tmp_path):
        """internal_queues check verifies dead-letter, OMS, and ME queues."""
        ctx = _make_ctx(tmp_path)
        pf = ctx.pre_flight_check()
        assert pf["internal_queues"] is True

    def test_order_book_consistency_passes_on_clean_state(self, tmp_path):
        """Consistency check passes when no orders exist."""
        ctx = _make_ctx(tmp_path)
        pf = ctx.pre_flight_check()
        assert pf["order_book_consistency"] is True

    def test_order_book_consistency_after_cold_start(self, tmp_path):
        """Consistency check passes after a proper cold start with orders."""
        events_dir = tmp_path / "events"
        oid = uuid.uuid4()
        evts = [
            AccountUpdatedEvent(
                account_id="u1", cash_delta=Decimal("1000000"),
                occurred_at=T_BASE,
            ),
            OrderPlacedEvent(
                order_id=oid, account_id="u1",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")), price=Money(Decimal("500")),
                occurred_at=T_BASE + timedelta(seconds=1),
            ),
            OrderAcceptedEvent(order_id=oid, occurred_at=T_BASE + timedelta(seconds=2)),
            OrderRoutedEvent(order_id=oid, symbol=Symbol("2330"),
                             occurred_at=T_BASE + timedelta(seconds=3)),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        ctx = _make_ctx(tmp_path)
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )

        pf = ctx.pre_flight_check()
        assert pf["order_book_consistency"] is True
        assert pf["all_passed"] is True


# ═══════════════════════════════════════════════════════════════
# H. Startup blocking (G-1)
# ═══════════════════════════════════════════════════════════════

class TestStartupBlocking:

    def test_preflight_returns_all_passed_true_on_healthy(self, tmp_path):
        """Healthy system returns all_passed=True."""
        ctx = _make_ctx(tmp_path)
        pf = ctx.pre_flight_check()
        assert pf["all_passed"] is True

    def test_preflight_failure_logged(self, tmp_path):
        """When a check fails, all_passed is False and failed checks are identified."""
        ctx = _make_ctx(tmp_path)
        # Sabotage: replace matcher with an object whose method raises
        class BrokenMatcher:
            def get_book_snapshot(self, symbol):
                raise RuntimeError("ME is dead")
            def get_book_depths(self):
                raise RuntimeError("ME is dead")
            def drain_remain_events(self):
                raise RuntimeError("ME is dead")
        ctx.matcher = BrokenMatcher()  # type: ignore
        pf = ctx.pre_flight_check()
        assert pf["all_passed"] is False
        assert pf["matching_engine"] is False

    def test_create_app_startup_would_raise_on_failure(self):
        """Verify the startup hook checks all_passed and raises on failure."""
        # We test the code path in create_app's startup handler by
        # verifying it calls pre_flight_check and reacts to failures.
        # (Full integration would need async event loop — here we verify
        #  the pre_flight_check contract.)
        import inspect
        from src.app import create_app

        # Inspect the startup coroutine source for RuntimeError raise
        source = inspect.getsource(create_app)
        assert "RuntimeError" in source
        assert "Pre-flight check FAILED" in source


# ═══════════════════════════════════════════════════════════════
# I. No double I/O (G-4)
# ═══════════════════════════════════════════════════════════════

class TestNoDoubleIO:

    def test_rebuild_order_book_accepts_preloaded_events(self, tmp_path):
        """rebuild_order_book_from_log works with preloaded_events kwarg."""
        oid = uuid.uuid4()
        from src.events.order_events import OrderPlacedEvent

        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="u1",
            symbol=Symbol("2330"),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
            occurred_at=T_BASE,
        )

        ctx = _make_ctx(tmp_path)
        count = ctx.rebuild_order_book_from_log(preloaded_events=[evt])
        assert count == 1

        snap = ctx.matcher.get_book_snapshot(Symbol("2330"))
        assert len(snap["bids"]) == 1

    def test_rebuild_with_empty_preloaded_events(self, tmp_path):
        """rebuild_order_book_from_log with empty list returns 0."""
        ctx = _make_ctx(tmp_path)
        count = ctx.rebuild_order_book_from_log(preloaded_events=[])
        assert count == 0

    def test_cold_start_uses_preloaded_events_internally(self, tmp_path):
        """cold_start passes events to rebuild_order_book (no double I/O).

        Verify by checking that rebuild_order_book_from_log's preloaded_events
        path is exercised during cold_start.
        """
        events_dir = tmp_path / "events"
        oid = uuid.uuid4()
        evts = [
            OrderPlacedEvent(
                order_id=oid, account_id="u1",
                symbol=Symbol("2330"), side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")), price=Money(Decimal("500")),
                occurred_at=T_BASE,
            ),
        ]
        _write_ndjson(events_dir / "2025-03" / "events-2025-03-01.log", evts)

        # Sabotage: create EventLoader that only works once
        # If cold_start reads from disk twice, the second read will fail
        class OneTimeLoader(EventLoader):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._call_count = 0

            def load_events(self):
                self._call_count += 1
                if self._call_count > 1:
                    raise RuntimeError("load_events called more than once!")
                return super().load_events()

        ctx = _make_ctx(tmp_path)
        loader = OneTimeLoader(base_dir=events_dir)
        summary = ctx.cold_start(
            event_loader=loader,
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snap"),
        )
        # Should succeed — rebuild_order_book uses preloaded events
        assert summary["resting_orders"] == 1
        # Confirm loader was only called once
        assert loader._call_count == 1
