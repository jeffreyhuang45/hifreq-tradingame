# src/api/rest.py
"""
FastAPI REST endpoints (API Gateway) – Module 2.

Responsibilities:
  • Auth (login, register, password)
  • Trading (place/cancel orders, portfolio) — requires auth
  • Market Data (read-only queries) — requires auth
  • Admin (user CRUD, system monitoring) — requires admin role
  • Export (Excel/CSV download)

All endpoints use async def for I/O concurrency (DSD §4.2).
Auth is enforced via dependency injection.
"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Header, Response

from src.api.schemas import (
    AdminResetPasswordRequest,
    AdminUpdateUserRequest,
    BehaviorAnalysisResponse,
    ChangePasswordRequest,
    CashTransactionResponse,
    DepositRequest,
    LoginRequest,
    LoginResponse,
    MarketDataResponse,
    OrderBookSnapshotResponse,
    OrderResponse,
    PlaceOrderRequest,
    PortfolioResponse,
    PositionItem,
    PredictionResponse,
    RegisterRequest,
    SystemStatusResponse,
    TradeStatisticsResponse,
    TradeLotResponse,
    UserResponse,
    WithdrawRequest,
)
from src.common.enums import OrderSide, OrderType
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.account_events import AccountUpdatedEvent
from src.events.order_events import OrderPlacedEvent

if TYPE_CHECKING:
    from src.app import AppContext
    from src.auth.user_model import User

router = APIRouter(prefix="/api/v1")

# Will be set by app.py during bootstrap
_ctx: "AppContext | None" = None


def set_context(ctx: "AppContext") -> None:
    global _ctx
    _ctx = ctx


def _get_ctx() -> "AppContext":
    if _ctx is None:
        raise RuntimeError("AppContext not initialized")
    return _ctx


# ── Auth dependency ──────────────────────────────────────────

async def get_current_user(authorization: Annotated[str | None, Header()] = None) -> "User":
    """Extract and validate JWT token from Authorization header."""
    ctx = _get_ctx()
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    # Accept "Bearer <token>" or plain token
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user = ctx.auth_service.validate_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


async def require_admin(user: "User" = Depends(get_current_user)) -> "User":
    """Require the authenticated user to have admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ═══════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.post("/auth/login")
