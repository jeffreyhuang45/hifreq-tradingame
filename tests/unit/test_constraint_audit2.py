# tests/unit/test_constraint_audit2.py
"""
DSD §1.6 Constraints Audit #2 – gap tests.

  G-1:  WSClient hashable (eq=False) → set storage works
  G-2:  Cancel → ME book removal (no phantom trades)
  G-3:  AccountDeletedEvent (event-sourced deletion)
  G-4:  _order_locks → public API (no cross-class private access)
  G-5:  EventWriter.base_dir public property
  G-6:  WebSocket channel mappings (OrderFilled, OrderRemainOnBook, etc.)
  G-7:  pre_flight_check does not pollute event log
  G-8:  Snapshot restore comprehensive
  G-9:  No market.* auto-subscription
"""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from src.account.account import Account, AccountService
from src.account.account_projection import AccountProjection
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.account_events import AccountDeletedEvent, AccountUpdatedEvent
from src.events.base_event import BaseEvent, EventType, event_from_dict
from src.events.order_events import (
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderPlacedEvent,
    OrderSide,
    OrderType,
)
from src.events.trade_events import TradeExecutedEvent
from src.matching_engine.matcher import Matcher
from src.storage.event_writer import EventWriter


# ═══════════════════════════════════════════════════════════════
# G-1: WSClient hashable → set storage works
# ═══════════════════════════════════════════════════════════════


class TestWSClientHashable:
    def test_wsclient_is_hashable(self):
        """WSClient must be hashable so it can be stored in a set."""
        from src.api.websocket import WSClient

        ws_mock = MagicMock()
        client = WSClient(ws=ws_mock, user_id="test")
        # Must not raise TypeError
        h = hash(client)
        assert isinstance(h, int)

    def test_wsclient_set_add_remove(self):
        """WSClient can be added and removed from a set."""
        from src.api.websocket import WSClient

        ws1 = MagicMock()
        ws2 = MagicMock()
        c1 = WSClient(ws=ws1, user_id="u1")
        c2 = WSClient(ws=ws2, user_id="u2")

        clients: set[WSClient] = set()
        clients.add(c1)
        clients.add(c2)
        assert len(clients) == 2

        clients.discard(c1)
        assert len(clients) == 1

    def test_wsclient_identity_based_equality(self):
        """Two WSClient with same data are NOT equal (identity-based)."""
        from src.api.websocket import WSClient

        ws = MagicMock()
        c1 = WSClient(ws=ws, user_id="same")
        c2 = WSClient(ws=ws, user_id="same")
        assert c1 is not c2
        assert c1 != c2  # eq=False means identity comparison


# ═══════════════════════════════════════════════════════════════
# G-2: Cancel → ME book removal
# ═══════════════════════════════════════════════════════════════


class TestCancelRemovesFromME:
    def test_cancel_removes_order_from_book(self, tmp_path):
        """After cancel, the order must be removed from ME book."""
        from src.app import AppContext

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "events"))
        user = ctx.account_service.get_or_create("seller")
        user.get_position(Symbol("2330")).qty_available = Qty(Decimal("1000"))

        # Place a SELL order
        sell_oid = OrderID(uuid.uuid4())
        sell_evt = OrderPlacedEvent(
            order_id=sell_oid, account_id="seller",
            symbol=Symbol("2330"), side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        )
        ctx.oms.place_order(sell_evt)
        ctx.process_oms_events()

        # Verify order is on the book
        snapshot_before = ctx.matcher.get_book_snapshot(Symbol("2330"))
        assert len(snapshot_before["asks"]) == 1

        # Cancel the order
        ctx.oms.cancel_order(sell_oid)
        ctx.process_oms_events()

        # Verify order is REMOVED from ME book
        snapshot_after = ctx.matcher.get_book_snapshot(Symbol("2330"))
        assert len(snapshot_after["asks"]) == 0, \
            "Canceled order still on ME book → phantom trades possible"

    def test_cancel_prevents_matching(self, tmp_path):
        """Canceled order must NOT match against new incoming orders."""
        from src.app import AppContext

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "events"))
        seller = ctx.account_service.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("1000"))
        buyer = ctx.account_service.get_or_create("buyer")
        buyer.deposit(Money(Decimal("1000000")))

        # Place and cancel a SELL order
        sell_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=sell_oid, account_id="seller",
            symbol=Symbol("2330"), side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()
        ctx.oms.cancel_order(sell_oid)
        ctx.process_oms_events()

        # Now place a BUY at the same price
        buy_oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=buy_oid, account_id="buyer",
            symbol=Symbol("2330"), side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")), price=Money(Decimal("500")),
        ))
        ctx.process_oms_events()

        # No trade should have happened
        from src.oms.order import OrderStatus
        buy_order = ctx.order_repo.get(buy_oid)
        assert buy_order.filled_qty == Decimal(0), \
            "Canceled sell order matched → phantom trade!"


