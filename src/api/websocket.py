# src/api/websocket.py
"""
WebSocket push – broadcasts events to connected clients.

DSD §7.1 channels:
  orders.{account_id}   – order status updates
  trades.{account_id}   – trade execution reports
  market.{symbol}        – market data updates

Security:
  • JWT authentication — token validated before connection is accepted.
  • Channel-based subscription — events are ONLY sent to authorised clients.
  • Order/Trade events resolve account_id via order_repo to prevent leaking
    another user's trading activity.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.events.base_event import BaseEvent, EventType, _json_default
from src.common.topics import (
    CHANNEL_MARKET,
    CHANNEL_ORDERS,
    CHANNEL_TRADES,
    event_to_topics,
)

if TYPE_CHECKING:
    from src.oms.order_repository import OrderRepository

logger = logging.getLogger(__name__)

ws_router = APIRouter()

# ── Order repo reference (set by app.py during bootstrap) ────
_order_repo: "OrderRepository | None" = None


def set_order_repo(repo: "OrderRepository") -> None:
    """Injected by app.py so channel routing can resolve order_id → account_id."""
    global _order_repo
    _order_repo = repo


def _resolve_account_id(order_id: Any) -> str:
    """Look up account_id for a given order_id via order_repo. Returns '' on failure."""
    if _order_repo is None:
        return ""
    try:
        order = _order_repo.get(order_id)
        return order.account_id
    except Exception:
        return ""


# ── Per-connection state ─────────────────────────────────────

@dataclass(eq=False)
class WSClient:
    """Wraps a WebSocket connection with user identity and subscribed channels.

    ``eq=False`` preserves default ``object.__hash__`` so instances can
    be stored in a ``set``  (DSD §1.6 #3 fix: unhashable WSClient).
    """
    ws: WebSocket
    user_id: str = ""
    subscriptions: set[str] = field(default_factory=set)  # e.g. {"market.2330", "orders.user1"}


# Global set of active clients
_clients: Set[WSClient] = set()

# ── Latency metrics ──────────────────────────────────────────
_latency_samples: deque[float] = deque(maxlen=200)  # last 200 broadcast latencies (ms)


def get_ws_metrics() -> dict[str, Any]:
    """Return WebSocket metrics for admin monitoring."""
    samples = list(_latency_samples)
    return {
        "connected_clients": len(_clients),
        "latency_samples": len(samples),
        "avg_latency_ms": round(sum(samples) / len(samples), 3) if samples else 0.0,
        "max_latency_ms": round(max(samples), 3) if samples else 0.0,
        "min_latency_ms": round(min(samples), 3) if samples else 0.0,
    }


# ── Backward compat: expose connection count ─────────────────

class _ConnectionsCompat:
    """len()-compatible proxy so ``len(_connections)`` still works in rest.py."""
    def __len__(self) -> int:
        return len(_clients)


_connections = _ConnectionsCompat()


# ── Channel helpers ──────────────────────────────────────────

def _event_channels(event: BaseEvent) -> list[str]:
    """Determine which channels an event belongs to.

    Delegates to the canonical ``event_to_topics()`` from
    ``src.common.topics`` (DSD §2.6), passing in the order-repo
    resolver for account_id lookup.

    Returns a list of channel strings like:
      - ``market.2330``
      - ``orders.<user_id>``
      - ``trades.<user_id>``  (one per participant)
    """
    return event_to_topics(event, resolve_account_id=_resolve_account_id)


def _client_should_receive(client: WSClient, event: BaseEvent, channels: list[str]) -> bool:
    """Decide whether a client should receive this event.

    Security rules:
      - Order events (orders.{account_id}): client receives ONLY if
        the event's account_id matches the client's own user_id.
      - Trade events (trades.{account_id}): client receives ONLY if
        they are the buyer or seller in the trade.
      - Market data (market.{symbol}): client receives if subscribed
        to the specific symbol or to ``market.*``.
      - If no channels resolved (unknown event type): drop the event.
    """
    if not channels:
        # Cannot determine target → do NOT broadcast (fail-safe)
        return False

    for ch in channels:
        prefix, _, suffix = ch.partition(".")

        # ── Security-critical: order & trade channels ────────
        # Only deliver to the owner, regardless of wildcard subscriptions.
        if prefix in (CHANNEL_ORDERS, CHANNEL_TRADES):
            if suffix == client.user_id:
                return True
            # Do NOT allow wildcard to bypass ownership check
            continue

        # ── Market data: check subscription match ────────────
        if ch in client.subscriptions:
            return True
        if f"{prefix}.*" in client.subscriptions:
            return True

    return False


@ws_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
) -> None:
    """
    WebSocket endpoint with JWT authentication.
    Connect via: ws://host/ws?token=<jwt>

    After connection, send JSON to subscribe to market data channels:
        {"action": "subscribe", "channels": ["market.2330", "market.*"]}
        {"action": "unsubscribe", "channels": ["market.2330"]}

    Order and trade channels are auto-assigned based on authenticated
    user_id and cannot be overridden by the client (security enforcement).
    """
    # ── Authenticate before accept ───────────────────────────
    user_id = ""
    if token:
        try:
            from src.api.rest import _get_ctx
            ctx = _get_ctx()
            user = ctx.auth_service.validate_token(token)
            user_id = user.user_id
        except Exception:
            await websocket.close(code=4001, reason="Invalid or expired token")
            return
    else:
        # Reject unauthenticated connections
        await websocket.close(code=4001, reason="Missing token query parameter")
        return

    await websocket.accept()
    client = WSClient(ws=websocket, user_id=user_id)

    # Order/trade channels are enforced by ownership check in
    # _client_should_receive — no explicit subscription needed.
    # Market data channels require explicit subscribe message from client
    # (DSD §1.6 #3: no broadcast-all, channel-based subscription).

    _clients.add(client)
    logger.info("WS client connected: user=%s subs=%s", user_id, client.subscriptions)

    try:
        while True:
            data = await websocket.receive_text()
            # ── Handle subscribe/unsubscribe commands ─────────
            try:
                msg = json.loads(data)
                action = msg.get("action", "")

                if action == "subscribe":
                    chs = msg.get("channels", [])
                    # Only allow market.* subscriptions from client.
                    # orders.* / trades.* are server-controlled.
                    safe_chs = [c for c in chs if c.startswith(f"{CHANNEL_MARKET}.")]
                    client.subscriptions.update(safe_chs)
                    await websocket.send_text(json.dumps({
                        "type": "subscribed",
                        "channels": sorted(client.subscriptions),
                    }))
                    continue

                if action == "unsubscribe":
                    chs = msg.get("channels", [])
                    safe_chs = {c for c in chs if c.startswith(f"{CHANNEL_MARKET}.")}
                    client.subscriptions -= safe_chs
                    await websocket.send_text(json.dumps({
                        "type": "unsubscribed",
                        "channels": sorted(client.subscriptions),
                    }))
                    continue

            except (json.JSONDecodeError, AttributeError):
                pass

            # Echo back for heartbeat / debugging
            await websocket.send_text(f"ack:{data}")
    except WebSocketDisconnect:
        _clients.discard(client)
    except Exception:
        _clients.discard(client)


async def broadcast_event(event: BaseEvent) -> None:
    """Send an event ONLY to authorised WebSocket clients, with latency measurement."""
    if not _clients:
        return

    t0 = time.monotonic()
    channels = _event_channels(event)
    payload = json.dumps(event.to_dict(), default=_json_default, ensure_ascii=False)

    dead: list[WSClient] = []
    for client in _clients:
        if not _client_should_receive(client, event, channels):
            continue
        try:
            await client.ws.send_text(payload)
        except Exception:
            dead.append(client)

    for c in dead:
        _clients.discard(c)

    # Record latency
    elapsed_ms = (time.monotonic() - t0) * 1000
    _latency_samples.append(elapsed_ms)
    if elapsed_ms > 100:
        logger.warning("WS broadcast latency %.1fms (event=%s)", elapsed_ms, event.event_type.value)


def broadcast_event_sync(event: BaseEvent) -> None:
    """
    Fire-and-forget broadcast from synchronous code.
    Safe to call from the OMS / ME event processing path.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_event(event))
    except RuntimeError:
        # No event loop running – skip (e.g. during tests)
        pass
