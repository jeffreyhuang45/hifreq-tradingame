# tests/unit/test_module5_gaps.py
"""
Module 5 – Market Data Processing Engine – gap tests.

G-1  Critical: _to_event() maps real bid_qty/ask_qty/last_trade_qty/trades_count
G-2  Medium:   DELETE /market-data/watch/{symbol} endpoint exists
G-3  Medium:   TWSEAdapter builds correct exchange prefix (tse_ vs otc_)
G-4  Medium:   Unit tests for MarketDataService / adapters (this file)
G-5  Low:      Bounded-context: public API replaces _last_quotes access
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.common.types import Money, Qty, Symbol
from src.events.market_events import MarketDataUpdatedEvent
from src.market_data.market_data_service import MarketDataService
from src.market_data.twse_adapter import SimulatedAdapter, StockQuote, TWSEAdapter, DailyOHLCV


# ── Helpers ──────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_quote(**overrides) -> StockQuote:
    """Build a StockQuote with sensible defaults, allowing overrides."""
    defaults = dict(
        symbol="2330",
        name="台積電",
        open_price=Decimal("950"),
        high_price=Decimal("960"),
        low_price=Decimal("940"),
        close_price=Decimal("955"),
        volume=12345,
        bid_price=Decimal("954"),
        ask_price=Decimal("956"),
        last_trade_price=Decimal("955"),
        change=Decimal("5"),
        bid_qty=100,
        ask_qty=200,
        last_trade_qty=10,
        trades_count=42,
        change_pct=Decimal("0.53"),
    )
    defaults.update(overrides)
    return StockQuote(**defaults)


# ═══════════════════════════════════════════════════════════════
# G-1: _to_event() maps real depth / trade-count fields
# ═══════════════════════════════════════════════════════════════

class TestToEventFieldMapping:

    def test_bid_qty_mapped(self):
        q = _make_quote(bid_qty=150)
        evt = MarketDataService._to_event(q)
        assert evt.bid_qty == Qty(Decimal(150))

    def test_ask_qty_mapped(self):
        q = _make_quote(ask_qty=300)
        evt = MarketDataService._to_event(q)
        assert evt.ask_qty == Qty(Decimal(300))

    def test_last_trade_qty_mapped(self):
        q = _make_quote(last_trade_qty=25)
        evt = MarketDataService._to_event(q)
        assert evt.last_trade_qty == Qty(Decimal(25))

    def test_last_trade_qty_none_when_zero(self):
        q = _make_quote(last_trade_qty=0)
        evt = MarketDataService._to_event(q)
        assert evt.last_trade_qty is None

    def test_trades_count_mapped(self):
        q = _make_quote(trades_count=99)
        evt = MarketDataService._to_event(q)
        assert evt.trades_count == 99

    def test_symbol_and_prices_mapped(self):
        q = _make_quote(symbol="2317", bid_price=Decimal("180"), ask_price=Decimal("181"))
        evt = MarketDataService._to_event(q)
        assert evt.symbol == Symbol("2317")
        assert evt.bid_price == Money(Decimal("180"))
        assert evt.ask_price == Money(Decimal("181"))

    def test_volume_mapped(self):
        q = _make_quote(volume=9999)
        evt = MarketDataService._to_event(q)
        assert evt.volume == Qty(Decimal(9999))


# ═══════════════════════════════════════════════════════════════
# G-1 + G-4: SimulatedAdapter generates all fields
# ═══════════════════════════════════════════════════════════════

class TestSimulatedAdapter:

    def test_generate_populates_depth_fields(self):
        adapter = SimulatedAdapter()
        quotes = _run(adapter.fetch_quotes(["2330"]))
        assert len(quotes) == 1
        q = quotes[0]
        # New fields must be present and non-negative
        assert q.bid_qty >= 0
        assert q.ask_qty >= 0
        assert q.last_trade_qty >= 0
        assert q.trades_count >= 0

    def test_generate_positive_depth(self):
        """Over multiple runs, bid_qty / ask_qty should be > 0."""
        adapter = SimulatedAdapter()
        positive = False
        for _ in range(10):
            quotes = _run(adapter.fetch_quotes(["2330"]))
            if quotes[0].bid_qty > 0 and quotes[0].ask_qty > 0:
                positive = True
                break
        assert positive, "bid_qty and ask_qty should be positive at least once in 10 runs"

    def test_available_symbols_returns_all(self):
        adapter = SimulatedAdapter()
        syms = adapter.available_symbols()
        assert len(syms) == 12
        symbols = {s["symbol"] for s in syms}
        assert "2330" in symbols
        assert "2317" in symbols

    def test_fetch_all_quotes(self):
        adapter = SimulatedAdapter()
        quotes = _run(adapter.fetch_all_quotes())
        assert len(quotes) == 12
        for q in quotes:
            assert q.volume >= 0

    def test_random_walk_changes_price(self):
        adapter = SimulatedAdapter()
        q1 = _run(adapter.fetch_quotes(["2330"]))[0]
        prices = {q1.close_price}
        for _ in range(20):
            q = _run(adapter.fetch_quotes(["2330"]))[0]
            prices.add(q.close_price)
        assert len(prices) > 1, "Random walk should produce varying prices"


# ═══════════════════════════════════════════════════════════════
# G-4: MarketDataService – watchlist management
# ═══════════════════════════════════════════════════════════════

class TestWatchlist:

    def _make_service(self) -> MarketDataService:
        adapter = SimulatedAdapter()
        return MarketDataService(
            adapter=adapter,
            on_market_data=lambda evt: None,
            poll_interval_s=999,
        )

    def test_add_to_watchlist(self):
        svc = self._make_service()
        svc.add_to_watchlist("9999")
        assert "9999" in svc.watchlist

    def test_remove_from_watchlist(self):
        svc = self._make_service()
        svc.add_to_watchlist("9999")
        svc.remove_from_watchlist("9999")
        assert "9999" not in svc.watchlist

    def test_remove_nonexistent_no_error(self):
        svc = self._make_service()
        svc.remove_from_watchlist("0000")  # should not raise


# ═══════════════════════════════════════════════════════════════
# G-4: MarketDataService – circuit breaker fallback
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreakerFallback:

    def test_fetch_now_returns_cached_when_cb_open(self):
        """When CB is open, fetch_now should return cached quotes."""
        captured: list[MarketDataUpdatedEvent] = []

        adapter = SimulatedAdapter()
        svc = MarketDataService(
            adapter=adapter,
            on_market_data=captured.append,
            poll_interval_s=999,
        )
        # Prime the cache
        quotes = _run(svc.fetch_now(["2330"]))
        assert len(quotes) == 1

        # Force circuit open
        svc._circuit_breaker._state = svc._circuit_breaker._state.__class__("OPEN")
        import time
        svc._circuit_breaker._last_failure_time = time.monotonic()

        # fetch_now should fall back to cache
        fallback = _run(svc.fetch_now(["2330"]))
        assert len(fallback) == 1
        assert fallback[0].symbol == "2330"

    def test_fetch_now_empty_when_cb_open_no_cache(self):
        """When CB is open and no cache, returns empty."""
        adapter = SimulatedAdapter()
        svc = MarketDataService(
            adapter=adapter,
            on_market_data=lambda e: None,
            poll_interval_s=999,
        )
        svc._circuit_breaker._state = svc._circuit_breaker._state.__class__("OPEN")
        import time
        svc._circuit_breaker._last_failure_time = time.monotonic()

        result = _run(svc.fetch_now(["2330"]))
        assert result == []


# ═══════════════════════════════════════════════════════════════
# G-5: Bounded-context public API
# ═══════════════════════════════════════════════════════════════

class TestPublicQuoteAPI:

    def test_get_last_quote_returns_none_before_fetch(self):
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        assert svc.get_last_quote("2330") is None

    def test_get_last_quote_returns_quote_after_fetch(self):
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        _run(svc.fetch_now(["2330"]))
        q = svc.get_last_quote("2330")
        assert q is not None
        assert q.symbol == "2330"

    def test_get_all_last_quotes_returns_copy(self):
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        _run(svc.fetch_now(["2330", "2317"]))
        all_q = svc.get_all_last_quotes()
        assert "2330" in all_q
        assert "2317" in all_q
        # Must be a copy, not the internal dict
        all_q["FAKE"] = None  # type: ignore
        assert svc.get_last_quote("FAKE") is None


# ═══════════════════════════════════════════════════════════════
# G-3: TWSEAdapter exchange prefix
# ═══════════════════════════════════════════════════════════════

class TestExchangePrefix:

    def test_tse_prefix_for_listed_stocks(self):
        adapter = TWSEAdapter()
        assert adapter._exchange_prefix("2330") == "tse"
        assert adapter._exchange_prefix("2317") == "tse"
        assert adapter._exchange_prefix("1301") == "tse"
        assert adapter._exchange_prefix("2881") == "tse"

    def test_otc_prefix_for_otc_stocks(self):
        adapter = TWSEAdapter()
        assert adapter._exchange_prefix("3711") == "otc"
        assert adapter._exchange_prefix("4904") == "otc"
        assert adapter._exchange_prefix("5871") == "otc"
        assert adapter._exchange_prefix("6547") == "otc"
        assert adapter._exchange_prefix("8069") == "otc"

    def test_tse_for_5digit_codes(self):
        """Five-digit codes (e.g., ETFs) default to tse."""
        adapter = TWSEAdapter()
        assert adapter._exchange_prefix("00878") == "tse"

    def test_tse_for_empty_string(self):
        adapter = TWSEAdapter()
        assert adapter._exchange_prefix("") == "tse"


# ═══════════════════════════════════════════════════════════════
# G-4: MarketDataService poll_once publishes events
# ═══════════════════════════════════════════════════════════════

class TestPollPublishing:

    def test_poll_once_publishes_events(self):
        """_poll_once should invoke on_market_data callback for each quote."""
        captured: list[MarketDataUpdatedEvent] = []
        adapter = SimulatedAdapter()
        svc = MarketDataService(
            adapter=adapter,
            on_market_data=captured.append,
            poll_interval_s=999,
        )
        svc._watchlist = {"2330", "2317"}
        _run(svc._poll_once())
        assert len(captured) == 2
        symbols = {e.symbol for e in captured}
        assert Symbol("2330") in symbols
        assert Symbol("2317") in symbols

    def test_poll_once_populates_depth_in_event(self):
        """Events from _poll_once should carry non-zero depth fields."""
        captured: list[MarketDataUpdatedEvent] = []
        adapter = SimulatedAdapter()
        svc = MarketDataService(
            adapter=adapter,
            on_market_data=captured.append,
            poll_interval_s=999,
        )
        svc._watchlist = {"2330"}
        _run(svc._poll_once())
        evt = captured[0]
        # G-1: these must no longer be hardcoded to zero
        assert evt.bid_qty > Qty(0) or evt.ask_qty > Qty(0)
        assert evt.trades_count > 0


# ═══════════════════════════════════════════════════════════════
# G-1: StockQuote new fields exist
# ═══════════════════════════════════════════════════════════════

class TestStockQuoteFields:

    def test_new_fields_have_defaults(self):
        """StockQuote should accept the 4 new fields with defaults."""
        q = StockQuote(
            symbol="TEST",
            name="Test",
            open_price=Decimal(100),
            high_price=Decimal(101),
            low_price=Decimal(99),
            close_price=Decimal(100),
            volume=1000,
            bid_price=Decimal(99),
            ask_price=Decimal(101),
            last_trade_price=Decimal(100),
            change=Decimal(0),
        )
        assert q.bid_qty == 0
        assert q.ask_qty == 0
        assert q.last_trade_qty == 0
        assert q.trades_count == 0

    def test_new_fields_accept_values(self):
        q = _make_quote(bid_qty=50, ask_qty=75, last_trade_qty=5, trades_count=12)
        assert q.bid_qty == 50
        assert q.ask_qty == 75
        assert q.last_trade_qty == 5
        assert q.trades_count == 12


# ═══════════════════════════════════════════════════════════════
# STOCK_DAY: TWSEAdapter._parse_stock_day_row()
# ═══════════════════════════════════════════════════════════════

class TestParseStockDayRow:

    def test_parse_roc_date_to_iso(self):
        """ROC date 115/03/04 → ISO 2026-03-04."""
        row = [
            "115/03/04", "84,647,010", "159,499,070,920",
            "1,895.00", "1,910.00", "1,865.00", "1,865.00",
            "-70.00", "728,396", "",
        ]
        bar = TWSEAdapter._parse_stock_day_row(row)
        assert bar.date == "2026-03-04"

    def test_parse_ohlcv_fields(self):
        row = [
            "115/03/02", "57,404,594", "113,190,016,272",
            "1,940.00", "1,995.00", "1,940.00", "1,975.00",
            "-20.00", "325,611", "",
        ]
        bar = TWSEAdapter._parse_stock_day_row(row)
        assert bar.open_price == Decimal("1940.00")
        assert bar.high_price == Decimal("1995.00")
        assert bar.low_price == Decimal("1940.00")
        assert bar.close_price == Decimal("1975.00")
        assert bar.volume == 57_404_594
        assert bar.trade_value == 113_190_016_272
        assert bar.trades_count == 325_611
        assert bar.change == Decimal("-20.00")

    def test_parse_positive_change(self):
        row = [
            "115/03/05", "10,000", "50,000,000",
            "100.00", "105.00", "99.00", "103.00",
            "+3.00", "1,000", "",
        ]
        bar = TWSEAdapter._parse_stock_day_row(row)
        assert bar.change == Decimal("3.00")

    def test_parse_comma_heavy_numbers(self):
        row = [
            "115/01/02", "1,234,567,890", "9,876,543,210,000",
            "12,345.00", "12,400.00", "12,300.00", "12,350.00",
            "50.00", "999,999", "",
        ]
        bar = TWSEAdapter._parse_stock_day_row(row)
        assert bar.volume == 1_234_567_890
        assert bar.trade_value == 9_876_543_210_000


class TestDailyOHLCVDataclass:

    def test_defaults(self):
        bar = DailyOHLCV(
            date="2026-03-04",
            open_price=Decimal(100),
            high_price=Decimal(105),
            low_price=Decimal(95),
            close_price=Decimal(102),
            volume=10000,
        )
        assert bar.trade_value == 0
        assert bar.trades_count == 0
        assert bar.change == Decimal(0)

    def test_full_construction(self):
        bar = DailyOHLCV(
            date="2026-03-04",
            open_price=Decimal("1895"),
            high_price=Decimal("1910"),
            low_price=Decimal("1865"),
            close_price=Decimal("1865"),
            volume=84647010,
            trade_value=159499070920,
            trades_count=728396,
            change=Decimal("-70"),
        )
        assert bar.date == "2026-03-04"
        assert bar.close_price == Decimal("1865")
        assert bar.trade_value == 159499070920


# ═══════════════════════════════════════════════════════════════
# STOCK_DAY: SimulatedAdapter.fetch_history()
# ═══════════════════════════════════════════════════════════════

class TestSimulatedHistory:

    def test_returns_bars_for_known_symbol(self):
        adapter = SimulatedAdapter()
        bars = _run(adapter.fetch_history("2330"))
        assert len(bars) > 0
        assert all(isinstance(b, DailyOHLCV) for b in bars)

    def test_returns_empty_for_unknown_symbol(self):
        adapter = SimulatedAdapter()
        bars = _run(adapter.fetch_history("9999"))
        assert bars == []

    def test_bars_have_valid_ohlcv(self):
        adapter = SimulatedAdapter()
        bars = _run(adapter.fetch_history("2330"))
        for bar in bars:
            assert bar.low_price <= bar.high_price
            assert bar.volume > 0
            assert bar.date  # non-empty
            assert bar.trades_count > 0

    def test_bars_sorted_by_date(self):
        adapter = SimulatedAdapter()
        bars = _run(adapter.fetch_history("2330"))
        dates = [b.date for b in bars]
        assert dates == sorted(dates)


# ═══════════════════════════════════════════════════════════════
# MarketDataService.fetch_history() – bounded context API
# ═══════════════════════════════════════════════════════════════

class TestServiceFetchHistory:

    def test_returns_list_of_dicts(self):
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        result = _run(svc.fetch_history("2330"))
        assert isinstance(result, list)
        assert len(result) > 0
        bar = result[0]
        assert "date" in bar
        assert "open" in bar
        assert "high" in bar
        assert "low" in bar
        assert "close" in bar
        assert "volume" in bar
        assert "trade_value" in bar
        assert "trades_count" in bar
        assert "change" in bar

    def test_returns_empty_for_unknown(self):
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        result = _run(svc.fetch_history("0000"))
        assert result == []

    def test_values_are_strings_for_decimals(self):
        """Decimal fields should be serialized as strings for JSON safety."""
        adapter = SimulatedAdapter()
        svc = MarketDataService(adapter=adapter, on_market_data=lambda e: None)
        result = _run(svc.fetch_history("2330"))
        bar = result[0]
        assert isinstance(bar["open"], str)
        assert isinstance(bar["close"], str)
        assert isinstance(bar["change"], str)
        assert isinstance(bar["volume"], int)
