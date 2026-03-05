# tests/unit/test_canonical_models.py
"""
DSD §2.5 — Canonical Domain Models tests.

Verifies:
  A. common/enums.py — single source of truth for domain enums
  B. common/models.py — canonical snapshot models with to_dict/from_dict
  C. Domain model serialization — Account, Position, Order, StockQuote, DailyOHLCV
  D. Enum import chain — backward compatibility through re-exports
  E. REST endpoint return types — Pydantic schemas used, not raw dicts
"""
from __future__ import annotations

import inspect
import uuid
from decimal import Decimal
from datetime import datetime, timezone

import pytest

from src.common.types import Money, Qty, Symbol

# ═══════════════════════════════════════════════════════════════
# A. Canonical Enums — single source of truth
# ═══════════════════════════════════════════════════════════════


class TestCanonicalEnums:
    """Enums live in common.enums and are the SAME object everywhere."""

    def test_order_side_values(self):
        from src.common.enums import OrderSide
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"

    def test_order_type_values(self):
        from src.common.enums import OrderType
        assert OrderType.LIMIT.value == "LIMIT"
        assert OrderType.MARKET.value == "MARKET"

    def test_order_status_values(self):
        from src.common.enums import OrderStatus
        assert set(s.value for s in OrderStatus) == {
            "PENDING", "ACCEPTED", "ROUTED",
            "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED",
        }

    def test_rejection_reason_values(self):
        from src.common.enums import RejectionReason
        assert RejectionReason.INSUFFICIENT_CASH.value == "INSUFFICIENT_CASH"

    def test_order_side_identity_across_modules(self):
        """OrderSide from common.enums IS the same object as from order_events."""
        from src.common.enums import OrderSide as EnumsSide
        from src.events.order_events import OrderSide as EventsSide
        from src.oms.order import OrderSide as OmsSide
        from src.account.account import OrderSide as AccountSide

        assert EnumsSide is EventsSide
        assert EnumsSide is OmsSide
        assert EnumsSide is AccountSide

    def test_order_type_identity_across_modules(self):
        from src.common.enums import OrderType as EnumsType
        from src.events.order_events import OrderType as EventsType
        from src.oms.order import OrderType as OmsType

        assert EnumsType is EventsType
        assert EnumsType is OmsType

    def test_order_status_identity_across_modules(self):
        from src.common.enums import OrderStatus as EnumsStatus
        from src.oms.order import OrderStatus as OmsStatus

        assert EnumsStatus is OmsStatus

    def test_rejection_reason_identity(self):
        from src.common.enums import RejectionReason as EnumsRR
        from src.events.order_events import RejectionReason as EventsRR

        assert EnumsRR is EventsRR

    def test_no_enum_definition_in_order_events(self):
        """order_events.py must NOT define its own enums (re-export only)."""
        source = inspect.getsource(
            __import__("src.events.order_events", fromlist=["__name__"])
        )
        # Should not contain 'class OrderSide' etc. — only re-exports
        assert "class OrderSide" not in source
        assert "class OrderType" not in source
        assert "class RejectionReason" not in source

    def test_no_enum_definition_in_oms_order(self):
        """oms/order.py must NOT define its own OrderStatus."""
        source = inspect.getsource(
            __import__("src.oms.order", fromlist=["__name__"])
        )
        assert "class OrderStatus" not in source


# ═══════════════════════════════════════════════════════════════
# B. Canonical Models — to_dict / from_dict roundtrips
# ═══════════════════════════════════════════════════════════════


class TestPositionModel:
    def test_roundtrip(self):
        from src.common.models import PositionModel
        from src.common.types import Qty, Money, Symbol

        p = PositionModel(
            symbol=Symbol("2330"),
            qty_available=Qty(Decimal("500")),
            qty_locked=Qty(Decimal("100")),
            total_cost=Money(Decimal("300000")),
        )
        d = p.to_dict()
        restored = PositionModel.from_dict(d)
        assert restored.symbol == "2330"
        assert restored.qty_available == Decimal("500")
        assert restored.qty_locked == Decimal("100")
        assert restored.total_cost == Decimal("300000")
        assert restored.avg_cost == p.avg_cost

    def test_avg_cost_calculation(self):
        from src.common.models import PositionModel

        p = PositionModel(
            symbol="2330",
            qty_available=Qty(Decimal("300")),
            qty_locked=Qty(Decimal("200")),
            total_cost=Money(Decimal("250000")),
        )
        assert p.avg_cost == Decimal("500.00")

    def test_empty_position_avg_cost_zero(self):
        from src.common.models import PositionModel

        p = PositionModel(symbol="2330")
        assert p.avg_cost == Decimal(0)