# ═══════════════════════════════════════════════════════════════
# G-3: AccountDeletedEvent (event-sourced deletion)
# ═══════════════════════════════════════════════════════════════


class TestAccountDeletedEvent:
    def test_event_type(self):
        """AccountDeletedEvent has correct event_type."""
        evt = AccountDeletedEvent(account_id="victim")
        assert evt.event_type == EventType.ACCOUNT_DELETED

    def test_serialization_roundtrip(self):
        """AccountDeletedEvent serializes and deserializes."""
        evt = AccountDeletedEvent(account_id="victim")
        json_str = evt.to_json()
        parsed = json.loads(json_str)
        assert parsed["event_type"] == "AccountDeleted"
        assert parsed["account_id"] == "victim"

        restored = event_from_dict(parsed)
        assert isinstance(restored, AccountDeletedEvent)
        assert restored.account_id == "victim"

    def test_replay_removes_account(self):
        """Replaying AccountDeletedEvent removes the account."""
        events: list[BaseEvent] = [
            AccountUpdatedEvent(account_id="u1", cash_delta=Decimal("100000")),
            AccountUpdatedEvent(account_id="u2", cash_delta=Decimal("50000")),
            AccountDeletedEvent(account_id="u1"),
        ]

        proj = AccountProjection()
        svc = proj.replay(events)

        # u1 should be gone
        accounts = {a.account_id for a in svc.list_all()}
        assert "u1" not in accounts, "Deleted account reappeared on replay"
        assert "u2" in accounts

    def test_cold_start_with_deletion(self, tmp_path):
        """Cold-start from event log with deletion produces correct state."""
        from src.app import AppContext
        from src.storage.event_loader import EventLoader

        events_dir = tmp_path / "events"
        log_path = events_dir / "2025-01" / "events-2025-01-01.log"
        log_path.parent.mkdir(parents=True)

        evts = [
            AccountUpdatedEvent(
                account_id="alice", cash_delta=Decimal("100000"),
                occurred_at=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            ),
            AccountUpdatedEvent(
                account_id="bob", cash_delta=Decimal("50000"),
                occurred_at=datetime(2025, 1, 1, 10, 1, tzinfo=timezone.utc),
            ),
            AccountDeletedEvent(
                account_id="alice",
                occurred_at=datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            ),
        ]
        with open(log_path, "w", encoding="utf-8") as f:
            for e in evts:
                f.write(e.to_json() + "\n")

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "out"))
        from src.storage.snapshot import SnapshotManager
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=SnapshotManager(base_dir=tmp_path / "snaps"),
        )

        accounts = {a.account_id for a in ctx.account_service.list_all()}
        assert "alice" not in accounts
        assert "bob" in accounts


# ═══════════════════════════════════════════════════════════════
# G-4: _order_locks → public API (no cross-class private access)
# ═══════════════════════════════════════════════════════════════


