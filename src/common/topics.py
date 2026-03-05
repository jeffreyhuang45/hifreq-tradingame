# src/common/topics.py
"""
Canonical Topic / Channel constants (DSD §2.6).

Defines the **single source of truth** for event routing channels.
Every module that produces or consumes events uses these constants
so that channel names are never hard-coded as string literals.

Channel patterns (DSD §7.1):
    MARKET.{symbol}          – market data updates
    ORDERS.{account_id}      – order lifecycle events
    TRADES.{account_id}      – trade execution / settlement reports

Import hierarchy:
    common.topics  ←  api.websocket / app / tests
"""
from __future__ import annotations

from typing import Any

from src.events.base_event import BaseEvent, EventType


# ═══════════════════════════════════════════════════════════════
# Channel prefixes
# ═══════════════════════════════════════════════════════════════
CHANNEL_MARKET = "market"      # market.{symbol}
CHANNEL_ORDERS = "orders"      # orders.{account_id}
CHANNEL_TRADES = "trades"      # trades.{account_id}

# Wildcard for subscribing to all symbols
CHANNEL_MARKET_ALL = f"{CHANNEL_MARKET}.*"


# ═══════════════════════════════════════════════════════════════
# EventType → Channel prefix mapping
# ═══════════════════════════════════════════════════════════════
# Each EventType maps to a channel prefix.
# The suffix ({symbol} or {account_id}) is resolved at runtime.

EVENT_CHANNEL_MAP: dict[EventType, str] = {
    # ── Market data ──────────────────────────────────────────
    EventType.MARKET_DATA_UPDATED:  CHANNEL_MARKET,

    # ── Order lifecycle → orders.{account_id} ────────────────
    EventType.ORDER_PLACED:         CHANNEL_ORDERS,
    EventType.ORDER_ACCEPTED:       CHANNEL_ORDERS,
    EventType.ORDER_REJECTED:       CHANNEL_ORDERS,
    EventType.ORDER_CANCELED:       CHANNEL_ORDERS,
    EventType.ORDER_ROUTED:         CHANNEL_ORDERS,
    EventType.ORDER_FILLED:         CHANNEL_ORDERS,
    EventType.CANCEL_REJECTED:      CHANNEL_ORDERS,
    EventType.ORDER_REMAIN_ON_BOOK: CHANNEL_ORDERS,
    EventType.ACCOUNT_UPDATED:      CHANNEL_ORDERS,

    # ── Trade execution → trades.{account_id} ────────────────
    EventType.TRADE_EXECUTED:       CHANNEL_TRADES,
    EventType.TRADE_SETTLEMENT:     CHANNEL_TRADES,

    # ── Account deleted (admin only, no channel broadcast) ───
    EventType.ACCOUNT_DELETED:      CHANNEL_ORDERS,
}


def event_to_topics(
    event: BaseEvent,
    *,
    resolve_account_id: Any = None,
) -> list[str]:
    """Determine the canonical channel(s) an event should be published to.

    Parameters
    ----------
    event:
        The domain event.
    resolve_account_id:
        Optional callable ``(order_id) -> str`` that looks up the
        account_id for a given order_id. Required for order events
        that don't carry ``account_id`` directly (e.g. OrderAccepted).

    Returns
    -------
    list[str]
        Channel strings, e.g. ``["market.2330"]`` or
        ``["trades.user1", "trades.user2"]``.
    """
    etype = getattr(event, "event_type", None)
    if etype is None:
        return []

    prefix = EVENT_CHANNEL_MAP.get(etype)
    if prefix is None:
        return []

    # ── Market data → market.{symbol} ────────────────────────
    if prefix == CHANNEL_MARKET:
        sym = getattr(event, "symbol", "")
        return [f"{CHANNEL_MARKET}.{sym}"] if sym else []

    # ── Trade executed → trades.{buyer}, trades.{seller} ─────
    if etype == EventType.TRADE_EXECUTED:
        channels: list[str] = []
        buy_oid = getattr(event, "buy_order_id", None)
        sell_oid = getattr(event, "sell_order_id", None)
        if resolve_account_id:
            buyer = resolve_account_id(buy_oid) if buy_oid else ""
            seller = resolve_account_id(sell_oid) if sell_oid else ""
        else:
            buyer = ""
            seller = ""
        if buyer:
            channels.append(f"{CHANNEL_TRADES}.{buyer}")
        if seller and seller != buyer:
            channels.append(f"{CHANNEL_TRADES}.{seller}")
        return channels

    # ── Trade settlement → trades.{buyer}, trades.{seller} ───
    if etype == EventType.TRADE_SETTLEMENT:
        channels = []
        buyer = getattr(event, "buyer_account_id", "")
        seller = getattr(event, "seller_account_id", "")
        if buyer:
            channels.append(f"{CHANNEL_TRADES}.{buyer}")
        if seller and seller != buyer:
            channels.append(f"{CHANNEL_TRADES}.{seller}")
        return channels

    # ── Order events that carry account_id directly ──────────
    acct = getattr(event, "account_id", "")
    if acct:
        return [f"{prefix}.{acct}"]

    # ── Order events that need order_id → account_id lookup ──
    oid = getattr(event, "order_id", None)
    if oid and resolve_account_id:
        acct = resolve_account_id(oid)
        if acct:
            return [f"{prefix}.{acct}"]

    return []