class TestTradeLotModel:
    def test_roundtrip(self):
        from src.common.models import TradeLotModel

        t = TradeLotModel(
            trade_id="t1", order_id="o1", symbol="2330",
            side="BUY", qty=Decimal("100"), price=Decimal("500"),
            trade_date="2026-03-05T10:00:00+00:00",
        )
        d = t.to_dict()
        restored = TradeLotModel.from_dict(d)
        assert restored.trade_id == "t1"
        assert restored.qty == Decimal("100")
        assert restored.price == Decimal("500")


class TestCashTransactionModel:
    def test_roundtrip(self):
        from src.common.models import CashTransactionModel

        c = CashTransactionModel(
            timestamp="2026-03-05T10:00:00+00:00",
            tx_type="DEPOSIT",
            amount=Decimal("100000"),
            description="Initial deposit",
        )
        d = c.to_dict()
        restored = CashTransactionModel.from_dict(d)
        assert restored.tx_type == "DEPOSIT"
        assert restored.amount == Decimal("100000")


class TestAccountSnapshotModel:
    def test_roundtrip(self):
        from src.common.models import (
            AccountSnapshotModel, PositionModel,
            TradeLotModel, CashTransactionModel,
        )

        snap = AccountSnapshotModel(
            account_id="u1",
            cash_available=Money(Decimal("80000")),
            cash_locked=Money(Decimal("20000")),
            positions=[
                PositionModel(symbol="2330", qty_available=Qty(Decimal("500")),
                              total_cost=Money(Decimal("250000"))),
            ],
            trade_history=[
                TradeLotModel(trade_id="t1", order_id="o1", symbol="2330",
                              side="BUY", qty=Decimal("500"), price=Decimal("500"),
                              trade_date="2026-03-05"),
            ],
            cash_transactions=[
                CashTransactionModel(timestamp="2026-03-05", tx_type="DEPOSIT",
                                     amount=Decimal("100000")),
            ],
        )
        d = snap.to_dict()
        restored = AccountSnapshotModel.from_dict(d)
        assert restored.account_id == "u1"
        assert restored.cash_available == Decimal("80000")
        assert restored.total_cash == Decimal("100000")
        assert len(restored.positions) == 1
        assert restored.positions[0].symbol == "2330"
        assert len(restored.trade_history) == 1
        assert len(restored.cash_transactions) == 1


class TestOrderSnapshotModel:
    def test_roundtrip(self):
        from src.common.models import OrderSnapshotModel

        o = OrderSnapshotModel(
            order_id=str(uuid.uuid4()),
            account_id="u1",
            symbol="2330",
            side="BUY",
            order_type="LIMIT",
            qty=Decimal("100"),
            price=Decimal("500"),
            status="ACCEPTED",
            created_at="2026-03-05T10:00:00+00:00",
            filled_qty=Decimal("50"),
        )
        d = o.to_dict()
        restored = OrderSnapshotModel.from_dict(d)
        assert restored.order_id == o.order_id
        assert restored.qty == Decimal("100")
        assert restored.price == Decimal("500")
        assert restored.remaining_qty == Decimal("50")

    def test_from_order(self):
        from src.common.models import OrderSnapshotModel
        from src.oms.order import Order, OrderStatus
        from src.common.enums import OrderSide, OrderType
        from src.common.types import OrderID, Qty, Money, Symbol, AccountID

        oid = OrderID(uuid.uuid4())
        order = Order(
            order_id=oid,
            account_id=AccountID("u1"),
            symbol=Symbol("2330"),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("100")),
            price=Money(Decimal("500")),
            status=OrderStatus.ACCEPTED,
        )
        snap = OrderSnapshotModel.from_order(order)
        assert snap.order_id == str(oid)
        assert snap.side == "BUY"
        assert snap.status == "ACCEPTED"