class TestOrderLockPublicAPI:
    def test_has_order_lock(self):
        """Account.has_order_lock works correctly."""
        acct = Account(account_id="test")
        oid = OrderID(uuid.uuid4())
        assert not acct.has_order_lock(oid)
        acct.set_order_lock(oid, (OrderSide.BUY, Symbol("2330"), Money(Decimal("500")), Qty(Decimal("100"))))
        assert acct.has_order_lock(oid)

    def test_get_order_lock(self):
        """Account.get_order_lock returns the lock tuple."""
        acct = Account(account_id="test")
        oid = OrderID(uuid.uuid4())
        lock = (OrderSide.BUY, Symbol("2330"), Money(Decimal("50000")), Qty(Decimal("100")))
        acct.set_order_lock(oid, lock)
        assert acct.get_order_lock(oid) == lock
        assert acct.get_order_lock(OrderID(uuid.uuid4())) is None

    def test_pop_order_lock(self):
        """Account.pop_order_lock removes and returns."""
        acct = Account(account_id="test")
        oid = OrderID(uuid.uuid4())
        lock = (OrderSide.SELL, Symbol("2330"), Money(Decimal("0")), Qty(Decimal("100")))
        acct.set_order_lock(oid, lock)
        result = acct.pop_order_lock(oid)
        assert result == lock
        assert not acct.has_order_lock(oid)

    def test_order_lock_ids(self):
        """Account.order_lock_ids returns all locked order IDs."""
        acct = Account(account_id="test")
        oid1 = OrderID(uuid.uuid4())
        oid2 = OrderID(uuid.uuid4())
        acct.set_order_lock(oid1, (OrderSide.BUY, Symbol("X"), Money(0), Qty(0)))
        acct.set_order_lock(oid2, (OrderSide.SELL, Symbol("Y"), Money(0), Qty(0)))
        assert acct.order_lock_ids == {oid1, oid2}

    def test_no_private_access_in_src(self):
        """No src/ file outside account.py accesses _order_locks."""
        import re
        src_root = Path(__file__).parent.parent.parent / "src"
        violations = []
        for py in src_root.rglob("*.py"):
            if py.name == "account.py":
                continue  # the defining module is exempt
            content = py.read_text(encoding="utf-8", errors="ignore")
            # Find any access pattern like ._order_locks
            if re.search(r"\._order_locks", content):
                violations.append(str(py.relative_to(src_root)))
        assert violations == [], f"Files still access _order_locks directly: {violations}"


# ═══════════════════════════════════════════════════════════════
# G-5: EventWriter.base_dir public property
# ═══════════════════════════════════════════════════════════════


class TestEventWriterBaseDir:
    def test_base_dir_property_exists(self, tmp_path):
        """EventWriter exposes base_dir as a public property."""
        ew = EventWriter(base_dir=tmp_path / "events")
        assert ew.base_dir == tmp_path / "events"

    def test_no_private_base_dir_access_in_src(self):
        """No src/ file outside event_writer.py accesses _base_dir."""
        import re
        src_root = Path(__file__).parent.parent.parent / "src"
        violations = []
        for py in src_root.rglob("*.py"):
            if py.name == "event_writer.py":
                continue
            content = py.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"event_writer\._base_dir", content):
                violations.append(str(py.relative_to(src_root)))
        assert violations == [], f"Files still access _base_dir: {violations}"


# ═══════════════════════════════════════════════════════════════
# G-6: WebSocket channel mappings
# ═══════════════════════════════════════════════════════════════


class TestWSChannelMappings:
    def test_order_filled_has_channel(self):
        """OrderFilledEvent resolves to orders.{account_id}."""
        from src.api.websocket import _event_channels, set_order_repo

        # Mock order_repo
        mock_repo = MagicMock()
        mock_order = MagicMock()
        mock_order.account_id = "user1"
        mock_repo.get.return_value = mock_order
        set_order_repo(mock_repo)

        evt = OrderFilledEvent(
            order_id=uuid.uuid4(),
            cumulative_qty=Decimal("100"),
            status="FILLED",
        )
        channels = _event_channels(evt)
        assert any("orders.user1" in ch for ch in channels), \
            f"OrderFilledEvent has no channel mapping. Got: {channels}"

    def test_account_updated_has_channel(self):
        """AccountUpdatedEvent resolves to orders.{account_id}."""
        from src.api.websocket import _event_channels

        evt = AccountUpdatedEvent(account_id="user1", cash_delta=Decimal("100"))
        channels = _event_channels(evt)
        assert "orders.user1" in channels, \
            f"AccountUpdatedEvent has no channel. Got: {channels}"

    def test_trade_settlement_has_channels(self):
        """TradeSettlementEvent resolves to trades.{buyer} and trades.{seller}."""
        from src.api.websocket import _event_channels
        from src.events.account_events import TradeSettlementEvent

        evt = TradeSettlementEvent(
            buyer_account_id="buyer1",
            seller_account_id="seller1",
            buyer_order_id=uuid.uuid4(),
            seller_order_id=uuid.uuid4(),
            symbol=Symbol("2330"),
            price=Money(Decimal("500")),
            qty=Qty(Decimal("100")),
        )
        channels = _event_channels(evt)
        assert "trades.buyer1" in channels
        assert "trades.seller1" in channels


# ═══════════════════════════════════════════════════════════════
# G-7: pre_flight_check does not pollute event log
# ═══════════════════════════════════════════════════════════════


