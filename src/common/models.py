# src/common/models.py
"""
Canonical Domain Models (DSD §2.5).

This module defines the **shared data models** used across all modules.
Every model here provides:
  • Typed fields with canonical types from ``src.common.types``
  • ``to_dict()`` for JSON-friendly serialization
  • ``from_dict()`` classmethod for deserialization

Models in this file are *value objects* — they carry data between
modules without implying ownership. The module that *owns* the
lifecycle (Account ↔ AccountService, Order ↔ OMS) is still the
authority, but every module speaks the same vocabulary.

Import hierarchy:
  common.types  ←  common.enums  ←  common.models  ←  (domain modules)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.common.enums import OrderSide, OrderStatus, OrderType
from src.common.types import AccountID, Money, OrderID, Qty, Symbol


# ═══════════════════════════════════════════════════════════════
# Position
# ═══════════════════════════════════════════════════════════════

@dataclass
class PositionModel:
    """Canonical snapshot of a single stock position.

    Used for API responses, snapshot persistence, and cross-module
    data transfer. The Account module's ``Position`` class remains
    the authoritative mutable runtime object.
    """
    symbol: Symbol
    qty_available: Qty = field(default_factory=lambda: Qty(Decimal(0)))
    qty_locked: Qty = field(default_factory=lambda: Qty(Decimal(0)))
    total_cost: Money = field(default_factory=lambda: Money(Decimal(0)))

    @property
    def total(self) -> Qty:
        return Qty(self.qty_available + self.qty_locked)

    @property
    def avg_cost(self) -> Money:
        if self.total <= 0:
            return Money(Decimal(0))
        return Money((self.total_cost / self.total).quantize(Decimal("0.01")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "qty_available": str(self.qty_available),
            "qty_locked": str(self.qty_locked),
            "total_cost": str(self.total_cost),
            "avg_cost": str(self.avg_cost),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PositionModel:
        return cls(
            symbol=Symbol(data["symbol"]),
            qty_available=Qty(Decimal(str(data.get("qty_available", 0)))),
            qty_locked=Qty(Decimal(str(data.get("qty_locked", 0)))),
            total_cost=Money(Decimal(str(data.get("total_cost", 0)))),
        )


# ═══════════════════════════════════════════════════════════════
# Trade Lot
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradeLotModel:
    """Canonical record of a single trade execution."""
    trade_id: str
    order_id: str
    symbol: str
    side: str           # "BUY" | "SELL"
    qty: Decimal
    price: Decimal
    trade_date: str     # ISO-8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": str(self.qty),
            "price": str(self.price),
            "trade_date": self.trade_date,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TradeLotModel:
        return cls(
            trade_id=data.get("trade_id", ""),
            order_id=data.get("order_id", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            qty=Decimal(str(data.get("qty", 0))),
            price=Decimal(str(data.get("price", 0))),
            trade_date=data.get("trade_date", ""),
        )


# ═══════════════════════════════════════════════════════════════
# Cash Transaction
# ═══════════════════════════════════════════════════════════════

@dataclass
class CashTransactionModel:
    """Canonical record of a cash flow event."""
    timestamp: str      # ISO-8601
    tx_type: str        # DEPOSIT, WITHDRAW, BUY_LOCK, BUY_SETTLE, SELL_SETTLE, UNLOCK
    amount: Decimal
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tx_type": self.tx_type,
            "amount": str(self.amount),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CashTransactionModel:
        return cls(
            timestamp=data.get("timestamp", ""),
            tx_type=data.get("tx_type", ""),
            amount=Decimal(str(data.get("amount", 0))),
            description=data.get("description", ""),
        )


# ═══════════════════════════════════════════════════════════════
# Account Snapshot
# ═══════════════════════════════════════════════════════════════

@dataclass
class AccountSnapshotModel:
    """Read-only snapshot of an Account's state.

    Used for API responses, persistence, and cross-module queries.
    """
    account_id: AccountID
    cash_available: Money = field(default_factory=lambda: Money(Decimal(0)))
    cash_locked: Money = field(default_factory=lambda: Money(Decimal(0)))
    positions: list[PositionModel] = field(default_factory=list)
    trade_history: list[TradeLotModel] = field(default_factory=list)
    cash_transactions: list[CashTransactionModel] = field(default_factory=list)

    @property
    def total_cash(self) -> Money:
        return Money(self.cash_available + self.cash_locked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": str(self.account_id),
            "cash_available": str(self.cash_available),
            "cash_locked": str(self.cash_locked),
            "positions": [p.to_dict() for p in self.positions],
            "trade_history": [t.to_dict() for t in self.trade_history],
            "cash_transactions": [c.to_dict() for c in self.cash_transactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountSnapshotModel:
        return cls(
            account_id=AccountID(data["account_id"]),
            cash_available=Money(Decimal(str(data.get("cash_available", 0)))),
            cash_locked=Money(Decimal(str(data.get("cash_locked", 0)))),
            positions=[
                PositionModel.from_dict(p) for p in data.get("positions", [])
            ],
            trade_history=[
                TradeLotModel.from_dict(t) for t in data.get("trade_history", [])
            ],
            cash_transactions=[
                CashTransactionModel.from_dict(c) for c in data.get("cash_transactions", [])
            ],
        )


# ═══════════════════════════════════════════════════════════════
# Order Snapshot
# ═══════════════════════════════════════════════════════════════

@dataclass
class OrderSnapshotModel:
    """Read-only snapshot of an Order's state.

    Serializable view of an OMS Order for API responses and reports.
    """
    order_id: str
    account_id: str
    symbol: str
    side: str
    order_type: str
    qty: Decimal
    price: Decimal | None
    status: str
    created_at: str  # ISO-8601
    filled_qty: Decimal = Decimal(0)

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "qty": str(self.qty),
            "price": str(self.price) if self.price is not None else None,
            "status": self.status,
            "created_at": self.created_at,
            "filled_qty": str(self.filled_qty),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderSnapshotModel:
        price_raw = data.get("price")
        return cls(
            order_id=data.get("order_id", ""),
            account_id=data.get("account_id", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("order_type", ""),
            qty=Decimal(str(data.get("qty", 0))),
            price=Decimal(str(price_raw)) if price_raw is not None else None,
            status=data.get("status", ""),
            created_at=data.get("created_at", ""),
            filled_qty=Decimal(str(data.get("filled_qty", 0))),
        )

    @classmethod
    def from_order(cls, order: Any) -> OrderSnapshotModel:
        """Build from an OMS ``Order`` object."""
        return cls(
            order_id=str(order.order_id),
            account_id=str(order.account_id),
            symbol=str(order.symbol),
            side=order.side.value if hasattr(order.side, "value") else str(order.side),
            order_type=order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type),
            qty=Decimal(str(order.qty)),
            price=Decimal(str(order.price)) if order.price is not None else None,
            status=order.status.value if hasattr(order.status, "value") else str(order.status),
            created_at=order.created_at.isoformat() if hasattr(order.created_at, "isoformat") else str(order.created_at),
            filled_qty=Decimal(str(order.filled_qty)),
        )


# ═══════════════════════════════════════════════════════════════
# Market Data Snapshot
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketDataSnapshotModel:
    """Canonical snapshot of market data for a single symbol.

    Unifies the fields from StockQuote (adapter layer) and
    MarketDataUpdatedEvent (event layer) into one shared vocabulary.
    """
    symbol: str
    name: str = ""
    bid_price: Decimal = Decimal(0)
    ask_price: Decimal = Decimal(0)
    last_trade_price: Decimal | None = None
    open_price: Decimal = Decimal(0)
    high_price: Decimal = Decimal(0)
    low_price: Decimal = Decimal(0)
    close_price: Decimal = Decimal(0)
    volume: int = 0
    change: Decimal = Decimal(0)
    change_pct: Decimal = Decimal(0)
    bid_qty: int = 0
    ask_qty: int = 0
    last_trade_qty: int | None = None
    trades_count: int = 0
    timestamp: str = ""  # ISO-8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "bid_price": str(self.bid_price),
            "ask_price": str(self.ask_price),
            "last_trade_price": str(self.last_trade_price) if self.last_trade_price is not None else None,
            "open_price": str(self.open_price),
            "high_price": str(self.high_price),
            "low_price": str(self.low_price),
            "close_price": str(self.close_price),
            "volume": str(self.volume),
            "change": str(self.change),
            "change_pct": str(self.change_pct),
            "bid_qty": str(self.bid_qty),
            "ask_qty": str(self.ask_qty),
            "last_trade_qty": str(self.last_trade_qty) if self.last_trade_qty is not None else None,
            "trades_count": str(self.trades_count),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketDataSnapshotModel:
        ltp = data.get("last_trade_price")
        ltq = data.get("last_trade_qty")
        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            bid_price=Decimal(str(data.get("bid_price", 0))),
            ask_price=Decimal(str(data.get("ask_price", 0))),
            last_trade_price=Decimal(str(ltp)) if ltp is not None else None,
            open_price=Decimal(str(data.get("open_price", 0))),
            high_price=Decimal(str(data.get("high_price", 0))),
            low_price=Decimal(str(data.get("low_price", 0))),
            close_price=Decimal(str(data.get("close_price", 0))),
            volume=int(data.get("volume", 0)),
            change=Decimal(str(data.get("change", 0))),
            change_pct=Decimal(str(data.get("change_pct", 0))),
            bid_qty=int(data.get("bid_qty", 0)),
            ask_qty=int(data.get("ask_qty", 0)),
            last_trade_qty=int(ltq) if ltq is not None else None,
            trades_count=int(data.get("trades_count", 0)),
            timestamp=data.get("timestamp", ""),
        )

    @classmethod
    def from_quote(cls, q: Any) -> MarketDataSnapshotModel:
        """Build from a StockQuote adapter object."""
        return cls(
            symbol=q.symbol,
            name=getattr(q, "name", ""),
            bid_price=Decimal(str(q.bid_price)),
            ask_price=Decimal(str(q.ask_price)),
            last_trade_price=Decimal(str(q.last_trade_price)) if q.last_trade_price else None,
            open_price=Decimal(str(q.open_price)),
            high_price=Decimal(str(q.high_price)),
            low_price=Decimal(str(q.low_price)),
            close_price=Decimal(str(q.close_price)),
            volume=int(q.volume),
            change=Decimal(str(q.change)),
            change_pct=Decimal(str(getattr(q, "change_pct", 0))),
            bid_qty=int(getattr(q, "bid_qty", 0)),
            ask_qty=int(getattr(q, "ask_qty", 0)),
            last_trade_qty=int(q.last_trade_qty) if getattr(q, "last_trade_qty", None) else None,
            trades_count=int(getattr(q, "trades_count", 0)),
            timestamp=q.timestamp.isoformat() if hasattr(q, "timestamp") and hasattr(q.timestamp, "isoformat") else "",
        )

    @classmethod
    def from_event(cls, evt: Any) -> MarketDataSnapshotModel:
        """Build from a MarketDataUpdatedEvent."""
        ltp = getattr(evt, "last_trade_price", None)
        ltq = getattr(evt, "last_trade_qty", None)
        return cls(
            symbol=str(getattr(evt, "symbol", "")),
            bid_price=Decimal(str(getattr(evt, "bid_price", 0))),
            ask_price=Decimal(str(getattr(evt, "ask_price", 0))),
            last_trade_price=Decimal(str(ltp)) if ltp is not None else None,
            volume=int(getattr(evt, "volume", 0)),
            bid_qty=int(getattr(evt, "bid_qty", 0)),
            ask_qty=int(getattr(evt, "ask_qty", 0)),
            last_trade_qty=int(ltq) if ltq is not None else None,
            trades_count=int(getattr(evt, "trades_count", 0)),
        )


# ═══════════════════════════════════════════════════════════════
# Order Book Level / Snapshot
# ═══════════════════════════════════════════════════════════════

@dataclass
class OrderBookLevelModel:
    """A single price level in the order book."""
    price: Decimal
    qty: Decimal
    order_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "qty": str(self.qty),
            "order_count": self.order_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderBookLevelModel:
        return cls(
            price=Decimal(str(data.get("price", 0))),
            qty=Decimal(str(data.get("qty", 0))),
            order_count=int(data.get("order_count", 0)),
        )


@dataclass
class OrderBookSnapshotModel:
    """Full snapshot of a symbol's order book."""
    symbol: str
    bids: list[OrderBookLevelModel] = field(default_factory=list)
    asks: list[OrderBookLevelModel] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bids": [b.to_dict() for b in self.bids],
            "asks": [a.to_dict() for a in self.asks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderBookSnapshotModel:
        return cls(
            symbol=data.get("symbol", ""),
            bids=[OrderBookLevelModel.from_dict(b) for b in data.get("bids", [])],
            asks=[OrderBookLevelModel.from_dict(a) for a in data.get("asks", [])],
        )


# ═══════════════════════════════════════════════════════════════
# Leaderboard Entry
# ═══════════════════════════════════════════════════════════════

@dataclass
class LeaderboardEntryModel:
    """One row in the trading competition leaderboard."""
    rank: int
    user_id: str
    username: str
    display_name: str
    total_assets: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    trade_count: int
    trade_volume: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "total_assets": str(self.total_assets),
            "pnl": str(self.pnl),
            "pnl_pct": str(self.pnl_pct),
            "trade_count": self.trade_count,
            "trade_volume": str(self.trade_volume),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LeaderboardEntryModel:
        return cls(
            rank=int(data.get("rank", 0)),
            user_id=data.get("user_id", ""),
            username=data.get("username", ""),
            display_name=data.get("display_name", ""),
            total_assets=Decimal(str(data.get("total_assets", 0))),
            pnl=Decimal(str(data.get("pnl", 0))),
            pnl_pct=Decimal(str(data.get("pnl_pct", 0))),
            trade_count=int(data.get("trade_count", 0)),
            trade_volume=Decimal(str(data.get("trade_volume", 0))),
        )
