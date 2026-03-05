# src/account/account.py
"""
Account domain model.

Design rules (DSD §4.3 / §2.6):
  • Account state is rebuilt ENTIRELY from events (Event Replay).
  • No direct mutation — only via apply_event / lock / unlock helpers.
  • This module satisfies the FundChecker Protocol used by OMS
    (without OMS importing this module at type-check time).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List

from src.common.enums import OrderSide
from src.common.errors import InsufficientCashError, InsufficientPositionError
from src.common.types import AccountID, Money, OrderID, Qty, Symbol


@dataclass
class TradeLot:
    """A single trade execution record for history tracking."""
    trade_id: str
    order_id: str
    symbol: str
    side: str           # "BUY" or "SELL"
    qty: Decimal
    price: Decimal
    trade_date: str     # ISO-8601

    def to_dict(self) -> dict:
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
    def from_dict(cls, data: dict) -> TradeLot:
        return cls(
            trade_id=data.get("trade_id", ""),
            order_id=data.get("order_id", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            qty=Decimal(str(data.get("qty", 0))),
            price=Decimal(str(data.get("price", 0))),
            trade_date=data.get("trade_date", ""),
        )


@dataclass
class CashTransaction:
    """Cash flow record for the account."""
    timestamp: str      # ISO-8601
    tx_type: str        # DEPOSIT, WITHDRAW, BUY_LOCK, BUY_SETTLE, SELL_SETTLE, UNLOCK
    amount: Decimal
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "tx_type": self.tx_type,
            "amount": str(self.amount),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CashTransaction:
        return cls(
            timestamp=data.get("timestamp", ""),
            tx_type=data.get("tx_type", ""),
            amount=Decimal(str(data.get("amount", 0))),
            description=data.get("description", ""),
        )


@dataclass
class Position:
    """Stock position for one symbol."""
    qty_available: Qty = field(default_factory=lambda: Qty(Decimal(0)))
    qty_locked: Qty = field(default_factory=lambda: Qty(Decimal(0)))
    total_cost: Money = field(default_factory=lambda: Money(Decimal(0)))  # accumulated cost basis

    @property
    def total(self) -> Qty:
        return Qty(self.qty_available + self.qty_locked)

    @property
    def avg_cost(self) -> Money:
        """Weighted average cost per share."""
        if self.total <= 0:
            return Money(Decimal(0))
        return Money((self.total_cost / self.total).quantize(Decimal("0.01")))

    def to_dict(self, *, symbol: str = "") -> dict:
        return {
            "symbol": symbol,
            "qty_available": str(self.qty_available),
            "qty_locked": str(self.qty_locked),
            "total_cost": str(self.total_cost),
            "avg_cost": str(self.avg_cost),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Position:
        return cls(
            qty_available=Qty(Decimal(str(data.get("qty_available", 0)))),
            qty_locked=Qty(Decimal(str(data.get("qty_locked", 0)))),
            total_cost=Money(Decimal(str(data.get("total_cost", 0)))),
        )


@dataclass
class Account:
    """Virtual trading account."""
    account_id: AccountID
    cash_available: Money = field(default_factory=lambda: Money(Decimal(0)))
    cash_locked: Money = field(default_factory=lambda: Money(Decimal(0)))
    positions: Dict[Symbol, Position] = field(default_factory=dict)

    # ── fund locks (used per-order) ──────────────────────────
    _order_locks: Dict[OrderID, tuple[OrderSide, Symbol, Money, Qty]] = field(
        default_factory=dict, repr=False
    )

    # ── history ──────────────────────────────────────────────
    trade_history: list[TradeLot] = field(default_factory=list, repr=False)
    cash_transactions: list[CashTransaction] = field(default_factory=list, repr=False)

    # ── Order lock public API (DSD §1.6 #2: no cross-class private access)

    def has_order_lock(self, order_id: OrderID) -> bool:
        """Check if an order lock exists."""
        return order_id in self._order_locks

    def get_order_lock(self, order_id: OrderID) -> tuple[OrderSide, Symbol, Money, Qty] | None:
        """Get the order lock tuple, or None."""
        return self._order_locks.get(order_id)

    def set_order_lock(self, order_id: OrderID, lock: tuple[OrderSide, Symbol, Money, Qty]) -> None:
        """Set or update an order lock."""
        self._order_locks[order_id] = lock

    def pop_order_lock(self, order_id: OrderID) -> tuple[OrderSide, Symbol, Money, Qty] | None:
        """Remove and return an order lock, or None."""
        return self._order_locks.pop(order_id, None)

    @property
    def order_lock_ids(self) -> set[OrderID]:
        """Return the set of order IDs with active locks."""
        return set(self._order_locks.keys())

    # ── Serialization (DSD §2.5) ─────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "account_id": str(self.account_id),
            "cash_available": str(self.cash_available),
            "cash_locked": str(self.cash_locked),
            "positions": [
                {"symbol": str(sym), **pos.to_dict(symbol=str(sym))}
                for sym, pos in self.positions.items()
                if pos.total > 0
            ],
            "trade_history": [t.to_dict() for t in self.trade_history],
            "cash_transactions": [c.to_dict() for c in self.cash_transactions],
        }

    def to_snapshot(self) -> dict:
        """Minimal snapshot for persistence (no history)."""
        return {
            "account_id": str(self.account_id),
            "cash_available": str(self.cash_available),
            "cash_locked": str(self.cash_locked),
            "positions": [
                {
                    "symbol": str(sym),
                    "qty_available": str(pos.qty_available),
                    "qty_locked": str(pos.qty_locked),
                    "total_cost": str(pos.total_cost),
                }
                for sym, pos in self.positions.items()
                if pos.total > 0
            ],
        }

    # ── Queries ──────────────────────────────────────────────

    @property
    def total_cash(self) -> Money:
        return Money(self.cash_available + self.cash_locked)

    def get_position(self, symbol: Symbol) -> Position:
        return self.positions.setdefault(symbol, Position())

    # ── Cash management ──────────────────────────────────────

    def deposit(self, amount: Money) -> None:
        self.cash_available = Money(self.cash_available + amount)
        self.cash_transactions.append(CashTransaction(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tx_type="DEPOSIT",
            amount=amount,
            description=f"存入 {amount}",
        ))

    def withdraw(self, amount: Money) -> None:
        if self.cash_available < amount:
            raise InsufficientCashError("Insufficient cash for withdrawal")
        self.cash_available = Money(self.cash_available - amount)
        self.cash_transactions.append(CashTransaction(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tx_type="WITHDRAW",
            amount=-amount,
            description=f"提出 {amount}",
        ))


class AccountService:
    """
    Manages multiple accounts.
    Satisfies the FundChecker Protocol defined in OMS.
    """

    def __init__(self) -> None:
        self._accounts: Dict[AccountID, Account] = {}

    def get_or_create(self, account_id: AccountID) -> Account:
        if account_id not in self._accounts:
            self._accounts[account_id] = Account(account_id=account_id)
        return self._accounts[account_id]

    def get(self, account_id: AccountID) -> Account:
        return self._accounts[account_id]

    def list_all(self) -> list[Account]:
        return list(self._accounts.values())

    def delete_account(self, account_id: AccountID) -> bool:
        """Remove an account from the in-memory store."""
        return self._accounts.pop(account_id, None) is not None

    # ── FundChecker Protocol implementation ──────────────────

    def lock_funds(
        self,
        account_id: AccountID,
        order_id: OrderID,
        symbol: Symbol,
        side: OrderSide,
        qty: Qty,
        price: Money | None,
    ) -> None:
        """Lock cash (BUY) or shares (SELL) for an order."""
        acct = self.get_or_create(account_id)

        if side == OrderSide.BUY:
            if price is None:
                raise InsufficientCashError("MARKET orders need a reference price")
            cost = Money(price * qty)
            if acct.cash_available < cost:
                raise InsufficientCashError(
                    f"Need {cost}, available {acct.cash_available}"
                )
            acct.cash_available = Money(acct.cash_available - cost)
            acct.cash_locked = Money(acct.cash_locked + cost)
            acct.set_order_lock(order_id, (side, symbol, cost, qty))
            acct.cash_transactions.append(CashTransaction(
                timestamp=datetime.now(timezone.utc).isoformat(),
                tx_type="BUY_LOCK",
                amount=-cost,
                description=f"掛單鎖定 {symbol} {qty}股 @ {price}",
            ))

        else:  # SELL
            pos = acct.get_position(symbol)
            if pos.qty_available < qty:
                raise InsufficientPositionError(
                    f"Need {qty} of {symbol}, available {pos.qty_available}"
                )
            pos.qty_available = Qty(pos.qty_available - qty)
            pos.qty_locked = Qty(pos.qty_locked + qty)
            acct.set_order_lock(order_id, (
                side,
                symbol,
                Money(Decimal(0)),
                qty,
            ))

    def unlock_funds(
        self,
        account_id: AccountID,
        order_id: OrderID,
    ) -> None:
        """Reverse a lock (cancel / reject)."""
        acct = self.get_or_create(account_id)
        lock = acct.pop_order_lock(order_id)
        if lock is None:
            return
        side, symbol, locked_cash, locked_qty = lock

        if side == OrderSide.BUY:
            acct.cash_locked = Money(acct.cash_locked - locked_cash)
            acct.cash_available = Money(acct.cash_available + locked_cash)
        else:
            pos = acct.get_position(symbol)
            pos.qty_locked = Qty(pos.qty_locked - locked_qty)
            pos.qty_available = Qty(pos.qty_available + locked_qty)

    # ── Settlement (after trade execution) ───────────────────

    def settle_trade(
        self,
        buyer_account_id: AccountID,
        seller_account_id: AccountID,
        buyer_order_id: OrderID,
        seller_order_id: OrderID,
        symbol: Symbol,
        price: Money,
        qty: Qty,
    ) -> None:
        """
        Settle a trade between buyer and seller.
        Moves cash from buyer locked → seller available,
        and shares from seller locked → buyer available.
        """
        cost = Money(price * qty)

        buyer = self.get_or_create(buyer_account_id)
        seller = self.get_or_create(seller_account_id)

        # Buyer: release locked cash
        buyer.cash_locked = Money(buyer.cash_locked - cost)

        # Seller: receive cash
        seller.cash_available = Money(seller.cash_available + cost)

        # Seller: release locked shares
        seller_pos = seller.get_position(symbol)
        seller_pos.qty_locked = Qty(seller_pos.qty_locked - qty)

        # Buyer: receive shares + accumulate cost basis
        buyer_pos = buyer.get_position(symbol)
        buyer_pos.qty_available = Qty(buyer_pos.qty_available + qty)
        buyer_pos.total_cost = Money(buyer_pos.total_cost + cost)

        # Seller: reduce cost basis proportionally
        if seller_pos.total > 0:
            cost_per_share = seller_pos.total_cost / (seller_pos.total + qty)  # before reduction
            seller_pos.total_cost = Money(seller_pos.total_cost - cost_per_share * qty)
        else:
            seller_pos.total_cost = Money(Decimal(0))

        # Record trade history
        now_iso = datetime.now(timezone.utc).isoformat()
        buyer.trade_history.append(TradeLot(
            trade_id="", order_id=str(buyer_order_id), symbol=str(symbol),
            side="BUY", qty=qty, price=price, trade_date=now_iso,
        ))
        seller.trade_history.append(TradeLot(
            trade_id="", order_id=str(seller_order_id), symbol=str(symbol),
            side="SELL", qty=qty, price=price, trade_date=now_iso,
        ))
        buyer.cash_transactions.append(CashTransaction(
            timestamp=now_iso, tx_type="BUY_SETTLE", amount=-cost,
            description=f"買入成交 {symbol} {qty}股 @ {price}",
        ))
        seller.cash_transactions.append(CashTransaction(
            timestamp=now_iso, tx_type="SELL_SETTLE", amount=cost,
            description=f"賣出成交 {symbol} {qty}股 @ {price}",
        ))

        # Update order lock records (reduce locked amounts)
        self._reduce_order_lock(buyer, buyer_order_id, cost, Qty(Decimal(0)))
        self._reduce_order_lock(seller, seller_order_id, Money(Decimal(0)), qty)

    # ── Engine A: single-sided market settlement ─────────────

    def settle_market_trade(
        self,
        account_id: AccountID,
        order_id: OrderID,
        symbol: Symbol,
        side: OrderSide,
        price: Money,
        qty: Qty,
    ) -> None:
        """
        Settle a trade where the counterparty is the market (Engine A).
        Only the user's account is affected.
        BUY: release locked cash, add position.
        SELL: release locked shares, add cash.
        """
        cost = Money(price * qty)
        acct = self.get_or_create(account_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        if side == OrderSide.BUY:
            # Release locked cash for the actual trade cost
            acct.cash_locked = Money(acct.cash_locked - cost)
            # Add position
            pos = acct.get_position(symbol)
            pos.qty_available = Qty(pos.qty_available + qty)
            pos.total_cost = Money(pos.total_cost + cost)
            # Return excess locked cash (order price may be > trade price)
            lock = acct.get_order_lock(order_id)
            if lock:
                _, _, locked_cash, _ = lock
                excess = Money(locked_cash - cost)
                if excess > 0:
                    acct.cash_locked = Money(acct.cash_locked - excess)
                    acct.cash_available = Money(acct.cash_available + excess)
            # Record
            acct.trade_history.append(TradeLot(
                trade_id="", order_id=str(order_id), symbol=str(symbol),
                side="BUY", qty=qty, price=price, trade_date=now_iso,
            ))
            acct.cash_transactions.append(CashTransaction(
                timestamp=now_iso, tx_type="BUY_SETTLE", amount=-cost,
                description=f"買入成交(大盤) {symbol} {qty}股 @ {price}",
            ))
        else:
            # Release locked shares
            pos = acct.get_position(symbol)
            pos.qty_locked = Qty(pos.qty_locked - qty)
            # Reduce cost basis proportionally
            if pos.total > 0:
                cost_per_share = pos.total_cost / (pos.total + qty)
                pos.total_cost = Money(pos.total_cost - cost_per_share * qty)
            else:
                pos.total_cost = Money(Decimal(0))
            # Add cash
            acct.cash_available = Money(acct.cash_available + cost)
            # Record
            acct.trade_history.append(TradeLot(
                trade_id="", order_id=str(order_id), symbol=str(symbol),
                side="SELL", qty=qty, price=price, trade_date=now_iso,
            ))
            acct.cash_transactions.append(CashTransaction(
                timestamp=now_iso, tx_type="SELL_SETTLE", amount=cost,
                description=f"賣出成交(大盤) {symbol} {qty}股 @ {price}",
            ))

        # Remove order lock
        acct.pop_order_lock(order_id)

    def compute_leaderboard(
        self,
        users: list,
        market_data_cache: dict,
    ) -> list[dict]:
        """
        Compute ranked leaderboard for all users.

        Business logic: total assets = cash + holdings market value,
        P&L = total_assets - total_deposits, rank by P&L desc.

        Args:
            users: list of User objects (need user_id, username, display_name)
            market_data_cache: dict[Symbol, MarketDataUpdatedEvent]
        Returns:
            Sorted list of ranking dicts with rank assigned.
        """
        rankings: list[dict] = []

        for u in users:
            acct = self.get_or_create(u.user_id)
            # Total assets = cash + holdings market value
            total_cash = acct.cash_available + acct.cash_locked
            holdings_value = Decimal(0)
            for sym, pos in acct.positions.items():
                qty = pos.qty_available + pos.qty_locked
                md = market_data_cache.get(sym)
                if md and md.last_trade_price:
                    holdings_value += md.last_trade_price * qty
            total_assets = total_cash + holdings_value

            # Count trades and total volume
            trade_count = len(acct.trade_history)
            trade_volume = sum(t.qty * t.price for t in acct.trade_history)

            # P&L: total deposits as cost basis
            total_deposits = sum(
                tx.amount for tx in acct.cash_transactions if tx.tx_type == "DEPOSIT"
            )
            pnl = total_assets - total_deposits if total_deposits > 0 else Decimal(0)
            pnl_pct = (pnl / total_deposits * 100) if total_deposits > 0 else Decimal(0)

            rankings.append({
                "user_id": u.user_id,
                "username": u.username,
                "display_name": u.display_name or u.username,
                "total_assets": str(total_assets),
                "pnl": str(pnl.quantize(Decimal("0.01"))),
                "pnl_pct": str(pnl_pct.quantize(Decimal("0.01"))),
                "trade_count": trade_count,
                "trade_volume": str(trade_volume),
            })

        # Sort by P&L descending and assign rank
        rankings.sort(key=lambda r: Decimal(r["pnl"]), reverse=True)
        for i, r in enumerate(rankings, 1):
            r["rank"] = i

        return rankings

    @staticmethod
    def _reduce_order_lock(
        acct: Account,
        order_id: OrderID,
        cash_reduction: Money,
        qty_reduction: Qty,
    ) -> None:
        lock = acct.get_order_lock(order_id)
        if lock is None:
            return
        side, symbol, locked_cash, locked_qty = lock
        new_cash = Money(locked_cash - cash_reduction)
        new_qty = Qty(locked_qty - qty_reduction)
        if new_cash <= 0 and new_qty <= 0:
            acct.pop_order_lock(order_id)
        else:
            acct.set_order_lock(order_id, (side, symbol, new_cash, new_qty))