class TestMarketDataSnapshotModel:
    def test_roundtrip(self):
        from src.common.models import MarketDataSnapshotModel

        m = MarketDataSnapshotModel(
            symbol="2330", name="TSMC",
            bid_price=Decimal("499"), ask_price=Decimal("501"),
            last_trade_price=Decimal("500"),
            open_price=Decimal("495"), high_price=Decimal("505"),
            low_price=Decimal("493"), close_price=Decimal("500"),
            volume=12345, change=Decimal("5"), change_pct=Decimal("1.01"),
        )
        d = m.to_dict()
        restored = MarketDataSnapshotModel.from_dict(d)
        assert restored.symbol == "2330"
        assert restored.last_trade_price == Decimal("500")
        assert restored.volume == 12345

    def test_from_quote(self):
        from src.common.models import MarketDataSnapshotModel
        from src.market_data.twse_adapter import StockQuote

        sq = StockQuote(
            symbol="2330", name="TSMC",
            open_price=Decimal("495"), high_price=Decimal("505"),
            low_price=Decimal("493"), close_price=Decimal("500"),
            volume=12345,
            bid_price=Decimal("499"), ask_price=Decimal("501"),
            last_trade_price=Decimal("500"), change=Decimal("5"),
        )
        snap = MarketDataSnapshotModel.from_quote(sq)
        assert snap.symbol == "2330"
        assert snap.name == "TSMC"
        assert snap.last_trade_price == Decimal("500")

    def test_from_event(self):
        from src.common.models import MarketDataSnapshotModel
        from src.events.market_events import MarketDataUpdatedEvent

        evt = MarketDataUpdatedEvent(
            symbol="2330",
            bid_price=Decimal("499"),
            ask_price=Decimal("501"),
            last_trade_price=Decimal("500"),
            volume=Qty(Decimal("12345")),
        )
        snap = MarketDataSnapshotModel.from_event(evt)
        assert snap.symbol == "2330"
        assert snap.bid_price == Decimal("499")


class TestOrderBookModels:
    def test_level_roundtrip(self):
        from src.common.models import OrderBookLevelModel

        lvl = OrderBookLevelModel(
            price=Decimal("500"), qty=Decimal("1000"), order_count=5,
        )
        d = lvl.to_dict()
        restored = OrderBookLevelModel.from_dict(d)
        assert restored.price == Decimal("500")
        assert restored.order_count == 5

    def test_snapshot_roundtrip(self):
        from src.common.models import OrderBookSnapshotModel, OrderBookLevelModel

        snap = OrderBookSnapshotModel(
            symbol="2330",
            bids=[OrderBookLevelModel(Decimal("499"), Decimal("500"), 3)],
            asks=[OrderBookLevelModel(Decimal("501"), Decimal("300"), 2)],
        )
        d = snap.to_dict()
        restored = OrderBookSnapshotModel.from_dict(d)
        assert restored.symbol == "2330"
        assert len(restored.bids) == 1
        assert len(restored.asks) == 1


class TestLeaderboardEntryModel:
    def test_roundtrip(self):
        from src.common.models import LeaderboardEntryModel

        e = LeaderboardEntryModel(
            rank=1, user_id="u1", username="alice",
            display_name="Alice", total_assets=Decimal("1100000"),
            pnl=Decimal("100000"), pnl_pct=Decimal("10.00"),
            trade_count=42, trade_volume=Decimal("5000000"),
        )
        d = e.to_dict()
        restored = LeaderboardEntryModel.from_dict(d)
        assert restored.rank == 1
        assert restored.pnl == Decimal("100000")


# ═══════════════════════════════════════════════════════════════
# C. Domain Model Serialization — to_dict / from_dict
# ═══════════════════════════════════════════════════════════════


