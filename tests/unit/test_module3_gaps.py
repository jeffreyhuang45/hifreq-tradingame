# tests/unit/test_module3_gaps.py
"""
Tests for Module 3 gap fixes:
  C-1: Average cost price per position
  C-2: Per-position P&L
  C-3: Portfolio total market value / total assets
  C-4: Client operation logs
  C-5: Trading behavior analysis
  M-1: Password reset to default
  M-2: Account status field
  M-3: Trade statistics
  M-4: System metrics (OMS counters)
  M-5: Structured system logs
  M-6: Investment prediction
  L-1/L-2/L-3: Schema completeness
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from src.account.account import Account, AccountService, Position
from src.account.trading_analysis import (
    compute_behavior_analysis,
    compute_predictions,
    compute_trade_statistics,
)
from src.common.operation_log import (
    OperationLogBuffer,
    OperationLogEntry,
    SystemLogBuffer,
    SystemLogEntry,
)
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.order_events import OrderSide


# ═══════════════════════════════════════════════════════════════
# C-1: Average Cost Price
# ═══════════════════════════════════════════════════════════════

class TestAverageCost:
    def test_position_avg_cost_single_buy(self):
        """Buy 100 @ 500 → avg_cost = 500."""
        svc = AccountService()
        buyer = svc.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))
        seller = svc.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("200"))

        # Lock and settle
        svc.lock_funds("buyer", OrderID(uuid.uuid4()), Symbol("2330"),
                       OrderSide.BUY, Qty(Decimal("100")), Money(Decimal("500")))
        sell_oid = OrderID(uuid.uuid4())
        svc.lock_funds("seller", sell_oid, Symbol("2330"),
                       OrderSide.SELL, Qty(Decimal("100")), None)
        buy_oid = list(buyer.order_lock_ids)[0]
        svc.settle_trade("buyer", "seller", buy_oid, sell_oid,
                         Symbol("2330"), Money(Decimal("500")), Qty(Decimal("100")))

        pos = buyer.get_position(Symbol("2330"))
        assert pos.avg_cost == Money(Decimal("500.00"))

    def test_position_avg_cost_two_buys(self):
        """Buy 100@500 + 100@600 → avg_cost = 550."""
        svc = AccountService()
        buyer = svc.get_or_create("buyer")
        buyer.deposit(Money(Decimal("200000")))
        seller = svc.get_or_create("seller")
        seller.get_position(Symbol("2330")).qty_available = Qty(Decimal("300"))

        # Trade 1: 100 @ 500
        buy_oid1 = OrderID(uuid.uuid4())
        sell_oid1 = OrderID(uuid.uuid4())
        svc.lock_funds("buyer", buy_oid1, Symbol("2330"),
                       OrderSide.BUY, Qty(Decimal("100")), Money(Decimal("500")))
        svc.lock_funds("seller", sell_oid1, Symbol("2330"),
                       OrderSide.SELL, Qty(Decimal("100")), None)
        svc.settle_trade("buyer", "seller", buy_oid1, sell_oid1,
                         Symbol("2330"), Money(Decimal("500")), Qty(Decimal("100")))

        # Trade 2: 100 @ 600
        buy_oid2 = OrderID(uuid.uuid4())
        sell_oid2 = OrderID(uuid.uuid4())
        svc.lock_funds("buyer", buy_oid2, Symbol("2330"),
                       OrderSide.BUY, Qty(Decimal("100")), Money(Decimal("600")))
        svc.lock_funds("seller", sell_oid2, Symbol("2330"),
                       OrderSide.SELL, Qty(Decimal("100")), None)
        svc.settle_trade("buyer", "seller", buy_oid2, sell_oid2,
                         Symbol("2330"), Money(Decimal("600")), Qty(Decimal("100")))

        pos = buyer.get_position(Symbol("2330"))
        # Total cost = 50000 + 60000 = 110000, total qty = 200
        assert pos.total_cost == Money(Decimal("110000"))
        assert pos.avg_cost == Money(Decimal("550.00"))

    def test_empty_position_avg_cost_zero(self):
        """Empty position has avg_cost = 0."""
        pos = Position()
        assert pos.avg_cost == Money(Decimal(0))


# ═══════════════════════════════════════════════════════════════
# C-4 / M-5: Operation + System Logs
# ═══════════════════════════════════════════════════════════════

class TestOperationLog:
    def test_append_and_query(self):
        buf = OperationLogBuffer(max_size=10)
        buf.append(OperationLogEntry(
            timestamp="2026-01-01T00:00:00Z", user_id="u1",
            operation="POST", path="/api/v1/orders", status_code=202,
        ))
        buf.append(OperationLogEntry(
            timestamp="2026-01-01T00:00:01Z", user_id="u2",
            operation="GET", path="/api/v1/portfolio", status_code=200,
        ))
        all_entries = buf.query()
        assert len(all_entries) == 2

        u1_entries = buf.query(user_id="u1")
        assert len(u1_entries) == 1
        assert u1_entries[0]["path"] == "/api/v1/orders"

    def test_ring_buffer_overflow(self):
        buf = OperationLogBuffer(max_size=3)
        for i in range(5):
            buf.append(OperationLogEntry(
                timestamp=f"2026-01-01T00:00:0{i}Z", user_id="u1",
                operation="GET", path=f"/path/{i}", status_code=200,
            ))
        entries = buf.query()
        assert len(entries) == 3
        assert entries[0]["path"] == "/path/2"


class TestSystemLog:
    def test_query_by_level(self):
        buf = SystemLogBuffer(max_size=10)
        buf.append(SystemLogEntry(
            timestamp="2026-01-01T00:00:00Z", module="oms",
            level="ERROR", message="Something failed",
        ))
        buf.append(SystemLogEntry(
            timestamp="2026-01-01T00:00:01Z", module="auth",
            level="INFO", message="User logged in",
        ))
        errors = buf.query(level="ERROR")
        assert len(errors) == 1
        assert errors[0]["module"] == "oms"

    def test_query_by_module(self):
        buf = SystemLogBuffer(max_size=10)
        buf.append(SystemLogEntry(
            timestamp="2026-01-01T00:00:00Z", module="src.oms.oms_service",
            level="INFO", message="Order placed",
        ))
        buf.append(SystemLogEntry(
            timestamp="2026-01-01T00:00:01Z", module="src.auth.auth_service",
            level="INFO", message="Login",
        ))
        oms_logs = buf.query(module="oms")
        assert len(oms_logs) == 1


# ═══════════════════════════════════════════════════════════════
# M-1: Password Reset to Default
# ═══════════════════════════════════════════════════════════════

class TestPasswordResetDefault:
    def test_reset_to_default(self):
        from src.auth.user_model import UserRepository
        from src.auth.auth_service import AuthService, DEFAULT_PASSWORD
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = UserRepository(data_dir=tmpdir)
            svc = AuthService(repo)
            user = svc.register("testuser", "original_pw")
            # Reset with no password → should use DEFAULT_PASSWORD
            svc.admin_reset_password(user.user_id, None)
            # Now login with default password should work
            token = svc.login("testuser", DEFAULT_PASSWORD)
            assert token is not None

    def test_reset_with_specific_password(self):
        from src.auth.user_model import UserRepository
        from src.auth.auth_service import AuthService
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = UserRepository(data_dir=tmpdir)
            svc = AuthService(repo)
            user = svc.register("testuser2", "original_pw")
            svc.admin_reset_password(user.user_id, "new_specific_pw")
            token = svc.login("testuser2", "new_specific_pw")
            assert token is not None


# ═══════════════════════════════════════════════════════════════
# M-2: Account Status
# ═══════════════════════════════════════════════════════════════

class TestAccountStatus:
    def test_user_has_default_active_status(self):
        from src.auth.user_model import User
        u = User(user_id="u1", username="test", password_hash="x", salt="y")
        assert u.status == "active"

    def test_disabled_user_cannot_authenticate(self):
        from src.auth.user_model import UserRepository
        from src.auth.auth_service import AuthService
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = UserRepository(data_dir=tmpdir)
            svc = AuthService(repo)
            user = svc.register("blocked", "pw123")
            token = svc.login("blocked", "pw123")
            # Should work while active
            validated = svc.validate_token(token)
            assert validated.user_id == user.user_id
            # Disable the user
            user.status = "disabled"
            repo.update(user)
            with pytest.raises(ValueError, match="disabled"):
                svc.validate_token(token)


# ═══════════════════════════════════════════════════════════════
# M-3: Trade Statistics
# ═══════════════════════════════════════════════════════════════

class TestTradeStatistics:
    def test_empty_account(self):
        acct = Account(account_id="empty")
        stats = compute_trade_statistics(acct, {})
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == "0"

    def test_with_trades(self):
        from src.account.account import TradeLot
        acct = Account(account_id="trader")
        acct.trade_history = [
            TradeLot("t1", "o1", "2330", "BUY", Decimal("100"), Decimal("500"), "2026-01-01"),
            TradeLot("t2", "o2", "2330", "SELL", Decimal("100"), Decimal("550"), "2026-01-02"),
            TradeLot("t3", "o3", "2317", "BUY", Decimal("50"), Decimal("100"), "2026-01-03"),
        ]
        stats = compute_trade_statistics(acct, {})
        assert stats["total_trades"] == 3
        assert stats["total_buy_trades"] == 2
        assert stats["total_sell_trades"] == 1
        assert len(stats["most_traded_symbols"]) == 2
        # FIFO: bought 100@500, sold 100@550 → pnl = 5000 (win)
        assert Decimal(stats["realized_pnl"]) == Decimal("5000.00")
        assert Decimal(stats["win_rate"]) == Decimal("100.00")


# ═══════════════════════════════════════════════════════════════
# C-5: Behavior Analysis
# ═══════════════════════════════════════════════════════════════

class TestBehaviorAnalysis:
    def test_empty_account(self):
        acct = Account(account_id="empty")
        result = compute_behavior_analysis(acct)
        assert result["buy_sell_ratio"] == "0"

    def test_with_trades(self):
        from src.account.account import TradeLot
        acct = Account(account_id="trader")
        acct.trade_history = [
            TradeLot("t1", "o1", "2330", "BUY", Decimal("100"), Decimal("500"), "2026-01-01"),
            TradeLot("t2", "o2", "2330", "SELL", Decimal("50"), Decimal("550"), "2026-01-02"),
        ]
        result = compute_behavior_analysis(acct)
        # 1 buy / 1 sell = 1.0 ratio
        assert result["buy_sell_ratio"] == "1.00"
        assert len(result["most_traded_symbols"]) >= 1
        assert len(result["position_concentration"]) >= 1


# ═══════════════════════════════════════════════════════════════
# M-4: OMS Counters
# ═══════════════════════════════════════════════════════════════

class TestOMSCounters:
    def test_counters_increment(self):
        from src.app import AppContext
        ctx = AppContext()
        buyer = ctx.account_service.get_or_create("buyer")
        buyer.deposit(Money(Decimal("100000")))

        # Place a valid order → accepted
        from src.events.order_events import OrderPlacedEvent, OrderSide, OrderType
        oid = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=oid, account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10")), price=Money(Decimal("500")),
        ))
        assert ctx.oms.accepted_count == 1
        assert ctx.oms.rejected_count == 0

        # Place an order that will be rejected (insufficient cash)
        oid2 = OrderID(uuid.uuid4())
        ctx.oms.place_order(OrderPlacedEvent(
            order_id=oid2, account_id="buyer", symbol="2330",
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            qty=Qty(Decimal("10000")), price=Money(Decimal("500")),
        ))
        assert ctx.oms.accepted_count == 1
        assert ctx.oms.rejected_count == 1


# ═══════════════════════════════════════════════════════════════
# M-6: Investment Predictions
# ═══════════════════════════════════════════════════════════════

class TestPredictions:
    def test_empty_history(self):
        result = compute_predictions({}, {})
        assert result == []

    def test_with_history(self):
        history = {
            "2330": [
                {"time": "t1", "open": "500", "high": "510", "low": "495", "close": "505", "volume": 1000},
                {"time": "t2", "open": "505", "high": "515", "low": "500", "close": "510", "volume": 1200},
                {"time": "t3", "open": "510", "high": "520", "low": "505", "close": "515", "volume": 1100},
                {"time": "t4", "open": "515", "high": "525", "low": "510", "close": "520", "volume": 1300},
            ],
        }
        result = compute_predictions({}, history)
        assert len(result) == 1
        assert result[0]["symbol"] == "2330"
        assert "momentum_score" in result[0]
        assert "recommendation" in result[0]
