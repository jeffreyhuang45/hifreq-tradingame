# tests/unit/test_module7_gaps.py
"""
Module 7 – Storage Engine – DSD gap tests.

  G-1: Full cold-start orchestrator (snapshot → replay → rebuild all)
  G-2: Pre-flight health check
  G-3: Periodic report export (ReportWriter → disk)
  G-4: EventLoader snapshot-aware loading (load_after / load_events_after)
  G-5: Qty import in app.py (latent NameError fixed)
  G-6: Unit coverage for Storage Engine (EventWriter, EventLoader, SnapshotManager)
"""
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.app import AppContext
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.account_events import AccountUpdatedEvent
from src.events.base_event import BaseEvent, EventType, _json_default
from src.events.order_events import (
    OrderAcceptedEvent,
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
    OrderRemainOnBookEvent,
    OrderRoutedEvent,
    OrderSide,
    OrderType,
)
from src.events.trade_events import TradeExecutedEvent
from src.oms.order import OrderStatus
from src.storage.event_loader import EventLoader
from src.storage.event_writer import EventWriter
from src.storage.report_writer import ReportWriter
from src.storage.snapshot import SnapshotManager


# ── Helpers ──────────────────────────────────────────────────


def _make_order_placed(
    order_id: OrderID | None = None,
    account_id: str = "acct-1",
    symbol: str = "2330",
    side: OrderSide = OrderSide.BUY,
    qty: Decimal = Decimal("100"),
    price: Decimal = Decimal("500"),
    occurred_at: datetime | None = None,
) -> OrderPlacedEvent:
    return OrderPlacedEvent(
        order_id=order_id or uuid.uuid4(),
        account_id=account_id,
        symbol=Symbol(symbol),
        side=side,
        order_type=OrderType.LIMIT,
        qty=Qty(qty),
        price=Money(price),
        **({"occurred_at": occurred_at} if occurred_at else {}),
    )


def _make_trade(
    buy_order_id: OrderID,
    sell_order_id: OrderID,
    symbol: str = "2330",
    price: Decimal = Decimal("500"),
    qty: Decimal = Decimal("50"),
    occurred_at: datetime | None = None,
) -> TradeExecutedEvent:
    return TradeExecutedEvent(
        trade_id=uuid.uuid4(),
        symbol=Symbol(symbol),
        price=Money(price),
        qty=Qty(qty),
        buy_order_id=buy_order_id,
        sell_order_id=sell_order_id,
        **({"occurred_at": occurred_at} if occurred_at else {}),
    )


