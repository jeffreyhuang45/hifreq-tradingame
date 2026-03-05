# src/matching_engine/order_book.py
"""
Per-symbol order book with BUY and SELL sides.

Design rules (from DSD §4.6):
  • In-Memory, single-threaded.
  • BUY = MaxHeap (price DESC, time ASC).
  • SELL = MinHeap (price ASC, time ASC).
  • ME never touches Account – no fund logic here.
"""
from __future__ import annotations

from datetime import datetime

from src.common.types import Money, OrderID, Qty, Symbol
from src.matching_engine.price_time_priority import BookEntry, PriceSide


class OrderBook:
    """Order book for a single symbol."""

    def __init__(self, symbol: Symbol):
        self.symbol = symbol
        self.buys = PriceSide(is_buy=True)
        self.sells = PriceSide(is_buy=False)

    # ── Mutations ────────────────────────────────────────────

    def add_buy(self, order_id: OrderID, price: Money, qty: Qty,
                timestamp: datetime, account_id: str = "") -> None:
        self.buys.insert(order_id, price, qty, timestamp, account_id)

    def add_sell(self, order_id: OrderID, price: Money, qty: Qty,
                 timestamp: datetime, account_id: str = "") -> None:
        self.sells.insert(order_id, price, qty, timestamp, account_id)

    def cancel(self, order_id: OrderID) -> BookEntry | None:
        entry = self.buys.remove(order_id)
        if entry is None:
            entry = self.sells.remove(order_id)
        return entry

    # ── Queries ──────────────────────────────────────────────

    @property
    def best_bid(self) -> BookEntry | None:
        return self.buys.best

    @property
    def best_ask(self) -> BookEntry | None:
        return self.sells.best

    @property
    def is_crossable(self) -> bool:
        """True when best bid ≥ best ask → a trade can happen."""
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return False
        return bid.price >= ask.price

    def __repr__(self) -> str:
        return (
            f"OrderBook({self.symbol}  "
            f"bids={len(self.buys)}  asks={len(self.sells)})"
        )
