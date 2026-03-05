# src/matching_engine/price_time_priority.py
"""
Priority-queue helpers that enforce **Price-Time Priority** (FIFO).

BUY side  : MaxHeap  → highest price first, earliest time breaks ties.
SELL side  : MinHeap  → lowest  price first, earliest time breaks ties.

Implementation uses a sorted list (bisect) instead of heapq so that
we can efficiently iterate & remove partial fills without re-heapifying.
For the expected ≤ 20 orders/s this is more than fast enough.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterator
from uuid import UUID

from src.common.types import Money, OrderID, Qty


@dataclass(order=True, frozen=True)
class BookEntry:
    """
    A single resting order on one side of the book.
    Sort key is designed so that ``sorted()`` naturally gives the
    correct priority for SELL side (price ASC, time ASC).
    For BUY side we negate the price before inserting.
    """
    sort_price: Decimal = field(compare=True)   # negated for BUY
    sort_time: datetime  = field(compare=True)
    order_id: OrderID    = field(compare=False)
    price: Money         = field(compare=False)
    qty: Qty             = field(compare=False, repr=True)
    account_id: str      = field(compare=False, default="")


class PriceSide:
    """Ordered list of BookEntry for one side (BUY or SELL)."""

    def __init__(self, *, is_buy: bool):
        self._is_buy = is_buy
        self._entries: list[BookEntry] = []

    # ── Mutations ────────────────────────────────────────────

    def insert(self, order_id: OrderID, price: Money, qty: Qty,
               timestamp: datetime, account_id: str = "") -> None:
        sort_price = -price if self._is_buy else price
        entry = BookEntry(
            sort_price=sort_price,
            sort_time=timestamp,
            order_id=order_id,
            price=price,
            qty=qty,
            account_id=account_id,
        )
        bisect.insort(self._entries, entry)

    def remove(self, order_id: OrderID) -> BookEntry | None:
        for i, e in enumerate(self._entries):
            if e.order_id == order_id:
                return self._entries.pop(i)
        return None

    def reduce_top(self, fill_qty: Qty) -> None:
        """Reduce the top entry's qty after a partial fill."""
        top = self._entries[0]
        new_qty = Qty(top.qty - fill_qty)
        # Frozen dataclass → replace
        self._entries[0] = BookEntry(
            sort_price=top.sort_price,
            sort_time=top.sort_time,
            order_id=top.order_id,
            price=top.price,
            qty=new_qty,
            account_id=top.account_id,
        )

    def pop_top(self) -> BookEntry:
        return self._entries.pop(0)

    # ── Queries ──────────────────────────────────────────────

    @property
    def best(self) -> BookEntry | None:
        return self._entries[0] if self._entries else None

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[BookEntry]:
        return iter(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)
