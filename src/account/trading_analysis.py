# src/account/trading_analysis.py
"""
Trading Behavior Analysis & Investment Prediction module.

DSD §3 – Module 3 Requirements:
  • Trading behavior analysis (buy/sell ratio, frequency, concentration)
  • Trade statistics (win rate, avg size, most traded)
  • Investment prediction foundation (momentum, volatility, returns)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from src.account.account import Account, TradeLot


def compute_trade_statistics(
    acct: Account,
    market_data_cache: dict,
) -> dict[str, Any]:
    """
    Compute comprehensive trade statistics for one account (M-3).

    Returns: total trades, volume, buy/sell breakdown, avg size,
    most traded symbols, win rate, realized P&L.
    """
    trades = acct.trade_history
    if not trades:
        return {
            "total_trades": 0,
            "total_volume": "0",
            "total_buy_trades": 0,
            "total_sell_trades": 0,
            "avg_trade_size": "0",
            "most_traded_symbols": [],
            "win_rate": "0",
            "realized_pnl": "0",
        }

    buy_trades = [t for t in trades if t.side == "BUY"]
    sell_trades = [t for t in trades if t.side == "SELL"]
    total_volume = sum(t.qty * t.price for t in trades)
    avg_size = total_volume / len(trades) if trades else Decimal(0)

    # Most traded symbols
    sym_counts: Counter[str] = Counter()
    sym_volume: dict[str, Decimal] = defaultdict(Decimal)
    for t in trades:
        sym_counts[t.symbol] += 1
        sym_volume[t.symbol] += t.qty * t.price
    most_traded = [
        {"symbol": sym, "count": cnt, "volume": str(sym_volume[sym])}
        for sym, cnt in sym_counts.most_common(5)
    ]

    # Win rate: compute realized P&L per symbol using FIFO matching
    realized_pnl, win_count, loss_count = _compute_realized_pnl(trades)
    total_closed = win_count + loss_count
    win_rate = (Decimal(win_count) / total_closed * 100) if total_closed > 0 else Decimal(0)

    return {
        "total_trades": len(trades),
        "total_volume": str(total_volume.quantize(Decimal("0.01"))),
        "total_buy_trades": len(buy_trades),
        "total_sell_trades": len(sell_trades),
        "avg_trade_size": str(avg_size.quantize(Decimal("0.01"))),
        "most_traded_symbols": most_traded,
        "win_rate": str(win_rate.quantize(Decimal("0.01"))),
        "realized_pnl": str(realized_pnl.quantize(Decimal("0.01"))),
    }


def compute_behavior_analysis(acct: Account) -> dict[str, Any]:
    """
    Analyze trading behavior patterns (C-5).

    Returns: buy/sell ratio, avg trade size, trade frequency,
    most traded, position concentration, holding period, win/loss.
    """
    trades = acct.trade_history
    if not trades:
        return {
            "buy_sell_ratio": "0",
            "avg_trade_size": "0",
            "trade_frequency": "0 trades/day",
            "most_traded_symbols": [],
            "position_concentration": [],
            "holding_period_avg": "N/A",
            "win_loss_ratio": "0",
        }

    buy_count = sum(1 for t in trades if t.side == "BUY")
    sell_count = sum(1 for t in trades if t.side == "SELL")
    ratio = (Decimal(buy_count) / sell_count) if sell_count > 0 else Decimal(buy_count)

    total_volume = sum(t.qty * t.price for t in trades)
    avg_size = total_volume / len(trades)

    # Trade frequency (trades per day based on date span)
    dates = sorted(set(t.trade_date[:10] for t in trades))
    day_span = max(len(dates), 1)
    freq = Decimal(len(trades)) / day_span

    # Symbol counts
    sym_counts: Counter[str] = Counter(t.symbol for t in trades)
    most_traded = [
        {"symbol": sym, "count": cnt}
        for sym, cnt in sym_counts.most_common(5)
    ]

    # Position concentration (Herfindahl-like)
    total_vol_by_sym: dict[str, Decimal] = defaultdict(Decimal)
    for t in trades:
        total_vol_by_sym[t.symbol] += t.qty * t.price
    grand_total = sum(total_vol_by_sym.values()) or Decimal(1)
    concentration = [
        {
            "symbol": sym,
            "pct": str((vol / grand_total * 100).quantize(Decimal("0.01"))),
        }
        for sym, vol in sorted(total_vol_by_sym.items(), key=lambda x: x[1], reverse=True)
    ]

    # Win/loss from realized P&L
    _rpnl, wins, losses = _compute_realized_pnl(trades)
    wl_ratio = (Decimal(wins) / losses) if losses > 0 else Decimal(wins)

    return {
        "buy_sell_ratio": str(ratio.quantize(Decimal("0.01"))),
        "avg_trade_size": str(avg_size.quantize(Decimal("0.01"))),
        "trade_frequency": f"{freq.quantize(Decimal('0.1'))} trades/day",
        "most_traded_symbols": most_traded,
        "position_concentration": concentration,
        "holding_period_avg": f"{day_span} days span",
        "win_loss_ratio": str(wl_ratio.quantize(Decimal("0.01"))),
    }


def compute_predictions(
    market_data_cache: dict,
    quote_history: dict[str, list[dict]],
) -> list[dict[str, Any]]:
    """
    Simple investment prediction / momentum ranking (M-6).

    For each symbol with enough history, compute:
      • Average return (close-to-close %)
      • Volatility (stddev of returns)
      • Momentum score (avg_return / volatility, i.e. Sharpe-like)
      • Simple recommendation: BUY if momentum > 0.5, SELL if < -0.5, else HOLD
    """
    results = []
    for sym, ticks in quote_history.items():
        if len(ticks) < 3:
            continue
        closes = [Decimal(t["close"]) for t in ticks if Decimal(t["close"]) > 0]
        if len(closes) < 3:
            continue

        # Returns
        returns = []
        for i in range(1, len(closes)):
            ret = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
            returns.append(ret)

        avg_return = sum(returns) / len(returns)
        mean = avg_return
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        volatility = variance.sqrt() if hasattr(variance, "sqrt") else Decimal(variance ** Decimal("0.5"))

        momentum = (avg_return / volatility) if volatility > 0 else Decimal(0)

        if momentum > Decimal("0.5"):
            recommendation = "BUY"
        elif momentum < Decimal("-0.5"):
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        results.append({
            "symbol": sym,
            "momentum_score": str(momentum.quantize(Decimal("0.01"))),
            "avg_return": str(avg_return.quantize(Decimal("0.01"))),
            "volatility": str(volatility.quantize(Decimal("0.01"))),
            "recommendation": recommendation,
        })

    # Sort by momentum score descending
    results.sort(key=lambda r: Decimal(r["momentum_score"]), reverse=True)
    return results


# ── Helper: FIFO realized P&L ────────────────────────────────

def _compute_realized_pnl(
    trades: list[TradeLot],
) -> tuple[Decimal, int, int]:
    """
    Compute realized P&L using simplified FIFO matching.
    Returns (total_realized_pnl, win_count, loss_count).
    """
    # Per-symbol buy queue (FIFO) and sell matching
    buy_queues: dict[str, list[tuple[Decimal, Decimal]]] = defaultdict(list)  # (qty, price)
    realized = Decimal(0)
    wins = 0
    losses = 0

    for t in trades:
        if t.side == "BUY":
            buy_queues[t.symbol].append((t.qty, t.price))
        else:  # SELL
            sell_qty = t.qty
            sell_price = t.price
            queue = buy_queues.get(t.symbol, [])
            while sell_qty > 0 and queue:
                buy_qty, buy_price = queue[0]
                matched = min(sell_qty, buy_qty)
                pnl = matched * (sell_price - buy_price)
                realized += pnl
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1

                sell_qty -= matched
                if matched >= buy_qty:
                    queue.pop(0)
                else:
                    queue[0] = (buy_qty - matched, buy_price)

    return realized, wins, losses