def _write_ndjson(path: Path, events: list[BaseEvent]) -> None:
    """Write events to an NDJSON file (helper for test fixtures)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(evt.to_json() + "\n")


# ═══════════════════════════════════════════════════════════════
# G-6 Core: EventWriter unit tests
# ═══════════════════════════════════════════════════════════════


class TestEventWriter:
    def test_push_and_flush(self, tmp_path):
        """push() buffers, flush() writes to NDJSON file."""
        ew = EventWriter(base_dir=tmp_path, batch_size=10)
        evt = AccountUpdatedEvent(account_id="test", cash_delta=Decimal("1000"))
        ew.push(evt)
        count = ew.flush()
        assert count == 1

        # Verify file on disk
        log_files = list(tmp_path.rglob("events-*.log"))
        assert len(log_files) == 1
        lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        d = json.loads(lines[0])
        assert d["event_type"] == EventType.ACCOUNT_UPDATED.value

    def test_batch_auto_flush(self, tmp_path):
        """push() never flushes inline (DSD §1.6 critical-path safety).
        All events stay in buffer until explicit flush() or background thread.
        """
        ew = EventWriter(base_dir=tmp_path, batch_size=3)
        for i in range(5):
            ew.push(AccountUpdatedEvent(account_id=f"a{i}", cash_delta=Decimal(i)))
        # push() no longer auto-flushes at batch_size; all 5 remain
        remaining = ew.flush()
        assert remaining == 5

        log_files = list(tmp_path.rglob("events-*.log"))
        assert len(log_files) == 1
        lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5

    def test_flush_empty_returns_zero(self, tmp_path):
        """Flushing an empty buffer returns 0."""
        ew = EventWriter(base_dir=tmp_path)
        assert ew.flush() == 0

    def test_start_stop_background(self, tmp_path):
        """Background thread starts and stops cleanly."""
        ew = EventWriter(base_dir=tmp_path, flush_interval_s=0.1)
        ew.start_background()
        assert ew._running is True
        ew.push(AccountUpdatedEvent(account_id="bg", cash_delta=Decimal("1")))
        ew.stop()
        assert ew._running is False
        # Final flush happened in stop()
        log_files = list(tmp_path.rglob("events-*.log"))
        assert len(log_files) >= 1


# ═══════════════════════════════════════════════════════════════
# G-6 Core: EventLoader unit tests
# ═══════════════════════════════════════════════════════════════


class TestEventLoader:
    def test_load_all_empty_dir(self, tmp_path):
        """load_all returns [] when no event files exist."""
        loader = EventLoader(base_dir=tmp_path / "nonexistent")
        assert loader.load_all() == []

    def test_load_all_reads_ndjson(self, tmp_path):
        """load_all reads events from NDJSON log files."""
        evt1 = AccountUpdatedEvent(account_id="a1", cash_delta=Decimal("100"))
        evt2 = AccountUpdatedEvent(account_id="a2", cash_delta=Decimal("200"))
        log_path = tmp_path / "2025-01" / "events-2025-01-15.log"
        _write_ndjson(log_path, [evt1, evt2])

        loader = EventLoader(base_dir=tmp_path)
        dicts = loader.load_all()
        assert len(dicts) == 2
        assert dicts[0]["account_id"] == "a1"
        assert dicts[1]["account_id"] == "a2"

    def test_load_events_deserializes(self, tmp_path):
        """load_events returns BaseEvent instances."""
        evt = AccountUpdatedEvent(account_id="x", cash_delta=Decimal("50"))
        log_path = tmp_path / "2025-06" / "events-2025-06-01.log"
        _write_ndjson(log_path, [evt])

        loader = EventLoader(base_dir=tmp_path)
        events = loader.load_events()
        assert len(events) == 1
        assert isinstance(events[0], AccountUpdatedEvent)

    def test_load_events_skips_malformed(self, tmp_path):
        """Malformed JSON lines are skipped without error."""
        log_path = tmp_path / "2025-01" / "events-2025-01-01.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("NOT-JSON\n")
            f.write('{"event_type": "NONEXISTENT_TYPE"}\n')
        loader = EventLoader(base_dir=tmp_path)
        assert loader.load_events() == []


# ═══════════════════════════════════════════════════════════════
# G-4: EventLoader snapshot-aware loading
# ═══════════════════════════════════════════════════════════════


class TestEventLoaderAfter:
    def test_load_after_filters_by_timestamp(self, tmp_path):
        """load_after returns only events after the given timestamp."""
        t1 = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2025, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

        evt1 = AccountUpdatedEvent(account_id="a1", cash_delta=Decimal("100"), occurred_at=t1)
        evt2 = AccountUpdatedEvent(account_id="a2", cash_delta=Decimal("200"), occurred_at=t2)
        evt3 = AccountUpdatedEvent(account_id="a3", cash_delta=Decimal("300"), occurred_at=t3)

        log_path = tmp_path / "2025-01" / "events-2025-01-01.log"
        _write_ndjson(log_path, [evt1, evt2, evt3])

        loader = EventLoader(base_dir=tmp_path)
        # cutoff at t2 — only t3 should be returned
        result = loader.load_after(t2)
        assert len(result) == 1
        assert result[0]["account_id"] == "a3"

    def test_load_events_after_deserializes(self, tmp_path):
        """load_events_after returns deserialized BaseEvent instances."""
        t1 = datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2025, 6, 1, 16, 0, 0, tzinfo=timezone.utc)

        evt1 = AccountUpdatedEvent(account_id="old", cash_delta=Decimal("10"), occurred_at=t1)
        evt2 = AccountUpdatedEvent(account_id="new", cash_delta=Decimal("20"), occurred_at=t2)

        log_path = tmp_path / "2025-06" / "events-2025-06-01.log"
        _write_ndjson(log_path, [evt1, evt2])

        loader = EventLoader(base_dir=tmp_path)
        events = loader.load_events_after(t1)
        assert len(events) == 1
        assert isinstance(events[0], AccountUpdatedEvent)
        assert events[0].account_id == "new"

    def test_load_after_empty_when_all_before(self, tmp_path):
        """load_after returns [] when all events are before cutoff."""
        t_old = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        t_cutoff = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        evt = AccountUpdatedEvent(account_id="x", cash_delta=Decimal("1"), occurred_at=t_old)
        log_path = tmp_path / "2025-01" / "events-2025-01-01.log"
        _write_ndjson(log_path, [evt])

        loader = EventLoader(base_dir=tmp_path)
        assert loader.load_after(t_cutoff) == []


# ═══════════════════════════════════════════════════════════════
# G-6 Core: SnapshotManager unit tests
# ═══════════════════════════════════════════════════════════════


class TestSnapshotManager:
    def test_save_and_load(self, tmp_path):
        """save() writes JSON, load_latest() reads it back."""
        sm = SnapshotManager(base_dir=tmp_path)
        state = {
            "snapshot_ts": datetime.now(timezone.utc).isoformat(),
            "accounts": [{"account_id": "acc1", "cash_available": "100000"}],
        }
        path = sm.save(state)
        assert path.exists()

        loaded = sm.load_latest()
        assert loaded is not None
        assert loaded["accounts"][0]["account_id"] == "acc1"

    def test_load_latest_no_snapshots(self, tmp_path):
        """load_latest returns None when no snapshots exist."""
        sm = SnapshotManager(base_dir=tmp_path)
        assert sm.load_latest() is None

    def test_load_latest_nonexistent_dir(self):
        """load_latest returns None for a nonexistent directory."""
        sm = SnapshotManager(base_dir="/tmp/__no_exist__")
        assert sm.load_latest() is None

    def test_save_handles_special_types(self, tmp_path):
        """Decimal, UUID, datetime are serialized in snapshots."""
        sm = SnapshotManager(base_dir=tmp_path)
        state = {
            "snapshot_ts": datetime.now(timezone.utc).isoformat(),
            "value": Decimal("123.45"),
            "id": uuid.uuid4(),
        }
        path = sm.save(state)
        loaded = sm.load_latest()
        assert loaded is not None
        assert loaded["value"] == "123.45"  # Decimal → str


# ═══════════════════════════════════════════════════════════════
# G-3: ReportWriter unit tests
# ═══════════════════════════════════════════════════════════════


class TestReportWriter:
    def test_export_now_trades(self, tmp_path):
        """export_now writes trades CSV to disk."""
        trades = [
            {
                "trade_id": "t1",
                "symbol": "2330",
                "price": "500",
                "qty": "100",
                "buy_order_id": "b1",
                "sell_order_id": "s1",
                "matched_at": "2025-01-01T10:00:00",
            }
        ]
        rw = ReportWriter(
            base_dir=tmp_path,
            trades_provider=lambda: trades,
            orders_provider=lambda: [],
        )
        result = rw.export_now()
        assert result["trades"] is not None
        assert result["trades"].exists()

        content = result["trades"].read_text(encoding="utf-8")
        assert "2330" in content
        assert "500" in content

    def test_export_now_orders(self, tmp_path):
        """export_now writes orders CSV to disk."""
        orders = [
            {
                "order_id": "o1",
                "account_id": "acc1",
                "symbol": "2330",
                "side": "BUY",
                "order_type": "LIMIT",
                "qty": "100",
                "price": "500",
                "status": "FILLED",
                "filled_qty": "100",
            }
        ]
        rw = ReportWriter(
            base_dir=tmp_path,
            trades_provider=lambda: [],
            orders_provider=lambda: orders,
        )
        result = rw.export_now()
        assert result["orders"] is not None
        assert result["orders"].exists()

        content = result["orders"].read_text(encoding="utf-8")
        assert "acc1" in content
        assert "FILLED" in content

    def test_export_now_no_data(self, tmp_path):
        """export_now returns None paths when no data."""
        rw = ReportWriter(
            base_dir=tmp_path,
            trades_provider=lambda: [],
            orders_provider=lambda: [],
        )
        result = rw.export_now()
        assert result["trades"] is None
        assert result["orders"] is None

    def test_export_now_no_providers(self, tmp_path):
        """export_now handles None providers gracefully."""
        rw = ReportWriter(base_dir=tmp_path)
        result = rw.export_now()
        assert result["trades"] is None
        assert result["orders"] is None

    def test_directory_layout(self, tmp_path):
        """Report files use YYYY-MM directory layout."""
        trades = [{"trade_id": "t1", "symbol": "X", "price": "1", "qty": "1",
                    "buy_order_id": "b", "sell_order_id": "s", "matched_at": "now"}]
        rw = ReportWriter(base_dir=tmp_path, trades_provider=lambda: trades)
        result = rw.export_now()
        assert result["trades"] is not None
        # Path should contain YYYY-MM directory
        parts = result["trades"].parts
        month_dir = parts[-2]  # YYYY-MM
        assert len(month_dir) == 7  # e.g. "2025-06"
        assert "-" in month_dir


# ═══════════════════════════════════════════════════════════════
# G-1: Cold-start orchestrator
# ═══════════════════════════════════════════════════════════════


class TestColdStart:
    def test_cold_start_no_events(self, tmp_path):
        """cold_start with empty event log returns zero counts."""
        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=tmp_path / "events"),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )
        assert summary["events_replayed"] == 0
        assert summary["orders_restored"] == 0
        assert summary["resting_orders"] == 0
        assert summary["snapshot_loaded"] is False

    def test_cold_start_replays_deposit_and_order(self, tmp_path):
        """cold_start replays account + order events, rebuilding state."""
        events_dir = tmp_path / "events"

        # Write events to disk
        oid = uuid.uuid4()
        t_base = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        evts: list[BaseEvent] = [
            AccountUpdatedEvent(
                account_id="user1",
                cash_delta=Decimal("1000000"),
                occurred_at=t_base,
            ),
            OrderPlacedEvent(
                order_id=oid,
                account_id="user1",
                symbol=Symbol("2330"),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")),
                price=Money(Decimal("500")),
                occurred_at=t_base + timedelta(seconds=1),
            ),
        ]
        log_path = events_dir / "2025-01" / "events-2025-01-15.log"
        _write_ndjson(log_path, evts)

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )

        assert summary["events_replayed"] == 2
        assert summary["accounts_restored"] >= 1
        assert summary["orders_restored"] >= 1

        # Order should be in the repo
        order = ctx.order_repo.get(oid)
        assert order.symbol == Symbol("2330")

    def test_cold_start_with_snapshot(self, tmp_path):
        """cold_start with snapshot replays only events after snapshot ts."""
        events_dir = tmp_path / "events"
        snap_dir = tmp_path / "snapshots"

        t1 = datetime(2025, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        t_snap = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2025, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

        # Events: one before snapshot, one after
        evt_before = AccountUpdatedEvent(
            account_id="old_acct", cash_delta=Decimal("500"), occurred_at=t1
        )
        evt_after = AccountUpdatedEvent(
            account_id="new_acct", cash_delta=Decimal("999"), occurred_at=t2
        )
        log_path = events_dir / "2025-01" / "events-2025-01-01.log"
        _write_ndjson(log_path, [evt_before, evt_after])

        # Create snapshot at t_snap
        sm = SnapshotManager(base_dir=snap_dir)
        sm.save({
            "snapshot_ts": t_snap.isoformat(),
            "accounts": [{"account_id": "old_acct", "cash_available": "500"}],
        })

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))
        summary = ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=sm,
        )

        assert summary["snapshot_loaded"] is True
        # Only the event after snapshot should be replayed
        assert summary["events_replayed"] == 1

    def test_cold_start_rebuilds_order_status(self, tmp_path):
        """cold_start properly rebuilds order status from order lifecycle events."""
        events_dir = tmp_path / "events"

        oid = uuid.uuid4()
        t_base = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

        evts: list[BaseEvent] = [
            AccountUpdatedEvent(
                account_id="user1",
                cash_delta=Decimal("1000000"),
                occurred_at=t_base,
            ),
            OrderPlacedEvent(
                order_id=oid,
                account_id="user1",
                symbol=Symbol("2330"),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")),
                price=Money(Decimal("500")),
                occurred_at=t_base + timedelta(seconds=1),
            ),
            OrderAcceptedEvent(
                order_id=oid,
                occurred_at=t_base + timedelta(seconds=2),
            ),
            OrderCanceledEvent(
                order_id=oid,
                occurred_at=t_base + timedelta(seconds=3),
            ),
        ]
        log_path = events_dir / "2025-06" / "events-2025-06-01.log"
        _write_ndjson(log_path, evts)

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snapshots"),
        )

        order = ctx.order_repo.get(oid)
        assert order.status == OrderStatus.CANCELED


# ═══════════════════════════════════════════════════════════════
# G-2: Pre-flight health check
# ═══════════════════════════════════════════════════════════════


class TestPreFlightCheck:
    def test_all_checks_pass(self, tmp_path):
        """pre_flight_check returns all_passed=True on healthy system."""
        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "events"))
        result = ctx.pre_flight_check()

        assert result["event_log_dir"] is True
        assert result["event_writer"] is True
        assert result["matching_engine"] is True
        assert result["account_service"] is True
        assert result["all_passed"] is True

    def test_check_returns_dict(self, tmp_path):
        """pre_flight_check returns a dict with expected keys."""
        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "events"))
        result = ctx.pre_flight_check()

        assert isinstance(result, dict)
        expected_keys = {"event_log_dir", "event_writer", "matching_engine",
                         "account_service", "event_log_readable",
                         "internal_queues", "order_book_consistency",
                         "all_passed"}
        assert expected_keys == set(result.keys())


# ═══════════════════════════════════════════════════════════════
# G-5: Qty import in app.py (regression test)
# ═══════════════════════════════════════════════════════════════


class TestQtyImportFix:
    def test_rebuild_order_book_uses_qty_without_error(self, tmp_path):
        """rebuild_order_book_from_log() should not raise NameError for Qty."""
        events_dir = tmp_path / "events"

        oid_buy = uuid.uuid4()
        oid_sell = uuid.uuid4()
        t_base = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        evts: list[BaseEvent] = [
            OrderPlacedEvent(
                order_id=oid_buy,
                account_id="buyer",
                symbol=Symbol("2330"),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("100")),
                price=Money(Decimal("500")),
                occurred_at=t_base,
            ),
            OrderPlacedEvent(
                order_id=oid_sell,
                account_id="seller",
                symbol=Symbol("2330"),
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                qty=Qty(Decimal("200")),
                price=Money(Decimal("500")),
                occurred_at=t_base + timedelta(seconds=1),
            ),
            TradeExecutedEvent(
                trade_id=uuid.uuid4(),
                symbol=Symbol("2330"),
                price=Money(Decimal("500")),
                qty=Qty(Decimal("100")),
                buy_order_id=oid_buy,
                sell_order_id=oid_sell,
                occurred_at=t_base + timedelta(seconds=2),
            ),
        ]
        log_path = events_dir / "2025-01" / "events-2025-01-01.log"
        _write_ndjson(log_path, evts)

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "evt_out"))
        # This would have raised NameError before the Qty import fix
        count = ctx.rebuild_order_book_from_log(
            event_loader=EventLoader(base_dir=events_dir),
        )
        # sell order had 200 qty, 100 filled → 100 remaining → 1 resting order
        assert count == 1

    def test_qty_importable_from_app(self):
        """Qty is importable from app module (no NameError)."""
        from src.app import Qty as AppQty

        assert AppQty is Qty
