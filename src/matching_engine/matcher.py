# src/matching_engine/matcher.py
"""
Pure matching engine – single-threaded, deterministic, no side effects.

Rules (DSD §4.6):
  1. Price priority  – better price first.
  2. Time priority   – same price ⇒ FIFO.
  3. Trade price      = Maker (resting order) price.
  4. Partial fills    – allowed; remainder stays on book.
  5. No fund logic    – ME never touches Account.
  6. Idempotent       – same order_id will not be double-matched.

Inputs :  OrderRoutedEvent  (from OMS)
Outputs:  list[TradeExecutedEvent]
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from src.common.clock import SYSTEM_CLOCK
from src.common.enums import OrderSide
from src.common.types import Money, OrderID, Qty, Symbol, TradeID
from src.events.order_events import OrderRemainOnBookEvent, OrderRoutedEvent
from src.events.trade_events import TradeExecutedEvent
from src.matching_engine.order_book import OrderBook
from src.matching_engine.price_time_priority import BookEntry


class Matcher:
    """
    Stateless matching logic.
    Maintains per-symbol OrderBooks in-memory.
    """

    def __init__(self) -> None:
        self._books: dict[Symbol, OrderBook] = defaultdict(
            lambda: OrderBook(symbol=Symbol(""))
        )
        self._seen_order_ids: set[OrderID] = set()  # dedup guard
        self._pending_remain_events: list[OrderRemainOnBookEvent] = []

    def get_or_create_book(self, symbol: Symbol) -> OrderBook:
        if symbol not in self._books:
            self._books[symbol] = OrderBook(symbol)
        return self._books[symbol]

    def get_book_depths(self) -> tuple[dict[str, int], dict[str, int]]:
        """Return (buy_depth, sell_depth) dicts for all symbols.

        Public interface — avoids external access to private ``_books``.
        """
        buy_depth: dict[str, int] = {}
        sell_depth: dict[str, int] = {}
        for sym, book in self._books.items():
            buy_depth[str(sym)] = len(book.buys)
            sell_depth[str(sym)] = len(book.sells)
        return buy_depth, sell_depth

    # ── Primary entry point ──────────────────────────────────

    def submit_order(
        self,
        order_id: OrderID,
        account_id: str,
        symbol: Symbol,
        side: OrderSide,
        price: Money,
        qty: Qty,
        timestamp: datetime | None = None,
    ) -> list[TradeExecutedEvent]:
        """
        Add an order to the book and attempt to match.
        Returns a list of TradeExecutedEvent (may be empty).
        """
        if order_id in self._seen_order_ids:
            return []  # idempotent – already processed
        self._seen_order_ids.add(order_id)

        ts = timestamp or SYSTEM_CLOCK.now()
        book = self.get_or_create_book(symbol)

        # Add to the appropriate side
        if side == OrderSide.BUY:
            book.add_buy(order_id, price, qty, ts, account_id)
        else:
            book.add_sell(order_id, price, qty, ts, account_id)

        # Attempt matching
        trades = self._match(book)

        # Emit OrderRemainOnBookEvent if order is not fully filled (DSD §9)
        remaining_entry = self._find_entry(book, order_id, side)
        if remaining_entry is not None:
            self._pending_remain_events.append(OrderRemainOnBookEvent(
                order_id=order_id,
                symbol=symbol,
                side=side,
                remaining_qty=remaining_entry.qty,
                original_qty=qty,
            ))

        return trades

    def cancel_order(self, symbol: Symbol, order_id: OrderID) -> bool:
        """Remove an order from the book. Returns True if found."""
        book = self.get_or_create_book(symbol)
        removed = book.cancel(order_id)
        return removed is not None

    # ── OrderRemainOnBookEvent drain (DSD §9) ────────────────

    def drain_remain_events(self) -> list[OrderRemainOnBookEvent]:
        """Return and clear pending OrderRemainOnBookEvents."""
        events = list(self._pending_remain_events)
        self._pending_remain_events.clear()
        return events

    # ── Cold-start replay (DSD §5) ───────────────────────────

    def replay_order(
        self,
        order_id: OrderID,
        account_id: str,
        symbol: Symbol,
        side: OrderSide,
        price: Money,
        qty: Qty,
        timestamp: datetime,
    ) -> None:
        """Add a resting order during cold-start rebuild (no matching).

        Unlike ``submit_order``, this does **not** trigger the matching
        loop.  Used to reconstruct the order book from an event log.
        """
        self._seen_order_ids.add(order_id)
        book = self.get_or_create_book(symbol)
        if side == OrderSide.BUY:
            book.add_buy(order_id, price, qty, timestamp, account_id)
        else:
            book.add_sell(order_id, price, qty, timestamp, account_id)

    # ── Bounded-context query API (DSD §15) ──────────────────

    def get_book_snapshot(self, symbol: Symbol) -> dict:
        """Return order book snapshot with price levels aggregated.

        This is the standardised query interface for external consumers.
        No direct memory access to ``_books`` is needed.
        """
        book = self._books.get(symbol)
        if book is None:
            return {"symbol": str(symbol), "bids": [], "asks": []}

        def _aggregate(side) -> list[dict]:
            from collections import OrderedDict
            levels: OrderedDict[str, dict] = OrderedDict()
            for entry in side:
                p = str(entry.price)
                if p not in levels:
                    levels[p] = {"price": p, "qty": Decimal(0), "order_count": 0}
                levels[p]["qty"] += entry.qty
                levels[p]["order_count"] += 1
            return [
                {"price": lv["price"], "qty": str(lv["qty"]), "order_count": lv["order_count"]}
                for lv in levels.values()
            ]

        return {
            "symbol": str(symbol),
            "bids": _aggregate(book.buys),
            "asks": _aggregate(book.sells),
        }

    # ── Internal helpers ─────────────────────────────────────

    def _find_entry(
        self, book: OrderBook, order_id: OrderID, side: OrderSide,
    ) -> BookEntry | None:
        """Locate a resting entry by order_id on the given side."""
        entries = book.buys if side == OrderSide.BUY else book.sells
        for e in entries:
            if e.order_id == order_id:
                return e
        return None

    # ── Core matching loop ───────────────────────────────────

    def _match(self, book: OrderBook) -> list[TradeExecutedEvent]:
        trades: list[TradeExecutedEvent] = []

        while book.is_crossable:
            bid = book.best_bid
            ask = book.best_ask
            assert bid is not None and ask is not None

            # Trade price = Maker (resting) price
            # The order that was on the book first is the maker.
            if bid.sort_time <= ask.sort_time:
                trade_price = bid.price   # buyer was resting → maker
                maker_side = OrderSide.BUY
                taker_side = OrderSide.SELL
            else:
                trade_price = ask.price   # seller was resting → maker
                maker_side = OrderSide.SELL
                taker_side = OrderSide.BUY

            trade_qty = min(bid.qty, ask.qty)

            trade_event = TradeExecutedEvent(
                trade_id=TradeID(uuid.uuid4()),
                symbol=book.symbol,
                price=trade_price,
                qty=trade_qty,
                buy_order_id=bid.order_id,
                sell_order_id=ask.order_id,
                maker_side=maker_side,
                taker_side=taker_side,
                matched_at=SYSTEM_CLOCK.now(),
            )
            trades.append(trade_event)

            # Update book quantities
            remaining_bid = Qty(bid.qty - trade_qty)
            remaining_ask = Qty(ask.qty - trade_qty)

            if remaining_bid == 0:
                book.buys.pop_top()
            else:
                book.buys.reduce_top(trade_qty)

            if remaining_ask == 0:
                book.sells.pop_top()
            else:
                book.sells.reduce_top(trade_qty)

        return trades
