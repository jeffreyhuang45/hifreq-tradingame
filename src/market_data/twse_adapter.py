# src/market_data/twse_adapter.py
"""
Market Data Adapters (DSD §4.5 – Module 5).

Adapter pattern converts heterogeneous exchange data into the system's
unified StockQuote format.

Adapters:
  • TWSEAdapter      – real TWSE / TPEx OpenAPI
  • SimulatedAdapter – deterministic fake data for testing / demo
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from src.common.types import Money, Qty, Symbol


@dataclass
class StockQuote:
    """Standardized market data quote – the unified format."""
    symbol: str
    name: str
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    bid_price: Decimal
    ask_price: Decimal
    last_trade_price: Decimal
    change: Decimal
    bid_qty: int = 0
    ask_qty: int = 0
    last_trade_qty: int = 0
    trades_count: int = 0
    change_pct: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "open_price": str(self.open_price),
            "high_price": str(self.high_price),
            "low_price": str(self.low_price),
            "close_price": str(self.close_price),
            "volume": self.volume,
            "bid_price": str(self.bid_price),
            "ask_price": str(self.ask_price),
            "last_trade_price": str(self.last_trade_price),
            "change": str(self.change),
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "last_trade_qty": self.last_trade_qty,
            "trades_count": self.trades_count,
            "change_pct": str(self.change_pct),
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
        }

    @classmethod
    def from_dict(cls, data: dict) -> StockQuote:
        """Reconstruct from a dict."""
        ts_raw = data.get("timestamp", "")
        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            open_price=Decimal(str(data.get("open_price", 0))),
            high_price=Decimal(str(data.get("high_price", 0))),
            low_price=Decimal(str(data.get("low_price", 0))),
            close_price=Decimal(str(data.get("close_price", 0))),
            volume=int(data.get("volume", 0)),
            bid_price=Decimal(str(data.get("bid_price", 0))),
            ask_price=Decimal(str(data.get("ask_price", 0))),
            last_trade_price=Decimal(str(data.get("last_trade_price", 0))),
            change=Decimal(str(data.get("change", 0))),
            bid_qty=int(data.get("bid_qty", 0)),
            ask_qty=int(data.get("ask_qty", 0)),
            last_trade_qty=int(data.get("last_trade_qty", 0)),
            trades_count=int(data.get("trades_count", 0)),
            change_pct=Decimal(str(data.get("change_pct", 0))),
            timestamp=datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) and ts_raw else datetime.now(timezone.utc),
        )


@dataclass
class DailyOHLCV:
    """Single-day K-line bar from STOCK_DAY or simulated history."""
    date: str              # ISO-8601 date string, e.g. "2026-03-04"
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int            # shares (not lots)
    trade_value: int = 0   # total trade value in TWD
    trades_count: int = 0  # number of transactions
    change: Decimal = Decimal(0)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "date": self.date,
            "open": str(self.open_price),
            "high": str(self.high_price),
            "low": str(self.low_price),
            "close": str(self.close_price),
            "volume": self.volume,
            "trade_value": self.trade_value,
            "trades_count": self.trades_count,
            "change": str(self.change),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DailyOHLCV:
        """Reconstruct from a dict."""
        return cls(
            date=data.get("date", ""),
            open_price=Decimal(str(data.get("open", data.get("open_price", 0)))),
            high_price=Decimal(str(data.get("high", data.get("high_price", 0)))),
            low_price=Decimal(str(data.get("low", data.get("low_price", 0)))),
            close_price=Decimal(str(data.get("close", data.get("close_price", 0)))),
            volume=int(data.get("volume", 0)),
            trade_value=int(data.get("trade_value", 0)),
            trades_count=int(data.get("trades_count", 0)),
            change=Decimal(str(data.get("change", 0))),
        )


class MarketDataAdapter(ABC):
    """Abstract adapter for market data sources."""

    @abstractmethod
    async def fetch_quotes(self, symbols: list[str]) -> list[StockQuote]:
        """Fetch quotes for specific symbols."""
        ...

    @abstractmethod
    async def fetch_all_quotes(self) -> list[StockQuote]:
        """Fetch quotes for all available symbols."""
        ...

    @abstractmethod
    def available_symbols(self) -> list[dict[str, str]]:
        """Return list of {symbol, name} dicts."""
        ...

    @abstractmethod
    async def fetch_history(self, symbol: str, date: str = "") -> list[DailyOHLCV]:
        """Fetch monthly daily K-line bars for a single stock.

        Args:
            symbol: stock code, e.g. "2330"
            date: YYYYMMDD string (any day in the target month).
                  Defaults to current month if empty.

        Returns:
            List of DailyOHLCV sorted by date ascending.
        """
        ...


# ── TWSE / TPEx Adapter ─────────────────────────────────────

class TWSEAdapter(MarketDataAdapter):
    """
    Real adapter for 台灣證券交易所 (TWSE) and 櫃買中心 (TPEx).

    Endpoints:
      MIS:           https://mis.twse.com.tw/stock/api/getStockInfo.jsp
      STOCK_DAY_ALL: https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
      STOCK_DAY:     https://www.twse.com.tw/exchangeReport/STOCK_DAY
    """

    MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"

    # OTC (TPEx / 櫃買中心) stock codes typically start with these prefixes
    _OTC_PREFIXES = ("3", "4", "5", "6", "7", "8")

    def __init__(self) -> None:
        self._symbol_map: Dict[str, str] = {}   # symbol -> name

    async def fetch_quotes(self, symbols: list[str]) -> list[StockQuote]:
        """Fetch real-time quotes from TWSE MIS API."""
        try:
            import httpx
        except ImportError:
            return []

        if not symbols:
            return []

        # Build query: tse_2330.tw|tse_2317.tw|otc_6547.tw
        ex_ch = "|".join(
            f"{self._exchange_prefix(s)}_{s}.tw" for s in symbols
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(self.MIS_URL, params={"ex_ch": ex_ch, "json": "1"})
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        quotes: list[StockQuote] = []
        for item in data.get("msgArray", []):
            try:
                quotes.append(self._parse_mis_item(item))
            except Exception:
                continue
        return quotes

    async def fetch_all_quotes(self) -> list[StockQuote]:
        """Fetch daily summary for ALL stocks from TWSE OpenAPI."""
        try:
            import httpx
        except ImportError:
            return []

        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.get(self.TWSE_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        quotes: list[StockQuote] = []
        for item in data:
            try:
                quotes.append(self._parse_daily_item(item))
            except Exception:
                continue
        return quotes

    def available_symbols(self) -> list[dict[str, str]]:
        return [{"symbol": k, "name": v} for k, v in self._symbol_map.items()]

    def _exchange_prefix(self, symbol: str) -> str:
        """Return 'otc' for OTC/TPEx stocks, 'tse' for TWSE-listed."""
        if symbol and symbol[0] in self._OTC_PREFIXES and len(symbol) == 4:
            return "otc"
        return "tse"

    # ── Parsers ──────────────────────────────────────────────

    @staticmethod
    def _parse_mis_item(item: dict) -> StockQuote:
        """Parse a single item from MIS real-time API."""
        def _dec(v: str) -> Decimal:
            return Decimal(v) if v and v != "-" else Decimal(0)

        symbol = item.get("c", "")

        # MIS API: "g" = bid volume (lots), "f" = ask volume (lots)
        bid_vol_str = item.get("g", "0").split("_")[0]
        ask_vol_str = item.get("f", "0").split("_")[0]

        return StockQuote(
            symbol=symbol,
            name=item.get("n", ""),
            open_price=_dec(item.get("o", "0")),
            high_price=_dec(item.get("h", "0")),
            low_price=_dec(item.get("l", "0")),
            close_price=_dec(item.get("z", "0")),
            volume=int(item.get("v", "0")),
            bid_price=_dec(item.get("b", "0").split("_")[0]),
            ask_price=_dec(item.get("a", "0").split("_")[0]),
            last_trade_price=_dec(item.get("z", "0")),
            change=_dec(item.get("z", "0")) - _dec(item.get("y", "0")),
            bid_qty=int(_dec(bid_vol_str)),
            ask_qty=int(_dec(ask_vol_str)),
            last_trade_qty=int(_dec(item.get("tv", "0"))),
            trades_count=int(_dec(item.get("s", "0"))),
        )

    @staticmethod
    def _parse_daily_item(item: dict) -> StockQuote:
        """Parse a single item from TWSE daily summary."""
        def _dec(v: Any) -> Decimal:
            try:
                return Decimal(str(v).replace(",", ""))
            except Exception:
                return Decimal(0)

        return StockQuote(
            symbol=item.get("Code", ""),
            name=item.get("Name", ""),
            open_price=_dec(item.get("OpeningPrice", 0)),
            high_price=_dec(item.get("HighestPrice", 0)),
            low_price=_dec(item.get("LowestPrice", 0)),
            close_price=_dec(item.get("ClosingPrice", 0)),
            volume=int(_dec(item.get("TradeVolume", 0))),
            bid_price=_dec(item.get("ClosingPrice", 0)),
            ask_price=_dec(item.get("ClosingPrice", 0)),
            last_trade_price=_dec(item.get("ClosingPrice", 0)),
            change=_dec(item.get("Change", 0)),
            trades_count=int(_dec(item.get("Transaction", 0))),
        )

    async def fetch_history(self, symbol: str, date: str = "") -> list[DailyOHLCV]:
        """Fetch monthly daily K-line data from TWSE STOCK_DAY API.

        Args:
            symbol: stock code, e.g. "2330"
            date: YYYYMMDD (any day in the month). Defaults to today.
        """
        try:
            import httpx
        except ImportError:
            return []

        if not date:
            date = datetime.now().strftime("%Y%m%d")

        params = {"response": "json", "date": date, "stockNo": symbol}
        try:
            async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                resp = await client.get(self.STOCK_DAY_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if data.get("stat") != "OK":
            return []

        rows = data.get("data", [])
        result: list[DailyOHLCV] = []
        for row in rows:
            try:
                result.append(self._parse_stock_day_row(row))
            except Exception:
                continue
        return result

    @staticmethod
    def _parse_stock_day_row(row: list) -> DailyOHLCV:
        """Parse a single row from STOCK_DAY API.

        Row format (fields):
          [0] 日期          e.g. "115/03/04"
          [1] 成交股數      e.g. "84,647,010"
          [2] 成交金額      e.g. "159,499,070,920"
          [3] 開盤價        e.g. "1,895.00"
          [4] 最高價        e.g. "1,910.00"
          [5] 最低價        e.g. "1,865.00"
          [6] 收盤價        e.g. "1,865.00"
          [7] 漲跌價差      e.g. "-70.00"
          [8] 成交筆數      e.g. "728,396"
          [9] 註記          (ignore)
        """
        def _dec(v: str) -> Decimal:
            try:
                return Decimal(v.replace(",", ""))
            except Exception:
                return Decimal(0)

        # Convert ROC date (115/03/04) to ISO (2026-03-04)
        roc_date = row[0].strip()
        parts = roc_date.split("/")
        if len(parts) == 3:
            year = int(parts[0]) + 1911
            iso_date = f"{year}-{parts[1]}-{parts[2]}"
        else:
            iso_date = roc_date

        return DailyOHLCV(
            date=iso_date,
            open_price=_dec(row[3]),
            high_price=_dec(row[4]),
            low_price=_dec(row[5]),
            close_price=_dec(row[6]),
            volume=int(_dec(row[1])),
            trade_value=int(_dec(row[2])),
            trades_count=int(_dec(row[8])) if len(row) > 8 else 0,
            change=_dec(row[7]) if len(row) > 7 else Decimal(0),
        )


# ── Simulated Adapter (for testing / demo) ──────────────────

# Default stock catalog for simulation
_SIM_STOCKS: list[dict[str, Any]] = [
    {"symbol": "2330", "name": "台積電",   "base": 950},
    {"symbol": "2317", "name": "鴻海",     "base": 180},
    {"symbol": "2454", "name": "聯發科",   "base": 1250},
    {"symbol": "2308", "name": "台達電",   "base": 380},
    {"symbol": "2881", "name": "富邦金",   "base": 85},
    {"symbol": "2882", "name": "國泰金",   "base": 62},
    {"symbol": "2303", "name": "聯電",     "base": 55},
    {"symbol": "3711", "name": "日月光投控","base": 170},
    {"symbol": "2891", "name": "中信金",   "base": 32},
    {"symbol": "1301", "name": "台塑",     "base": 95},
    {"symbol": "2002", "name": "中鋼",     "base": 26},
    {"symbol": "2886", "name": "兆豐金",   "base": 45},
]


class SimulatedAdapter(MarketDataAdapter):
    """
    Generates realistic-looking but random market data.
    Good for testing and classroom demos without needing internet.
    """

    def __init__(self, stocks: list[dict[str, Any]] | None = None) -> None:
        self._stocks = stocks or _SIM_STOCKS
        # Track running prices for continuity
        self._prices: Dict[str, Decimal] = {
            s["symbol"]: Decimal(str(s["base"])) for s in self._stocks
        }
        self._volumes: Dict[str, int] = {s["symbol"]: 0 for s in self._stocks}
        self._names: Dict[str, str] = {s["symbol"]: s["name"] for s in self._stocks}

    async def fetch_quotes(self, symbols: list[str]) -> list[StockQuote]:
        return [self._generate(s) for s in symbols if s in self._prices]

    async def fetch_all_quotes(self) -> list[StockQuote]:
        return [self._generate(s["symbol"]) for s in self._stocks]

    def available_symbols(self) -> list[dict[str, str]]:
        return [{"symbol": s["symbol"], "name": s["name"]} for s in self._stocks]

    def _generate(self, symbol: str) -> StockQuote:
        """Generate a quote with small random price movement."""
        base = self._prices[symbol]
        # Random walk: ±0.5% max change
        pct = Decimal(str(random.uniform(-0.005, 0.005)))
        change = (base * pct).quantize(Decimal("0.01"))
        new_price = base + change
        if new_price <= 0:
            new_price = Decimal("0.01")
        self._prices[symbol] = new_price

        spread = max(Decimal("0.5"), (new_price * Decimal("0.001")).quantize(Decimal("0.01")))
        volume_delta = random.randint(100, 5000)
        self._volumes[symbol] += volume_delta

        open_price = base  # use previous price as "open"
        high_price = max(base, new_price) + Decimal(str(random.uniform(0, 1))).quantize(Decimal("0.01"))
        low_price = min(base, new_price) - Decimal(str(random.uniform(0, 1))).quantize(Decimal("0.01"))

        bid_qty = random.randint(1, 500)
        ask_qty = random.randint(1, 500)
        last_trade_qty = random.randint(1, 50)
        trades_count = random.randint(1, 200)

        return StockQuote(
            symbol=symbol,
            name=self._names.get(symbol, symbol),
            open_price=open_price,
            high_price=high_price,
            low_price=max(Decimal("0.01"), low_price),
            close_price=new_price,
            volume=self._volumes[symbol],
            bid_price=new_price - spread,
            ask_price=new_price + spread,
            last_trade_price=new_price,
            change=change,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            last_trade_qty=last_trade_qty,
            trades_count=trades_count,
            change_pct=(change / base * 100).quantize(Decimal("0.01")) if base else Decimal(0),
        )

    async def fetch_history(self, symbol: str, date: str = "") -> list[DailyOHLCV]:
        """Generate synthetic monthly K-line history for testing."""
        if symbol not in self._prices:
            return []

        base = float(self._prices[symbol])
        today = datetime.now(timezone.utc)
        bars: list[DailyOHLCV] = []
        # Generate up to 22 trading days in the current month
        for day in range(1, min(today.day + 1, 23)):
            try:
                d = today.replace(day=day)
            except ValueError:
                continue
            # Skip weekends
            if d.weekday() >= 5:
                continue
            pct = random.uniform(-0.02, 0.02)
            o = round(base * (1 + pct), 2)
            h = round(o * (1 + random.uniform(0, 0.015)), 2)
            l = round(o * (1 - random.uniform(0, 0.015)), 2)
            c = round(random.uniform(l, h), 2)
            vol = random.randint(10_000_000, 100_000_000)
            bars.append(DailyOHLCV(
                date=d.strftime("%Y-%m-%d"),
                open_price=Decimal(str(o)),
                high_price=Decimal(str(h)),
                low_price=Decimal(str(l)),
                close_price=Decimal(str(c)),
                volume=vol,
                trade_value=int(vol * c),
                trades_count=random.randint(10000, 500000),
                change=Decimal(str(round(c - o, 2))),
            ))
            base = c  # carry forward for continuity
        return bars