async def login(req: LoginRequest) -> LoginResponse:
    ctx = _get_ctx()
    try:
        token = ctx.auth_service.login(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = ctx.auth_service.validate_token(token)
    # Ensure a trading account exists for this user
    ctx.account_service.get_or_create(user.user_id)
    return LoginResponse(
        token=token, user_id=user.user_id,
        username=user.username, role=user.role,
    )


@router.put("/auth/password")
async def change_password(
    req: ChangePasswordRequest,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    try:
        ctx.auth_service.change_password(user.user_id, req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Password changed successfully"}


# ═══════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS (requires admin role)
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/users")
async def list_users(admin: "User" = Depends(require_admin)) -> list[UserResponse]:
    ctx = _get_ctx()
    users = ctx.user_repo.list_all()
    return [
        UserResponse(
            user_id=u.user_id, username=u.username,
            role=u.role, display_name=u.display_name,
            created_at=u.created_at, status=getattr(u, 'status', 'active'),
        )
        for u in users
    ]


@router.post("/admin/users", status_code=201)
async def create_user(
    req: RegisterRequest,
    admin: "User" = Depends(require_admin),
) -> UserResponse:
    ctx = _get_ctx()
    try:
        user = ctx.auth_service.register(
            req.username, req.password,
            role=req.role, display_name=req.display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Create trading account
    ctx.account_service.get_or_create(user.user_id)
    return UserResponse(
        user_id=user.user_id, username=user.username,
        role=user.role, display_name=user.display_name,
        created_at=user.created_at, status=getattr(user, 'status', 'active'),
    )


@router.put("/admin/users/{user_id}")
async def update_user(
    user_id: str,
    req: AdminUpdateUserRequest,
    admin: "User" = Depends(require_admin),
) -> UserResponse:
    ctx = _get_ctx()
    try:
        kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
        user = ctx.auth_service.admin_update_user(user_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return UserResponse(
        user_id=user.user_id, username=user.username,
        role=user.role, display_name=user.display_name,
        created_at=user.created_at, status=getattr(user, 'status', 'active'),
    )


@router.put("/admin/users/{user_id}/password")
async def admin_reset_password(
    user_id: str,
    req: AdminResetPasswordRequest,
    admin: "User" = Depends(require_admin),
) -> dict:
    ctx = _get_ctx()
    try:
        ctx.auth_service.admin_reset_password(user_id, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    reset_type = "to default" if req.new_password is None else "to specified"
    return {"message": f"Password reset successfully ({reset_type})"}


@router.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: str,
    admin: "User" = Depends(require_admin),
) -> dict:
    ctx = _get_ctx()
    if not ctx.auth_service.admin_delete_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")

    # Emit event BEFORE deletion so cold-start replay can reproduce (DSD §1.6 #4)
    from src.events.account_events import AccountDeletedEvent
    ctx.event_writer.push(AccountDeletedEvent(account_id=user_id))

    ctx.account_service.delete_account(user_id)   # G-4: cleanup orphaned Account
    return {"message": "User deleted"}


@router.get("/admin/system")
async def system_status(admin: "User" = Depends(require_admin)) -> SystemStatusResponse:
    ctx = _get_ctx()
    from src.api.websocket import _connections
    orders = ctx.order_repo.list_all()
    active = [o for o in orders if o.is_open]

    # ME book depths (via public API)
    buy_depth, sell_depth = ctx.matcher.get_book_depths()

    cb_state = "CLOSED"
    if ctx.market_data_service:
        cb_state = ctx.market_data_service.circuit_breaker.state.value

    return SystemStatusResponse(
        active_orders=len(active),
        total_orders=len(orders),
        buy_book_depth=buy_depth,
        sell_book_depth=sell_depth,
        circuit_breaker_state=cb_state,
        connected_ws_clients=len(_connections),
    )


@router.get("/admin/metrics")
async def system_metrics(admin: "User" = Depends(require_admin)) -> dict:
    """
    Combined system metrics: WS latency, CPU/memory, OMS counters (M-4).
    """
    import os
    import time as _time
    from src.api.websocket import get_ws_metrics

    ctx = _get_ctx()
    ws = get_ws_metrics()

    # CPU: simple load average (Windows-compatible via os.cpu_count)
    try:
        import platform
        if platform.system() != "Windows":
            load1, load5, load15 = os.getloadavg()
            cpu_info = {"load_1m": load1, "load_5m": load5, "load_15m": load15}
        else:
            cpu_info = {"cpu_count": os.cpu_count()}
    except Exception:
        cpu_info = {"cpu_count": os.cpu_count()}

    # OMS counters
    accepted = ctx.oms.accepted_count
    rejected = ctx.oms.rejected_count
    total_orders = accepted + rejected
    success_rate = (accepted / total_orders * 100) if total_orders > 0 else 100.0

    return {
        "ws_metrics": ws,
        "cpu": cpu_info,
        "oms": {
            "accepted_count": accepted,
            "rejected_count": rejected,
            "trade_count": ctx.oms.trade_count,
            "success_rate_pct": round(success_rate, 2),
        },
        "uptime_info": {
            "active_orders": len([o for o in ctx.order_repo.list_all() if o.is_open]),
            "total_accounts": len(ctx.account_service.list_all()),
        },
    }


@router.get("/admin/logs")
async def system_logs(
    admin: "User" = Depends(require_admin),
    level: str | None = None,
    module: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Structured system log query (M-5)."""
    from src.common.operation_log import system_log
    return system_log.query(level=level, module=module, limit=limit)


@router.get("/operations/log")
async def operation_logs(
    user: "User" = Depends(get_current_user),
    limit: int = 100,
) -> list[dict]:
    """Client operation log (C-4). Users see own; admin sees all."""
    from src.common.operation_log import operation_log
    uid = None if user.role == "admin" else user.user_id
    return operation_log.query(user_id=uid, limit=limit)


# ── Engine Mode Settings ─────────────────────────────────────

@router.get("/settings/engine-mode")
async def get_engine_mode(user: "User" = Depends(get_current_user)) -> dict:
    ctx = _get_ctx()
    return {"engine_mode": ctx.engine_mode}


@router.put("/settings/engine-mode")
async def set_engine_mode(
    body: dict,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    mode = body.get("engine_mode", "").upper()
    if mode not in ("A", "B"):
        raise HTTPException(status_code=400, detail="engine_mode must be 'A' or 'B'")
    ctx.engine_mode = mode
    return {"engine_mode": ctx.engine_mode}


# ═══════════════════════════════════════════════════════════════
# TRADING ENDPOINTS (CQRS — write path, requires auth)
# ═══════════════════════════════════════════════════════════════

@router.post("/orders", status_code=202)
async def place_order(
    req: PlaceOrderRequest,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    order_id = OrderID(uuid.uuid4())
    correlation_id = uuid.uuid4()

    # Circuit Breaker: reject MARKET orders when MD service is unavailable (DSD §4.4)
    if req.order_type == "MARKET" and ctx.market_data_service and ctx.market_data_service.circuit_breaker.is_open:
        raise HTTPException(
            status_code=503,
            detail="Market data service unavailable – MARKET orders rejected. Use LIMIT orders.",
        )

    event = OrderPlacedEvent(
        order_id=order_id,
        account_id=user.user_id,       # use authenticated user's account
        symbol=Symbol(req.symbol),
        side=OrderSide(req.side),
        order_type=OrderType(req.order_type),
        qty=Qty(req.qty),
        price=Money(req.price) if req.price is not None else None,
        correlation_id=correlation_id,
    )

    ctx.event_writer.push(event)          # G-1 fix: persist so replay can restore locks
    ctx.oms.place_order(event)
    ctx.process_oms_events()

    return {"order_id": str(order_id), "status": "ACCEPTED", "correlation_id": str(correlation_id)}


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    try:
        order = ctx.order_repo.get(OrderID(order_id))
        # Only allow canceling own orders (admin can cancel any)
        if order.account_id != user.user_id and user.role != "admin":
            raise HTTPException(status_code=403, detail="Cannot cancel another user's order")
        canceled = ctx.oms.cancel_order(OrderID(order_id))
        ctx.process_oms_events()
        if not canceled:
            raise HTTPException(
                status_code=409,
                detail=f"Order {order_id} in {order.status.value} state cannot be canceled",
            )
        return {"order_id": str(order_id), "status": "CANCELED"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ═══════════════════════════════════════════════════════════════
# QUERY ENDPOINTS (CQRS — read path, requires auth)
# ═══════════════════════════════════════════════════════════════

@router.get("/orderbook/{symbol}")
async def get_order_book(
    symbol: str,
    user: "User" = Depends(get_current_user),
) -> OrderBookSnapshotResponse:
    """Standardised order book query interface (DSD §15 bounded context)."""
    ctx = _get_ctx()
    snapshot = ctx.matcher.get_book_snapshot(Symbol(symbol))
    return OrderBookSnapshotResponse(**snapshot)


@router.get("/orders")
async def list_orders(user: "User" = Depends(get_current_user)) -> list[OrderResponse]:
    ctx = _get_ctx()
    orders = ctx.order_repo.list_all()
    # Regular users see only their own; admin sees all
    if user.role != "admin":
        orders = [o for o in orders if o.account_id == user.user_id]
    return [
        OrderResponse(
            order_id=str(o.order_id),
            created_at=o.created_at.isoformat()
            if hasattr(o.created_at, "isoformat")
            else str(o.created_at),
            status=o.status.value,
            symbol=o.symbol,
            side=o.side.value,
            order_type=o.order_type.value,
            qty=str(o.qty),
            price=str(o.price) if o.price else None,
            filled_qty=str(o.filled_qty),
        )
        for o in orders
    ]


@router.get("/portfolio")
async def get_portfolio(user: "User" = Depends(get_current_user)) -> PortfolioResponse:
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)

    total_cash = acct.cash_available + acct.cash_locked
    total_market_value = Decimal(0)
    position_items: list[PositionItem] = []

    for sym, pos in acct.positions.items():
        total_qty = pos.qty_available + pos.qty_locked
        if total_qty <= 0:
            continue
        # Look up current market price
        md = ctx.market_data_cache.get(sym)
        current_price = md.last_trade_price if md and md.last_trade_price else Decimal(0)
        market_value = current_price * total_qty
        total_market_value += market_value

        unrealized_pnl = market_value - pos.total_cost
        pnl_pct = (unrealized_pnl / pos.total_cost * 100) if pos.total_cost > 0 else Decimal(0)

        position_items.append(PositionItem(
            symbol=str(sym),
            qty_available=str(pos.qty_available),
            qty_locked=str(pos.qty_locked),
            avg_cost=str(pos.avg_cost),
            market_value=str(market_value.quantize(Decimal("0.01"))),
            unrealized_pnl=str(unrealized_pnl.quantize(Decimal("0.01"))),
            unrealized_pnl_pct=str(pnl_pct.quantize(Decimal("0.01"))),
        ))

    total_assets = total_cash + total_market_value
    total_deposits = sum(
        tx.amount for tx in acct.cash_transactions if tx.tx_type == "DEPOSIT"
    )
    total_pnl = total_assets - total_deposits if total_deposits > 0 else Decimal(0)
    total_pnl_pct = (total_pnl / total_deposits * 100) if total_deposits > 0 else Decimal(0)

    return PortfolioResponse(
        account_id=acct.account_id,
        cash_available=str(acct.cash_available),
        cash_locked=str(acct.cash_locked),
        positions=position_items,
        total_market_value=str(total_market_value.quantize(Decimal("0.01"))),
        total_assets=str(total_assets.quantize(Decimal("0.01"))),
        total_pnl=str(total_pnl.quantize(Decimal("0.01"))),
        total_pnl_pct=str(total_pnl_pct.quantize(Decimal("0.01"))),
    )


@router.get("/trades")
async def list_trades(user: "User" = Depends(get_current_user)) -> list[TradeLotResponse]:
    """Trade history for the authenticated user."""
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    return [
        TradeLotResponse(
            trade_id=t.trade_id,
            order_id=t.order_id,
            symbol=t.symbol,
            side=t.side,
            qty=str(t.qty),
            price=str(t.price),
            trade_date=t.trade_date,
        )
        for t in acct.trade_history
    ]


@router.get("/cashflow")
async def cash_flow(user: "User" = Depends(get_current_user)) -> list[CashTransactionResponse]:
    """Cash transaction log for the authenticated user."""
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    return [
        CashTransactionResponse(
            timestamp=tx.timestamp,
            tx_type=tx.tx_type,
            amount=str(tx.amount),
            description=tx.description,
        )
        for tx in acct.cash_transactions
    ]


@router.get("/trades/statistics")
async def trade_statistics(user: "User" = Depends(get_current_user)) -> TradeStatisticsResponse:
    """Trade statistics for the authenticated user (M-3)."""
    from src.account.trading_analysis import compute_trade_statistics
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    stats = compute_trade_statistics(acct, ctx.market_data_cache)
    return TradeStatisticsResponse(**stats)


@router.get("/analysis/behavior")
async def behavior_analysis(user: "User" = Depends(get_current_user)) -> BehaviorAnalysisResponse:
    """Trading behavior analysis (C-5)."""
    from src.account.trading_analysis import compute_behavior_analysis
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    result = compute_behavior_analysis(acct)
    return BehaviorAnalysisResponse(**result)


@router.get("/analysis/predictions")
async def investment_predictions(user: "User" = Depends(get_current_user)) -> list[PredictionResponse]:
    """Simple investment prediction / momentum ranking (M-6)."""
    from src.account.trading_analysis import compute_predictions
    ctx = _get_ctx()
    raw = compute_predictions(ctx.market_data_cache, ctx.quote_history)
    return [PredictionResponse(**p) for p in raw]


@router.post("/accounts/deposit")
async def deposit(
    req: DepositRequest,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)

    # Event-first: emit event BEFORE mutation (DSD §1.6 #8)
    evt = AccountUpdatedEvent(
        account_id=user.user_id,
        cash_delta=Money(req.amount),
    )
    ctx.event_writer.push(evt)

    # Then apply the state change
    acct.deposit(Money(req.amount))

    from src.api.websocket import broadcast_event_sync
    broadcast_event_sync(evt)

    # Queue per-user funding record write (DSD §1.6 #5, off critical path)
    if ctx.user_record_writer:
        ctx.user_record_writer.queue_funding_record(
            user.user_id,
            [{"timestamp": tx.timestamp, "tx_type": tx.tx_type,
              "amount": str(tx.amount), "description": tx.description}
             for tx in acct.cash_transactions],
        )

    return {"account_id": acct.account_id, "cash_available": str(acct.cash_available)}


@router.post("/accounts/withdraw")
async def withdraw(
    req: WithdrawRequest,
    user: "User" = Depends(get_current_user),
) -> dict:
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)

    # Pre-validate before emitting event (fail fast)
    if acct.cash_available < Money(req.amount):
        from src.common.errors import InsufficientCashError
        raise HTTPException(status_code=400, detail="Insufficient cash for withdrawal")

    # Event-first: emit event BEFORE mutation (DSD §1.6 #8)
    evt = AccountUpdatedEvent(
        account_id=user.user_id,
        cash_delta=Money(-req.amount),
    )
    ctx.event_writer.push(evt)

    # Then apply the state change
    acct.withdraw(Money(req.amount))

    from src.api.websocket import broadcast_event_sync
    broadcast_event_sync(evt)

    # Queue per-user funding record write (DSD §1.6 #5, off critical path)
    if ctx.user_record_writer:
        ctx.user_record_writer.queue_funding_record(
            user.user_id,
            [{"timestamp": tx.timestamp, "tx_type": tx.tx_type,
              "amount": str(tx.amount), "description": tx.description}
             for tx in acct.cash_transactions],
        )

    return {"account_id": acct.account_id, "cash_available": str(acct.cash_available)}


# ═══════════════════════════════════════════════════════════════
# MARKET DATA ENDPOINTS (read-only, requires auth)
# ═══════════════════════════════════════════════════════════════

@router.get("/market-data")
async def list_market_data(user: "User" = Depends(get_current_user)) -> list[MarketDataResponse]:
    """All cached market data."""
    ctx = _get_ctx()
    results = []
    # Merge cached event data with rich StockQuote fields
    last_quotes = ctx.market_data_service.get_all_last_quotes() if ctx.market_data_service else {}
    for sym, md in ctx.market_data_cache.items():
        sq = last_quotes.get(str(sym))
        results.append(MarketDataResponse(
            symbol=str(sym),
            name=sq.name if sq else "",
            bid_price=str(md.bid_price),
            ask_price=str(md.ask_price),
            last_trade_price=str(md.last_trade_price) if md.last_trade_price else None,
            open_price=str(sq.open_price) if sq else "0",
            high_price=str(sq.high_price) if sq else "0",
            low_price=str(sq.low_price) if sq else "0",
            close_price=str(sq.close_price) if sq else "0",
            volume=str(md.volume),
            change=str(sq.change) if sq else "0",
            change_pct=str(sq.change_pct) if sq else "0",
        ))
    return results


@router.get("/market-data/symbols")
async def list_symbols(user: "User" = Depends(get_current_user)) -> list[dict]:
    """Available symbols from market data adapter."""
    ctx = _get_ctx()
    if ctx.market_data_service:
        return ctx.market_data_service.available_symbols()
    return []


@router.get("/market-data/{symbol}")
async def get_market_data(symbol: str, user: "User" = Depends(get_current_user)) -> MarketDataResponse:
    ctx = _get_ctx()
    md = ctx.market_data_cache.get(Symbol(symbol))
    if md is None:
        raise HTTPException(status_code=404, detail=f"No market data for {symbol}")
    sq = ctx.market_data_service.get_last_quote(symbol) if ctx.market_data_service else None
    return MarketDataResponse(
        symbol=symbol,
        name=sq.name if sq else "",
        bid_price=str(md.bid_price),
        ask_price=str(md.ask_price),
        last_trade_price=str(md.last_trade_price) if md.last_trade_price else None,
        open_price=str(sq.open_price) if sq else "0",
        high_price=str(sq.high_price) if sq else "0",
        low_price=str(sq.low_price) if sq else "0",
        close_price=str(sq.close_price) if sq else "0",
        volume=str(md.volume),
        change=str(sq.change) if sq else "0",
        change_pct=str(sq.change_pct) if sq else "0",
    )


@router.post("/market-data/watch/{symbol}")
async def watch_symbol(symbol: str, user: "User" = Depends(get_current_user)) -> dict:
    """Add a symbol to the market data watchlist."""
    ctx = _get_ctx()
    if ctx.market_data_service:
        ctx.market_data_service.add_to_watchlist(symbol)
    return {"symbol": symbol, "watching": True}


@router.delete("/market-data/watch/{symbol}")
async def unwatch_symbol(symbol: str, user: "User" = Depends(get_current_user)) -> dict:
    """Remove a symbol from the market data watchlist."""
    ctx = _get_ctx()
    if ctx.market_data_service:
        ctx.market_data_service.remove_from_watchlist(symbol)
    return {"symbol": symbol, "watching": False}


@router.get("/market-data/{symbol}/history")
async def get_market_data_history(
    symbol: str,
    limit: int = 60,
    user: "User" = Depends(get_current_user),
) -> dict:
    """OHLCV history ticks for a symbol (for charts)."""
    ctx = _get_ctx()
    history = ctx.quote_history.get(symbol, [])
    return {"symbol": symbol, "ticks": history[-limit:]}


@router.get("/market-data/{symbol}/kline")
async def get_kline(
    symbol: str,
    date: str = "",
    user: "User" = Depends(get_current_user),
) -> dict:
    """Monthly daily K-line (OHLCV) from TWSE STOCK_DAY or simulated data.

    Query params:
      date: YYYYMMDD (any day in the target month). Defaults to current month.
    """
    ctx = _get_ctx()
    if not ctx.market_data_service:
        raise HTTPException(status_code=503, detail="Market data service unavailable")
    bars = await ctx.market_data_service.fetch_history(symbol, date)
    return {"symbol": symbol, "date": date or "current", "bars": bars}


# ═══════════════════════════════════════════════════════════════
# EXPORT ENDPOINTS (requires auth)
# ═══════════════════════════════════════════════════════════════

@router.get("/export/trades")
async def export_trades(
    format: str = "csv",
    user: "User" = Depends(get_current_user),
) -> Response:
    """Export trade history as CSV or Excel."""
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    trade_dicts = [
        {"trade_id": t.trade_id, "symbol": t.symbol, "price": str(t.price),
         "qty": str(t.qty), "buy_order_id": "", "sell_order_id": "",
         "matched_at": t.trade_date}
        for t in acct.trade_history
    ]

    if format == "excel":
        from src.storage.excel_writer import ExcelExporter
        data = ExcelExporter.export_trades(trade_dicts)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=trades.xlsx"},
        )
    else:
        from src.storage.excel_writer import CSVExporter
        data = CSVExporter.export_trades(trade_dicts)
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades.csv"},
        )


@router.get("/export/portfolio")
async def export_portfolio(
    format: str = "csv",
    user: "User" = Depends(get_current_user),
) -> Response:
    """Export portfolio as CSV or Excel."""
    ctx = _get_ctx()
    acct = ctx.account_service.get_or_create(user.user_id)
    acct_dict = {
        "account_id": acct.account_id,
        "cash_available": str(acct.cash_available),
        "cash_locked": str(acct.cash_locked),
        "positions": [
            {"symbol": sym, "qty_available": str(p.qty_available), "qty_locked": str(p.qty_locked)}
            for sym, p in acct.positions.items()
        ],
    }

    if format == "excel":
        from src.storage.excel_writer import ExcelExporter
        data = ExcelExporter.export_portfolio(acct_dict)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=portfolio.xlsx"},
        )
    else:
        from src.storage.excel_writer import CSVExporter
        data = CSVExporter.export_portfolio(acct_dict)
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=portfolio.csv"},
        )


@router.get("/export/orders")
async def export_orders(
    format: str = "csv",
    user: "User" = Depends(get_current_user),
) -> Response:
    """Export orders as CSV or Excel."""
    ctx = _get_ctx()
    orders = ctx.order_repo.list_all()
    if user.role != "admin":
        orders = [o for o in orders if o.account_id == user.user_id]
    order_dicts = [
        {
            "order_id": str(o.order_id), "account_id": o.account_id,
            "symbol": o.symbol, "side": o.side.value,
            "order_type": o.order_type.value, "qty": str(o.qty),
            "price": str(o.price) if o.price else "",
            "status": o.status.value, "filled_qty": str(o.filled_qty),
        }
        for o in orders
    ]

    if format == "excel":
        from src.storage.excel_writer import ExcelExporter
        data = ExcelExporter.export_orders(order_dicts)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=orders.xlsx"},
        )
    else:
        from src.storage.excel_writer import CSVExporter
        data = CSVExporter.export_orders(order_dicts)
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=orders.csv"},
        )


# ═══════════════════════════════════════════════════════════════
# LEADERBOARD ENDPOINT (requires auth)
# ═══════════════════════════════════════════════════════════════

@router.get("/leaderboard")
async def leaderboard(user: "User" = Depends(get_current_user)) -> dict:
    """Ranking of all traders by total return and trade volume."""
    ctx = _get_ctx()
    users = ctx.user_repo.list_all()
    rankings = ctx.account_service.compute_leaderboard(users, ctx.market_data_cache)
    return {"rankings": rankings}
