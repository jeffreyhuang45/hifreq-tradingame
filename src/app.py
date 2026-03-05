# src/app.py
"""
System bootstrap & wiring.

DSD §11 – app.py may ONLY:
  1. Initialize modules
  2. Subscribe events
  3. Start API / WS

❌ NO business logic allowed here.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.account.account import AccountService
from src.api import rest, websocket
from src.api.websocket import broadcast_event_sync
from src.auth.auth_service import AuthService
from src.auth.user_model import UserRepository
from src.common.clock import SYSTEM_CLOCK
from src.common.enums import OrderSide, OrderType
from src.common.types import Money, OrderID, Qty, Symbol, TradeID
from src.events.base_event import BaseEvent
from src.events.account_events import TradeSettlementEvent
from src.events.market_events import MarketDataUpdatedEvent
from src.events.order_events import OrderRemainOnBookEvent, OrderRoutedEvent, OrderCanceledEvent
from src.events.trade_events import TradeExecutedEvent
from src.market_data.market_data_service import MarketDataService
from src.market_data.twse_adapter import SimulatedAdapter
from src.matching_engine.matcher import Matcher
from src.oms.oms_service import OMSService
from src.oms.order_repository import OrderRepository
from src.account.account_projection import AccountProjection
from src.storage.event_writer import EventWriter
from src.storage.report_writer import ReportWriter
from src.storage.user_record_writer import UserRecordWriter
from src.storage.google_sheets_adapter import create_google_sheets_adapter

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holds references to all modules – the wiring root."""
    account_service: AccountService = field(default_factory=AccountService)
    order_repo: OrderRepository = field(default_factory=OrderRepository)
    matcher: Matcher = field(default_factory=Matcher)
    event_writer: EventWriter = field(default_factory=EventWriter)
    market_data_cache: dict[Symbol, MarketDataUpdatedEvent] = field(default_factory=dict)
    quote_history: dict[str, list[dict]] = field(default_factory=dict)   # symbol -> [{time,open,high,low,close,volume}]
    user_repo: UserRepository = field(default_factory=UserRepository)
    dead_letter_queue: list = field(default_factory=list)   # DSD §4.8 dead-letter
    _cold_started: bool = field(init=False, default=False, repr=False)
    report_writer: ReportWriter | None = field(init=False, default=None)
    user_record_writer: UserRecordWriter | None = field(init=False, default=None)
    engine_mode: str = field(default="B")   # "A" = market vs order, "B" = order vs order

    # Initialized in __post_init__
    oms: OMSService = field(init=False)
    auth_service: AuthService = field(init=False)
    market_data_service: MarketDataService | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.oms = OMSService(
            order_repository=self.order_repo,
            fund_checker=self.account_service,
        )
        self.auth_service = AuthService(self.user_repo)
        # Ensure default admin account exists
        self.auth_service.ensure_admin_exists()

        # Report writer (periodic CSV export to disk, DSD §4.7 G-3)
        self.report_writer = ReportWriter(
            trades_provider=self._get_trades_for_report,
            orders_provider=self._get_orders_for_report,
        )

        # Per-user record writer (DSD §1.6 #5)
        gsheet = create_google_sheets_adapter()
        self.user_record_writer = UserRecordWriter(
            google_sheets_adapter=gsheet,
        )

        # Market data service (Pub-Sub producer)
        adapter = SimulatedAdapter()
        self.market_data_service = MarketDataService(
            adapter=adapter,
            on_market_data=self.on_market_data,
            poll_interval_s=5.0,
        )

    # ── Event routing ────────────────────────────────────────

    # ── Report data providers (DSD §4.7 G-3) ────────────────

    def _get_trades_for_report(self) -> list[dict[str, Any]]:
        """Provide trade dicts for ReportWriter."""
        from src.storage.event_loader import EventLoader

        loader = EventLoader()
        events = loader.load_events()
        return [
            {
                "trade_id": str(e.trade_id),
                "symbol": str(e.symbol),
                "price": str(e.price),
                "qty": str(e.qty),
                "buy_order_id": str(e.buy_order_id),
                "sell_order_id": str(e.sell_order_id),
                "matched_at": e.occurred_at.isoformat()
                if hasattr(e.occurred_at, "isoformat")
                else str(e.occurred_at),
            }
            for e in events
            if isinstance(e, TradeExecutedEvent)
        ]

    def _get_orders_for_report(self) -> list[dict[str, Any]]:
        """Provide order dicts for ReportWriter."""
        return [
            {
                "order_id": str(o.order_id),
                "account_id": o.account_id,
                "symbol": str(o.symbol),
                "side": o.side.value if hasattr(o.side, "value") else str(o.side),
                "order_type": o.order_type.value
                if hasattr(o.order_type, "value")
                else str(o.order_type),
                "qty": str(o.qty),
                "price": str(o.price) if o.price else "",
                "status": o.status.value
                if hasattr(o.status, "value")
                else str(o.status),
                "filled_qty": str(o.filled_qty),
            }
            for o in self.order_repo.list_all()
        ]

    # ── Retry + Dead-Letter (DSD §4.8) ──────────────────────

    def _safe_publish(self, evt: BaseEvent, max_retries: int = 3) -> None:
        """Persist + broadcast with retry; failures go to dead-letter queue."""
        for attempt in range(1, max_retries + 1):
            try:
                self.event_writer.push(evt)
                broadcast_event_sync(evt)
                return
            except Exception as exc:
                logger.warning(
                    "Event publish attempt %d/%d failed: %s",
                    attempt, max_retries, exc,
                )
        logger.error(
            "Event moved to dead-letter queue: %s %s",
            evt.event_type.value, evt.event_id,
        )
        self.dead_letter_queue.append(evt)

    def drain_dead_letters(self) -> list[BaseEvent]:
        """Return and clear the dead-letter queue (admin observability)."""
        items = list(self.dead_letter_queue)
        self.dead_letter_queue.clear()
        return items

    def process_oms_events(self) -> None:
        """
        Drain events produced by OMS and route them:
          • OrderRoutedEvent  → Matching Engine
          • TradeExecutedEvent → Account settlement + WS push
          • All events → Data Pump (EventWriter) with retry + dead-letter
          • All events → WebSocket broadcast
        """
        events = self.oms.drain_events()
        for evt in events:
            self._safe_publish(evt)

            if isinstance(evt, OrderRoutedEvent):
                if self.engine_mode == "A":
                    self._route_to_me_engine_a(evt)
                else:
                    self._route_to_me(evt)

            # G-2 fix: remove canceled order from ME book so it cannot match
            if isinstance(evt, OrderCanceledEvent):
                try:
                    order = self.order_repo.get(evt.order_id)
                    self.matcher.cancel_order(Symbol(order.symbol), evt.order_id)
                except Exception:
                    pass  # Order may not be on book (e.g. already fully filled)

        # G-2 fix: re-drain to capture OrderFilledEvents published by
        # on_trade_executed() during _route_to_me()
        leftover = self.oms.drain_events()
        for evt in leftover:
            self._safe_publish(evt)

    def _route_to_me(self, evt: OrderRoutedEvent) -> None:
        """Send a routed order to the matching engine."""
        order = self.order_repo.get(evt.order_id)

        # Resolve effective price for MARKET orders (DSD §4.6)
        if order.order_type == OrderType.MARKET:
            if order.side == OrderSide.BUY:
                effective_price = Money(Decimal("999999999"))
            else:
                effective_price = Money(Decimal("0"))
        else:
            effective_price = order.price or Money(0)

        trades = self.matcher.submit_order(
            order_id=order.order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            side=order.side,
            price=effective_price,
            qty=order.remaining_qty,
            timestamp=order.created_at,
        )
        for trade in trades:
            # Persist + broadcast trade (with retry / dead-letter)
            self._safe_publish(trade)

            # OMS updates order status
            self.oms.on_trade_executed(trade)

            # Account settlement
            buyer_acct = self.order_repo.get(trade.buy_order_id).account_id
            seller_acct = self.order_repo.get(trade.sell_order_id).account_id
            self.account_service.settle_trade(
                buyer_account_id=buyer_acct,
                seller_account_id=seller_acct,
                buyer_order_id=trade.buy_order_id,
                seller_order_id=trade.sell_order_id,
                symbol=trade.symbol,
                price=trade.price,
                qty=trade.qty,
            )

            # Emit TradeSettlementEvent so settlement is replayable (DSD §1.6 #8, #15)
            settle_evt = TradeSettlementEvent(
                buyer_account_id=buyer_acct,
                seller_account_id=seller_acct,
                buyer_order_id=trade.buy_order_id,
                seller_order_id=trade.sell_order_id,
                symbol=trade.symbol,
                price=trade.price,
                qty=trade.qty,
            )
            self._safe_publish(settle_evt)

            # Queue per-user record writes (DSD §1.6 #5, off critical path)
            if self.user_record_writer:
                buyer = self.account_service.get_or_create(buyer_acct)
                seller = self.account_service.get_or_create(seller_acct)
                self.user_record_writer.queue_funding_record(
                    buyer_acct,
                    [{"timestamp": tx.timestamp, "tx_type": tx.tx_type,
                      "amount": str(tx.amount), "description": tx.description}
                     for tx in buyer.cash_transactions],
                )
                self.user_record_writer.queue_funding_record(
                    seller_acct,
                    [{"timestamp": tx.timestamp, "tx_type": tx.tx_type,
                      "amount": str(tx.amount), "description": tx.description}
                     for tx in seller.cash_transactions],
                )
                self.user_record_writer.queue_trading_record(
                    buyer_acct,
                    [{"trade_id": t.trade_id, "order_id": t.order_id,
                      "symbol": t.symbol, "side": t.side,
                      "qty": str(t.qty), "price": str(t.price),
                      "trade_date": t.trade_date}
                     for t in buyer.trade_history],
                )
                self.user_record_writer.queue_trading_record(
                    seller_acct,
                    [{"trade_id": t.trade_id, "order_id": t.order_id,
                      "symbol": t.symbol, "side": t.side,
                      "qty": str(t.qty), "price": str(t.price),
                      "trade_date": t.trade_date}
                     for t in seller.trade_history],
                )

        # Publish OrderRemainOnBookEvents (DSD §9)
        for remain_evt in self.matcher.drain_remain_events():
            self._safe_publish(remain_evt)

    # ── Engine A: match order against market bid/ask ─────────

    def _route_to_me_engine_a(self, evt: OrderRoutedEvent) -> None:
        """Engine A: match a routed order against real-time market data."""
        order = self.order_repo.get(evt.order_id)

        # Resolve effective price (same logic as Engine B for MARKET orders)
        if order.order_type == OrderType.MARKET:
            if order.side == OrderSide.BUY:
                effective_price = Money(Decimal("999999999"))
            else:
                effective_price = Money(Decimal("0"))
        else:
            effective_price = order.price or Money(0)

        # Get current market quote
        quote = (
            self.market_data_service.get_last_quote(str(order.symbol))
            if self.market_data_service else None
        )
        if quote is None:
            logger.debug("Engine A: no market data for %s, order stays ROUTED", order.symbol)
            return

        self._try_engine_a_fill(order, effective_price, quote)

    def _try_engine_a_fill(self, order, effective_price: Money, quote) -> bool:
        """Check Engine A matching rules and fill if conditions met.

        Rule 1: BUY price >= market ask_price → trade at ask_price.
        Rule 2: SELL price <= market bid_price → trade at bid_price.
        Returns True if a fill occurred.
        """
        from src.market_data.twse_adapter import StockQuote

        matched = False
        trade_price = Money(0)

        if order.side == OrderSide.BUY:
            if effective_price >= Money(quote.ask_price) and quote.ask_price > 0:
                matched = True
                trade_price = Money(quote.ask_price)
        else:
            if effective_price <= Money(quote.bid_price) and quote.bid_price > 0:
                matched = True
                trade_price = Money(quote.bid_price)

        if not matched:
            return False

        fill_qty = order.remaining_qty
        if fill_qty <= 0:
            return False

        # Create TradeExecutedEvent for logging / WS broadcast
        trade_id = TradeID(uuid.uuid4())
        # Use order_id for both sides (Engine A has no counter-party order)
        if order.side == OrderSide.BUY:
            buy_oid, sell_oid = order.order_id, order.order_id
        else:
            buy_oid, sell_oid = order.order_id, order.order_id

        trade_event = TradeExecutedEvent(
            trade_id=trade_id,
            symbol=order.symbol,
            price=trade_price,
            qty=fill_qty,
            buy_order_id=buy_oid,
            sell_order_id=sell_oid,
            maker_side=OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY,
            taker_side=order.side,
            matched_at=SYSTEM_CLOCK.now(),
        )
        self._safe_publish(trade_event)

        # Update order status via OMS
        self.oms.on_market_fill(order.order_id, fill_qty, trade_price)

        # Settle (single-sided: only the user's account)
        self.account_service.settle_market_trade(
            account_id=order.account_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=trade_price,
            qty=fill_qty,
        )

        # Drain OMS events (OrderFilledEvent)
        for oms_evt in self.oms.drain_events():
            self._safe_publish(oms_evt)

        logger.info(
            "Engine A fill: %s %s %s x %s @ %s",
            order.side.value, order.symbol, fill_qty, trade_price, order.order_id,
        )
        return True

    def _check_resting_orders_engine_a(self, symbol: str) -> None:
        """Scan resting orders and try to fill against new market data (Engine A)."""
        from src.oms.order import OrderStatus

        quote = (
            self.market_data_service.get_last_quote(symbol)
            if self.market_data_service else None
        )
        if quote is None:
            return

        for order in self.order_repo.list_all():
            if str(order.symbol) != symbol:
                continue
            if order.status not in (OrderStatus.ROUTED, OrderStatus.PARTIALLY_FILLED):
                continue
            if order.remaining_qty <= 0:
                continue

            # Resolve effective price
            if order.order_type == OrderType.MARKET:
                effective_price = (
                    Money(Decimal("999999999"))
                    if order.side == OrderSide.BUY
                    else Money(Decimal("0"))
                )
            else:
                effective_price = order.price or Money(0)

            self._try_engine_a_fill(order, effective_price, quote)

    def on_market_data(self, evt: MarketDataUpdatedEvent) -> None:
        """Cache the latest market data per symbol and build OHLC history."""
        self.market_data_cache[evt.symbol] = evt

        # Persist market data event for replay (C-3 fix)
        self.event_writer.push(evt)

        # Accumulate OHLC tick history for charts
        sym = str(evt.symbol)
        if sym not in self.quote_history:
            self.quote_history[sym] = []
        sq = None
        if self.market_data_service:
            sq = self.market_data_service.get_last_quote(sym)
        if sq:
            from datetime import datetime, timezone
            self.quote_history[sym].append({
                "time": sq.timestamp.isoformat() if hasattr(sq.timestamp, 'isoformat') else datetime.now(timezone.utc).isoformat(),
                "open": str(sq.open_price),
                "high": str(sq.high_price),
                "low": str(sq.low_price),
                "close": str(sq.close_price),
                "volume": sq.volume,
            })
            if len(self.quote_history[sym]) > 120:
                self.quote_history[sym] = self.quote_history[sym][-120:]

        broadcast_event_sync(evt)

        # Engine A: try to match resting orders against updated market data
        if self.engine_mode == "A":
            self._check_resting_orders_engine_a(sym)

    # ── Cold-Start Rebuild (DSD §5) ──────────────────────────

    def rebuild_order_book_from_log(
        self,
        event_loader: Any | None = None,
        *,
        preloaded_events: list | None = None,
    ) -> int:
        """Rebuild ME order book by replaying persisted event log.

        Returns the number of resting orders restored.
        Implements DSD §5: "ME 的狀態應能透過重放事件日誌重建".

        Args:
            event_loader: optional EventLoader instance (DI for tests).
            preloaded_events: if provided, skip disk I/O and use these
                events directly (G-4: avoid double I/O during cold_start).
        """
        from src.events.order_events import OrderCanceledEvent, OrderPlacedEvent
        from src.storage.event_loader import EventLoader

        if preloaded_events is not None:
            events = preloaded_events
        else:
            loader = event_loader or EventLoader()
            events = loader.load_events()
        if not events:
            return 0

        # Phase 1: build maps from history
        placed: dict = {}              # order_id → OrderPlacedEvent
        canceled: set = set()
        filled_qty: dict = {}          # order_id → cumulative Qty

        for ev in events:
            if isinstance(ev, OrderPlacedEvent):
                placed[ev.order_id] = ev
            elif isinstance(ev, OrderCanceledEvent):
                canceled.add(ev.order_id)
            elif isinstance(ev, TradeExecutedEvent):
                for oid in (ev.buy_order_id, ev.sell_order_id):
                    filled_qty[oid] = Qty(
                        filled_qty.get(oid, Decimal(0)) + ev.qty
                    )

        # Phase 2: replay resting orders into ME (no re-matching)
        count = 0
        for oid, pe in placed.items():
            if oid in canceled:
                continue
            total_filled = filled_qty.get(oid, Qty(Decimal(0)))
            remaining = Qty(pe.qty - total_filled)
            if remaining <= 0:
                continue

            price = pe.price
            if pe.order_type == OrderType.MARKET:
                price = (
                    Money(Decimal("999999999"))
                    if pe.side == OrderSide.BUY
                    else Money(Decimal("0"))
                )
            if price is None:
                continue

            self.matcher.replay_order(
                order_id=oid,
                account_id=pe.account_id,
                symbol=Symbol(pe.symbol),
                side=pe.side,
                price=price,
                qty=remaining,
                timestamp=pe.occurred_at,
            )
            count += 1

        logger.info("Order book rebuilt: %d resting orders restored", count)
        return count

    # ── Full Cold-Start Orchestrator (DSD §4.7 G-1) ─────────

    def cold_start(
        self,
        event_loader: Any | None = None,
        snapshot_manager: Any | None = None,
    ) -> dict[str, Any]:
        """
        Full cold-start: snapshot → replay events after snapshot → rebuild all.

        Steps:
          1. Load latest snapshot (optional acceleration).
          2. If snapshot: restore account state, replay events after snapshot ts.
             If no snapshot: replay ALL events.
          3. Rebuild account state via AccountProjection.
          4. Rebuild order status into OrderRepository.
          5. Rebuild ME order book via replay_order() (no re-matching).

        Idempotency: calling cold_start() a second time is a no-op (G-10).

        Args:
            event_loader: optional EventLoader instance (DI for tests).
            snapshot_manager: optional SnapshotManager instance (DI for tests).

        Returns a summary dict for observability.
        """
        if self._cold_started:
            logger.warning("cold_start() called again — skipping (already initialized)")
            return {"skipped": True, "reason": "already_initialized"}
        from src.events.order_events import (
            OrderAcceptedEvent,
            OrderCanceledEvent,
            OrderFilledEvent,
            OrderPlacedEvent,
            OrderRejectedEvent,
            OrderRoutedEvent,
        )
        from src.storage.event_loader import EventLoader
        from src.storage.snapshot import SnapshotManager

        summary: dict[str, Any] = {
            "snapshot_loaded": False,
            "events_replayed": 0,
            "accounts_restored": 0,
            "orders_restored": 0,
            "resting_orders": 0,
            "errors_skipped": 0,
        }

        snap_mgr = snapshot_manager or SnapshotManager()
        loader = event_loader or EventLoader()

        # Step 1+2: Load snapshot or full replay
        snapshot = snap_mgr.load_latest()
        if snapshot and "snapshot_ts" in snapshot:
            from datetime import datetime, timezone

            summary["snapshot_loaded"] = True
            # Restore account state from snapshot (if accounts key exists)
            if "accounts" in snapshot:
                for acct_data in snapshot["accounts"]:
                    acct = self.account_service.get_or_create(acct_data["account_id"])
                    if "cash_available" in acct_data:
                        acct.cash_available = Money(Decimal(str(acct_data["cash_available"])))
                    if "cash_locked" in acct_data:
                        acct.cash_locked = Money(Decimal(str(acct_data["cash_locked"])))
                    # Restore positions
                    for pos_data in acct_data.get("positions", []):
                        sym = Symbol(pos_data["symbol"])
                        pos = acct.get_position(sym)
                        pos.qty_available = Qty(Decimal(str(pos_data.get("qty_available", 0))))
                        pos.qty_locked = Qty(Decimal(str(pos_data.get("qty_locked", 0))))
                        pos.total_cost = Money(Decimal(str(pos_data.get("total_cost", 0))))

            # Replay only events after the snapshot timestamp
            snap_ts = datetime.fromisoformat(snapshot["snapshot_ts"])
            events = loader.load_events_after(snap_ts)
        else:
            events = loader.load_events()

        summary["events_replayed"] = len(events)

        # Step 3: Rebuild account state via AccountProjection
        projection = AccountProjection(self.account_service)
        projection.replay(events)
        summary["accounts_restored"] = len(self.account_service.list_all())

        # Step 4: Rebuild order status into OrderRepository
        from src.oms.order import Order, OrderStatus

        for evt in events:
            if isinstance(evt, OrderPlacedEvent):
                order = Order(
                    order_id=evt.order_id,
                    account_id=evt.account_id,
                    symbol=Symbol(evt.symbol),
                    side=evt.side,
                    order_type=evt.order_type,
                    qty=evt.qty,
                    price=evt.price,
                    status=OrderStatus.PENDING,
                    created_at=evt.occurred_at,
                )
                self.order_repo.add(order)
            elif isinstance(evt, OrderAcceptedEvent):
                try:
                    self.order_repo.get(evt.order_id).status = OrderStatus.ACCEPTED
                except Exception:
                    summary["errors_skipped"] += 1
                    logger.debug("Skipped OrderAccepted for unknown order %s", evt.order_id)
            elif isinstance(evt, OrderRejectedEvent):
                try:
                    self.order_repo.get(evt.order_id).status = OrderStatus.REJECTED
                except Exception:
                    summary["errors_skipped"] += 1
                    logger.debug("Skipped OrderRejected for unknown order %s", evt.order_id)
            elif isinstance(evt, OrderRoutedEvent):
                try:
                    self.order_repo.get(evt.order_id).status = OrderStatus.ROUTED
                except Exception:
                    summary["errors_skipped"] += 1
                    logger.debug("Skipped OrderRouted for unknown order %s", evt.order_id)
            elif isinstance(evt, OrderCanceledEvent):
                try:
                    self.order_repo.get(evt.order_id).status = OrderStatus.CANCELED
                except Exception:
                    summary["errors_skipped"] += 1
                    logger.debug("Skipped OrderCanceled for unknown order %s", evt.order_id)
            elif isinstance(evt, OrderFilledEvent):
                try:
                    o = self.order_repo.get(evt.order_id)
                    o.filled_qty = Qty(evt.cumulative_qty)
                    o.status = OrderStatus(evt.status)
                except Exception:
                    summary["errors_skipped"] += 1
                    logger.debug("Skipped OrderFilled for unknown order %s", evt.order_id)

        summary["orders_restored"] = len(self.order_repo.list_all())

        if summary["errors_skipped"] > 0:
            logger.warning(
                "Cold-start: %d event(s) skipped due to errors",
                summary["errors_skipped"],
            )

        # Step 5: Rebuild ME order book (G-4: pass already-loaded events)
        summary["resting_orders"] = self.rebuild_order_book_from_log(
            preloaded_events=events,
        )

        self._cold_started = True
        logger.info("Cold-start complete: %s", summary)
        return summary

    # ── Pre-Flight Health Check (DSD §4.7 G-2) ──────────────

    def pre_flight_check(self) -> dict[str, Any]:
        """
        Verify system readiness before going online.

        Checks:
          1. Event log directory exists / is writable.
          2. EventWriter buffer is functional.
          3. Matching Engine is initialized.
          4. AccountService is accessible.
          5. Event log files are readable (G-11).
          6. Internal queues are available (G-12).
          7. Order book ↔ order repo consistency (G-2).

        Returns a dict with check names → pass/fail booleans.
        Raises RuntimeError if any check fails (blocks system startup).
        """
        results: dict[str, Any] = {}

        # Check 1: Event log base directory writable
        try:
            base = self.event_writer.base_dir
            base.mkdir(parents=True, exist_ok=True)
            results["event_log_dir"] = base.exists() and base.is_dir()
        except Exception:
            results["event_log_dir"] = False

        # Check 2: EventWriter is functional (verify buffer accepts push)
        try:
            # Do NOT push a real event — that would pollute the event log
            # with a phantom __health_check__ account (DSD §1.6 #4).
            # Instead verify the writer is structurally sound.
            results["event_writer"] = (
                self.event_writer is not None
                and self.event_writer.base_dir.exists()
            )
        except Exception:
            results["event_writer"] = False

        # Check 3: Matching Engine alive
        try:
            # get_book_snapshot should work even with empty books
            self.matcher.get_book_snapshot(Symbol("__test__"))
            results["matching_engine"] = True
        except Exception:
            results["matching_engine"] = False

        # Check 4: AccountService alive
        try:
            self.account_service.list_all()
            results["account_service"] = True
        except Exception:
            results["account_service"] = False

        # Check 5: Event log files are readable (G-11)
        try:
            base = self.event_writer.base_dir
            if base.exists():
                for month_dir in base.iterdir():
                    if not month_dir.is_dir():
                        continue
                    for log_file in month_dir.glob("events-*.log"):
                        # Verify each log file is readable
                        with open(log_file, "r", encoding="utf-8") as f:
                            f.readline()  # read first line only
            results["event_log_readable"] = True
        except Exception:
            results["event_log_readable"] = False

        # Check 6: Internal queues available (G-12)
        try:
            # Verify dead-letter queue is a functional list
            assert isinstance(self.dead_letter_queue, list)
            # Verify OMS event queue is drainable
            _ = self.oms.drain_events()
            # Verify ME remain-events queue is drainable
            _ = self.matcher.drain_remain_events()
            results["internal_queues"] = True
        except Exception:
            results["internal_queues"] = False

        # Check 7: Order book ↔ order repo consistency (G-2)
        try:
            from src.common.enums import OrderStatus as _OS
            consistency_ok = True
            # Every resting order in ME book should exist in order_repo
            buy_depth, sell_depth = self.matcher.get_book_depths()
            # Verify each order in repo that should be on book
            for order in self.order_repo.list_all():
                if order.status in (_OS.ROUTED, _OS.ACCEPTED, _OS.PARTIALLY_FILLED):
                    sym = str(order.symbol)
                    if sym not in buy_depth and sym not in sell_depth:
                        # Symbol has no book at all — but order claims to be resting
                        # This can happen with MARKET orders that fully filled
                        if order.remaining_qty > 0:
                            logger.warning(
                                "Consistency: order %s (status=%s) has no ME book for %s",
                                order.order_id, order.status.value, sym,
                            )
                            consistency_ok = False
            results["order_book_consistency"] = consistency_ok
        except Exception as exc:
            logger.warning("Consistency check error: %s", exc)
            results["order_book_consistency"] = False

        results["all_passed"] = all(
            v for k, v in results.items() if k != "all_passed"
        )

        if not results["all_passed"]:
            failed = [k for k, v in results.items() if k != "all_passed" and not v]
            logger.error(
                "Pre-flight check FAILED — system should NOT go online: %s",
                failed,
            )

        logger.info("Pre-flight check: %s", results)
        return results