class TestPreFlightNoPollution:
    def test_no_health_check_event_in_log(self, tmp_path):
        """pre_flight_check must NOT write __health_check__ event to log."""
        from src.app import AppContext

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "events"))
        ctx.pre_flight_check()
        ctx.event_writer.flush()

        # Check all log files for __health_check__
        for log_file in (tmp_path / "events").rglob("*.log"):
            content = log_file.read_text(encoding="utf-8")
            assert "__health_check__" not in content, \
                "pre_flight_check polluted event log with phantom __health_check__ event"


# ═══════════════════════════════════════════════════════════════
# G-8: Snapshot restore comprehensive
# ═══════════════════════════════════════════════════════════════


class TestSnapshotRestoreComprehensive:
    def test_snapshot_restores_cash_locked(self, tmp_path):
        """Snapshot restore includes cash_locked."""
        from src.app import AppContext
        from src.storage.event_loader import EventLoader
        from src.storage.snapshot import SnapshotManager

        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True)
        sm = SnapshotManager(base_dir=tmp_path / "snapshots")
        sm.save({
            "snapshot_ts": "2025-01-01T12:00:00+00:00",
            "accounts": [{
                "account_id": "u1",
                "cash_available": "80000",
                "cash_locked": "20000",
            }],
        })

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "out"))
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=sm,
        )

        acct = ctx.account_service.get_or_create("u1")
        assert acct.cash_available == Money(Decimal("80000"))
        assert acct.cash_locked == Money(Decimal("20000"))

    def test_snapshot_restores_positions(self, tmp_path):
        """Snapshot restore includes position data."""
        from src.app import AppContext
        from src.storage.event_loader import EventLoader
        from src.storage.snapshot import SnapshotManager

        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True)
        sm = SnapshotManager(base_dir=tmp_path / "snapshots")
        sm.save({
            "snapshot_ts": "2025-01-01T12:00:00+00:00",
            "accounts": [{
                "account_id": "u1",
                "cash_available": "100000",
                "cash_locked": "0",
                "positions": [
                    {"symbol": "2330", "qty_available": "500",
                     "qty_locked": "100", "total_cost": "300000"},
                ],
            }],
        })

        ctx = AppContext(event_writer=EventWriter(base_dir=tmp_path / "out"))
        ctx.cold_start(
            event_loader=EventLoader(base_dir=events_dir),
            snapshot_manager=sm,
        )

        acct = ctx.account_service.get_or_create("u1")
        pos = acct.get_position(Symbol("2330"))
        assert pos.qty_available == Qty(Decimal("500"))
        assert pos.qty_locked == Qty(Decimal("100"))
        assert pos.total_cost == Money(Decimal("300000"))


# ═══════════════════════════════════════════════════════════════
# G-9: No market.* auto-subscription
# ═══════════════════════════════════════════════════════════════


class TestNoAutoMarketSubscription:
    def test_no_auto_market_wildcard_in_source(self):
        """WebSocket endpoint must NOT auto-subscribe to market.*."""
        import inspect
        from src.api import websocket

        source = inspect.getsource(websocket.websocket_endpoint)
        # Check that the auto-subscription line is gone
        assert 'subscriptions.add("market.*")' not in source, \
            "market.* auto-subscription violates DSD §1.6 #3 channel-based subscription"


# ═══════════════════════════════════════════════════════════════
# Cross-cutting: Encapsulation verification
# ═══════════════════════════════════════════════════════════════


class TestEncapsulationIntegrity:
    def test_lock_funds_uses_public_api(self):
        """lock_funds must use public API, not _order_locks directly."""
        import inspect
        source = inspect.getsource(AccountService.lock_funds)
        assert "_order_locks[" not in source, \
            "lock_funds still accesses _order_locks directly"

    def test_unlock_funds_uses_public_api(self):
        """unlock_funds must use public API."""
        import inspect
        source = inspect.getsource(AccountService.unlock_funds)
        assert "_order_locks" not in source, \
            "unlock_funds still accesses _order_locks directly"

    def test_reduce_order_lock_uses_public_api(self):
        """_reduce_order_lock must use public API."""
        import inspect
        source = inspect.getsource(AccountService._reduce_order_lock)
        assert "_order_locks[" not in source, \
            "_reduce_order_lock still accesses _order_locks directly"

    def test_account_projection_uses_public_api(self):
        """AccountProjection must not access _order_locks."""
        import inspect
        source = inspect.getsource(AccountProjection)
        assert "_order_locks" not in source, \
            "AccountProjection still accesses _order_locks directly"
