# src/market_data/market_data_service.py
"""
Market Data Service (DSD §4.5 – Module 5).

Pub-Sub Producer:
  • Periodically polls the adapter for new quotes.
  • Converts quotes to MarketDataUpdatedEvent.
  • Pushes events to AppContext (cache + WebSocket broadcast).
  • Uses CircuitBreaker to handle adapter failures gracefully.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Callable

from src.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.common.types import Money, Qty, Symbol
from src.events.market_events import MarketDataUpdatedEvent
from src.market_data.twse_adapter import MarketDataAdapter, StockQuote

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Background service that polls market data and publishes events.
    Acts as the Pub-Sub *Producer*.
    """

    def __init__(
        self,
        adapter: MarketDataAdapter,
        on_market_data: Callable[[MarketDataUpdatedEvent], None],
        poll_interval_s: float = 5.0,
    ) -> None:
        self._adapter = adapter
        self._on_market_data = on_market_data
        self._poll_interval = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Watchlist – symbols the user is interested in
        self._watchlist: set[str] = set()

        # Circuit breaker for external API calls
        self._circuit_breaker = CircuitBreaker(
            name="market-data-adapter",
            failure_threshold=3,
            reset_timeout_s=30.0,
        )

        # Last known quotes (fallback when circuit is open)
        self._last_quotes: dict[str, StockQuote] = {}

    # ── Public API ───────────────────────────────────────────

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    def add_to_watchlist(self, symbol: str) -> None:
        self._watchlist.add(symbol)

    def remove_from_watchlist(self, symbol: str) -> None:
        self._watchlist.discard(symbol)

    @property
    def watchlist(self) -> set[str]:
        return set(self._watchlist)

    def available_symbols(self) -> list[dict[str, str]]:
        return self._adapter.available_symbols()

    def get_last_quote(self, symbol: str) -> StockQuote | None:
        """Public accessor for the last known StockQuote (bounded context API)."""
        return self._last_quotes.get(symbol)

    def get_all_last_quotes(self) -> dict[str, StockQuote]:
        """Public accessor for all cached StockQuotes (bounded context API)."""
        return dict(self._last_quotes)

    async def fetch_history(self, symbol: str, date: str = "") -> list[dict]:
        """Fetch monthly daily K-line bars via the adapter (bounded context API).

        Returns list of dicts with keys:
          date, open, high, low, close, volume, trade_value, trades_count, change
        """
        from src.market_data.twse_adapter import DailyOHLCV
        try:
            bars = await self._circuit_breaker.async_call(
                self._adapter.fetch_history, symbol, date
            )
        except CircuitOpenError:
            logger.warning("Circuit breaker OPEN – cannot fetch history")
            return []
        except Exception as e:
            logger.error("Failed to fetch history for %s: %s", symbol, e)
            return []

        return [
            {
                "date": bar.date,
                "open": str(bar.open_price),
                "high": str(bar.high_price),
                "low": str(bar.low_price),
                "close": str(bar.close_price),
                "volume": bar.volume,
                "trade_value": bar.trade_value,
                "trades_count": bar.trades_count,
                "change": str(bar.change),
            }
            for bar in bars
        ]

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        # Load default watchlist from adapter
        for item in self._adapter.available_symbols():
            self._watchlist.add(item["symbol"])
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("MarketDataService started (interval=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MarketDataService stopped")

    async def fetch_now(self, symbols: list[str] | None = None) -> list[StockQuote]:
        """Fetch quotes immediately (bypasses polling interval)."""
        target = symbols or list(self._watchlist)
        if not target:
            return []
        try:
            quotes = await self._circuit_breaker.async_call(
                self._adapter.fetch_quotes, target
            )
            for q in quotes:
                self._last_quotes[q.symbol] = q
            return quotes
        except CircuitOpenError:
            logger.warning("Circuit breaker OPEN – returning cached quotes")
            return [self._last_quotes[s] for s in target if s in self._last_quotes]
        except Exception as e:
            logger.error("Failed to fetch quotes: %s", e)
            return [self._last_quotes[s] for s in target if s in self._last_quotes]

    # ── Internal ─────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Poll error: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        if not self._watchlist:
            return

        symbols = list(self._watchlist)
        try:
            quotes = await self._circuit_breaker.async_call(
                self._adapter.fetch_quotes, symbols
            )
        except CircuitOpenError:
            logger.debug("Circuit open, skipping poll")
            return
        except Exception:
            logger.debug("Adapter error during poll")
            return

        for quote in quotes:
            self._last_quotes[quote.symbol] = quote
            event = self._to_event(quote)
            try:
                self._on_market_data(event)
            except Exception as e:
                logger.error("Error publishing market data: %s", e)

    @staticmethod
    def _to_event(q: StockQuote) -> MarketDataUpdatedEvent:
        return MarketDataUpdatedEvent(
            symbol=Symbol(q.symbol),
            bid_price=Money(q.bid_price),
            bid_qty=Qty(Decimal(q.bid_qty)),
            ask_price=Money(q.ask_price),
            ask_qty=Qty(Decimal(q.ask_qty)),
            last_trade_price=Money(q.last_trade_price),
            last_trade_qty=Qty(Decimal(q.last_trade_qty)) if q.last_trade_qty else None,
            volume=Qty(Decimal(q.volume)),
            trades_count=q.trades_count,
        )