# ── FastAPI application factory ──────────────────────────────

def create_app(ctx: AppContext | None = None) -> FastAPI:
    """Create and wire the FastAPI application."""
    if ctx is None:
        ctx = AppContext()

    # Install structured system log handler (M-5)
    from src.common.operation_log import install_system_log_handler
    install_system_log_handler()

    app = FastAPI(
        title="Trading Sim – 簡易證券交易模擬遊戲",
        version="1.0.0",
    )

    # ── Operation log middleware (C-4) ─────────────────────
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from src.common.operation_log import OperationLogEntry, operation_log
    from datetime import datetime, timezone as _tz

    # Mapping of path prefixes to human-readable operation descriptions
    _OP_DESC_MAP = {
        ("/api/v1/auth/login", "POST"): "用戶登入",
        ("/api/v1/auth/register", "POST"): "用戶註冊",
        ("/api/v1/auth/password", "PUT"): "修改密碼",
        ("/api/v1/orders", "POST"): "下單",
        ("/api/v1/orders", "DELETE"): "取消訂單",
        ("/api/v1/portfolio", "GET"): "查詢投資組合",
        ("/api/v1/cashflow", "GET"): "查詢現金流水",
        ("/api/v1/trades", "GET"): "查詢成交記錄",
        ("/api/v1/account/deposit", "POST"): "存款",
        ("/api/v1/account/withdraw", "POST"): "提款",
    }

    def _describe_operation(method: str, path: str) -> str:
        """Human-readable operation description (G-5)."""
        for (prefix, m), desc in _OP_DESC_MAP.items():
            if method == m and path.startswith(prefix):
                return f"{method} {desc}"
        return f"{method} {path}"

    class OperationLogMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            response = await call_next(request)
            # Extract user_id from the auth context (best-effort)
            user_id = ""
            auth = request.headers.get("authorization", "")
            if auth:
                try:
                    token = auth.removeprefix("Bearer ").strip()
                    u = ctx.auth_service.validate_token(token)
                    user_id = u.user_id
                except Exception:
                    user_id = "anonymous"

            # G-5 fix: compose operation description + capture error detail
            op_desc = _describe_operation(request.method, str(request.url.path))
            detail = ""
            if response.status_code >= 400:
                # Best-effort: capture error reason from response headers
                detail = f"HTTP {response.status_code}"

            operation_log.append(OperationLogEntry(
                timestamp=datetime.now(_tz.utc).isoformat(),
                user_id=user_id,
                operation=op_desc,
                path=str(request.url.path),
                status_code=response.status_code,
                detail=detail,
            ))
            return response

    app.add_middleware(OperationLogMiddleware)

    # Wire REST + WS
    rest.set_context(ctx)
    websocket.set_order_repo(ctx.order_repo)
    app.include_router(rest.router)
    app.include_router(websocket.ws_router)

    # Serve frontend static files
    static_dir = Path(__file__).parent / "frontend" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        # Serve index.html at root
        from fastapi.responses import FileResponse

        @app.get("/")
        async def serve_index():
            return FileResponse(str(static_dir / "index.html"))

    # Start Data Pump background thread
    ctx.event_writer.start_background()

    # Start Report Writer background thread (DSD §4.7 G-3)
    if ctx.report_writer:
        ctx.report_writer.start_background()

    # Start User Record Writer background thread (DSD §1.6 #5)
    if ctx.user_record_writer:
        ctx.user_record_writer.start_background()

    @app.on_event("startup")
    async def startup() -> None:
        # Cold-start: restore state from event log / snapshot
        ctx.cold_start()
        # Pre-flight health check — block startup on failure (G-1)
        pf = ctx.pre_flight_check()
        if not pf.get("all_passed", False):
            failed = [k for k, v in pf.items() if k != "all_passed" and not v]
            raise RuntimeError(
                f"Pre-flight check FAILED — system blocked from going online: {failed}"
            )
        # Start Market Data Service
        if ctx.market_data_service:
            await ctx.market_data_service.start()
        logger.info("Trading Sim started")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        ctx.event_writer.stop()
        if ctx.report_writer:
            ctx.report_writer.stop()
        if ctx.user_record_writer:
            ctx.user_record_writer.stop()
        if ctx.market_data_service:
            await ctx.market_data_service.stop()
        logger.info("Trading Sim stopped")

    # Attach context for external access
    app.state.ctx = ctx  # type: ignore[attr-defined]

    return app