class TestAccountSerialization:
    def test_account_to_dict(self):
        from src.account.account import Account, Position, TradeLot, CashTransaction
        from src.common.types import AccountID, Money, Qty, Symbol

        acct = Account(account_id=AccountID("u1"))
        acct.cash_available = Money(Decimal("80000"))
        acct.cash_locked = Money(Decimal("20000"))
        pos = acct.get_position(Symbol("2330"))
        pos.qty_available = Qty(Decimal("500"))
        pos.total_cost = Money(Decimal("250000"))
        acct.trade_history.append(TradeLot(
            trade_id="t1", order_id="o1", symbol="2330",
            side="BUY", qty=Decimal("500"), price=Decimal("500"),
            trade_date="2026-03-05",
        ))

        d = acct.to_dict()
        assert d["account_id"] == "u1"
        assert d["cash_available"] == "80000"
        assert d["cash_locked"] == "20000"
        assert len(d["positions"]) == 1
        assert d["positions"][0]["symbol"] == "2330"
        assert d["positions"][0]["qty_available"] == "500"
        assert len(d["trade_history"]) == 1

    def test_account_to_snapshot(self):
        from src.account.account import Account
        from src.common.types import AccountID, Money, Qty, Symbol

        acct = Account(account_id=AccountID("u1"))
        acct.cash_available = Money(Decimal("80000"))
        pos = acct.get_position(Symbol("2330"))
        pos.qty_available = Qty(Decimal("500"))
        pos.total_cost = Money(Decimal("250000"))

        snap = acct.to_snapshot()
        assert snap["account_id"] == "u1"
        assert "trade_history" not in snap  # snapshot excludes history
        assert len(snap["positions"]) == 1

    def test_position_to_dict(self):
        from src.account.account import Position
        from src.common.types import Qty, Money

        pos = Position(
            qty_available=Qty(Decimal("500")),
            qty_locked=Qty(Decimal("100")),
            total_cost=Money(Decimal("300000")),
        )
        d = pos.to_dict(symbol="2330")
        assert d["symbol"] == "2330"
        assert d["qty_available"] == "500"
        assert d["avg_cost"] == "500.00"

    def test_position_from_dict(self):
        from src.account.account import Position

        d = {"qty_available": "500", "qty_locked": "100", "total_cost": "300000"}
        pos = Position.from_dict(d)
        assert pos.qty_available == Decimal("500")
        assert pos.total == Decimal("600")

    def test_trade_lot_roundtrip(self):
        from src.account.account import TradeLot

        t = TradeLot("t1", "o1", "2330", "BUY", Decimal("100"), Decimal("500"), "2026-03-05")
        d = t.to_dict()
        restored = TradeLot.from_dict(d)
        assert restored.trade_id == "t1"
        assert restored.qty == Decimal("100")

    def test_cash_transaction_roundtrip(self):
        from src.account.account import CashTransaction

        c = CashTransaction("2026-03-05T10:00:00", "DEPOSIT", Decimal("100000"), "初始存款")
        d = c.to_dict()
        restored = CashTransaction.from_dict(d)
        assert restored.tx_type == "DEPOSIT"
        assert restored.amount == Decimal("100000")
        assert restored.description == "初始存款"


class TestOrderSerialization:
    def test_order_to_dict(self):
        from src.oms.order import Order
        from src.common.enums import OrderSide, OrderType, OrderStatus
        from src.common.types import OrderID, AccountID, Symbol, Qty, Money

        oid = OrderID(uuid.uuid4())
        order = Order(
            order_id=oid, account_id=AccountID("u1"),
            symbol=Symbol("2330"), side=OrderSide.BUY,
            order_type=OrderType.LIMIT, qty=Qty(Decimal("100")),
            price=Money(Decimal("500")), status=OrderStatus.ACCEPTED,
        )
        d = order.to_dict()
        assert d["order_id"] == str(oid)
        assert d["side"] == "BUY"
        assert d["status"] == "ACCEPTED"
        assert d["qty"] == "100"

    def test_order_from_dict(self):
        from src.oms.order import Order
        from src.common.enums import OrderSide, OrderType, OrderStatus

        oid = str(uuid.uuid4())
        d = {
            "order_id": oid, "account_id": "u1",
            "symbol": "2330", "side": "BUY",
            "order_type": "LIMIT", "qty": "100",
            "price": "500", "status": "ACCEPTED",
            "filled_qty": "50",
        }
        order = Order.from_dict(d)
        assert str(order.order_id) == oid
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.ACCEPTED
        assert order.filled_qty == Decimal("50")

    def test_order_roundtrip(self):
        from src.oms.order import Order
        from src.common.enums import OrderSide, OrderType
        from src.common.types import OrderID, AccountID, Symbol, Qty, Money

        oid = OrderID(uuid.uuid4())
        original = Order(
            order_id=oid, account_id=AccountID("u1"),
            symbol=Symbol("2330"), side=OrderSide.SELL,
            order_type=OrderType.MARKET, qty=Qty(Decimal("200")),
            price=None,
        )
        d = original.to_dict()
        restored = Order.from_dict(d)
        assert restored.order_id == original.order_id
        assert restored.side == OrderSide.SELL
        assert restored.price is None


class TestStockQuoteSerialization:
    def test_stock_quote_roundtrip(self):
        from src.market_data.twse_adapter import StockQuote

        sq = StockQuote(
            symbol="2330", name="TSMC",
            open_price=Decimal("495"), high_price=Decimal("505"),
            low_price=Decimal("493"), close_price=Decimal("500"),
            volume=12345,
            bid_price=Decimal("499"), ask_price=Decimal("501"),
            last_trade_price=Decimal("500"), change=Decimal("5"),
            bid_qty=1000, ask_qty=800,
        )
        d = sq.to_dict()
        restored = StockQuote.from_dict(d)
        assert restored.symbol == "2330"
        assert restored.name == "TSMC"
        assert restored.last_trade_price == Decimal("500")
        assert restored.bid_qty == 1000

    def test_daily_ohlcv_roundtrip(self):
        from src.market_data.twse_adapter import DailyOHLCV

        bar = DailyOHLCV(
            date="2026-03-05",
            open_price=Decimal("495"), high_price=Decimal("505"),
            low_price=Decimal("493"), close_price=Decimal("500"),
            volume=12345, trade_value=6000000, trades_count=1234,
            change=Decimal("5"),
        )
        d = bar.to_dict()
        restored = DailyOHLCV.from_dict(d)
        assert restored.date == "2026-03-05"
        assert restored.close_price == Decimal("500")
        assert restored.volume == 12345


# ═══════════════════════════════════════════════════════════════
# D. Enum Re-export Chain — backward compatibility
# ═══════════════════════════════════════════════════════════════


class TestEnumReexportChain:
    """Every import path that existed before the refactoring still works."""

    def test_order_events_reexports(self):
        from src.events.order_events import OrderSide, OrderType, RejectionReason
        assert OrderSide.BUY.value == "BUY"
        assert OrderType.LIMIT.value == "LIMIT"
        assert RejectionReason.INSUFFICIENT_CASH.value == "INSUFFICIENT_CASH"

    def test_oms_order_reexports(self):
        from src.oms.order import OrderStatus, OrderSide, OrderType
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderSide.BUY.value == "BUY"
        assert OrderType.LIMIT.value == "LIMIT"

    def test_account_imports_from_enums(self):
        """Account module imports OrderSide from common.enums, not events."""
        source = inspect.getsource(
            __import__("src.account.account", fromlist=["__name__"])
        )
        assert "from src.common.enums import OrderSide" in source

    def test_oms_order_imports_from_enums(self):
        """OMS order module imports from common.enums."""
        source = inspect.getsource(
            __import__("src.oms.order", fromlist=["__name__"])
        )
        assert "from src.common.enums import" in source


# ═══════════════════════════════════════════════════════════════
# E. REST endpoints use Pydantic schemas (not raw dicts)
# ═══════════════════════════════════════════════════════════════


class TestRESTEndpointReturnTypes:
    """Verify REST endpoints declare typed return annotations."""

    def test_list_orders_return_type(self):
        from src.api.rest import list_orders
        hints = list_orders.__annotations__
        assert "return" in hints
        ret = hints["return"]
        # Should be list[OrderResponse], not list[dict]
        assert "dict" not in str(ret), f"list_orders still returns dict: {ret}"

    def test_list_trades_return_type(self):
        from src.api.rest import list_trades
        hints = list_trades.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"list_trades still returns dict"

    def test_cash_flow_return_type(self):
        from src.api.rest import cash_flow
        hints = cash_flow.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"cash_flow still returns dict"

    def test_trade_statistics_return_type(self):
        from src.api.rest import trade_statistics
        hints = trade_statistics.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"trade_statistics still returns dict"

    def test_behavior_analysis_return_type(self):
        from src.api.rest import behavior_analysis
        hints = behavior_analysis.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"behavior_analysis still returns dict"

    def test_list_market_data_return_type(self):
        from src.api.rest import list_market_data
        hints = list_market_data.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"list_market_data still returns dict"

    def test_get_market_data_return_type(self):
        from src.api.rest import get_market_data
        hints = get_market_data.__annotations__
        assert "dict" not in str(hints.get("return", "")), \
            f"get_market_data still returns dict"


# ═══════════════════════════════════════════════════════════════
# F. No duplicate enum definitions in src/ outside common/enums.py
# ═══════════════════════════════════════════════════════════════


class TestNoDuplicateEnums:
    """After the refactoring, only common/enums.py defines the enum classes."""

    def test_no_order_side_class_outside_enums(self):
        import re
        from pathlib import Path
        src = Path(__file__).parent.parent.parent / "src"
        for py in src.rglob("*.py"):
            if py.name == "enums.py":
                continue
            content = py.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"class OrderSide\(", content):
                pytest.fail(f"Duplicate OrderSide class in {py.relative_to(src)}")

    def test_no_order_status_class_outside_enums(self):
        import re
        from pathlib import Path
        src = Path(__file__).parent.parent.parent / "src"
        for py in src.rglob("*.py"):
            if py.name == "enums.py":
                continue
            content = py.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"class OrderStatus\(", content):
                pytest.fail(f"Duplicate OrderStatus class in {py.relative_to(src)}")
