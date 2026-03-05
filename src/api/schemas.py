# src/api/schemas.py
"""
Pydantic request / response schemas for the REST API.

Covers:
  • Auth (login, register, password change)
  • Orders (place, cancel, list)
  • Portfolio & holdings
  • Market data
  • Admin (user CRUD, system status)
  • Export
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Auth ─────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    user_id: str
    username: str
    role: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "user"

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class AdminResetPasswordRequest(BaseModel):
    new_password: str | None = None  # None → reset to default password

class AdminUpdateUserRequest(BaseModel):
    username: str | None = None
    display_name: str | None = None
    role: str | None = None
    status: str | None = None  # active / disabled / frozen

class UserResponse(BaseModel):
    user_id: str
    username: str
    role: str
    display_name: str
    created_at: str
    status: str = "active"


# ── Orders ───────────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(BUY|SELL)$")
    order_type: str = Field(alias="type", pattern="^(LIMIT|MARKET)$")
    qty: Decimal = Field(gt=0)
    price: Optional[Decimal] = None

    model_config = {"populate_by_name": True}


class CancelOrderRequest(BaseModel):
    order_id: UUID


class OrderResponse(BaseModel):
    order_id: str
    created_at: str
    status: str
    symbol: str
    side: str
    order_type: str
    qty: str
    price: Optional[str] = None
    filled_qty: str = "0"


# ── Portfolio ────────────────────────────────────────────────

class PositionItem(BaseModel):
    symbol: str
    qty_available: str
    qty_locked: str
    avg_cost: str = "0"
    market_value: str = "0"
    unrealized_pnl: str = "0"
    unrealized_pnl_pct: str = "0"

class PortfolioResponse(BaseModel):
    account_id: str
    cash_available: str
    cash_locked: str
    positions: list[PositionItem] = []
    total_market_value: str = "0"
    total_assets: str = "0"
    total_pnl: str = "0"
    total_pnl_pct: str = "0"

PortfolioResponse.model_rebuild()


# ── Trade History ────────────────────────────────────────────

class TradeLotResponse(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    side: str
    qty: str
    price: str
    trade_date: str


# ── Cash Flow ────────────────────────────────────────────────

class CashTransactionResponse(BaseModel):
    timestamp: str
    tx_type: str
    amount: str
    description: str


# ── Market Data ──────────────────────────────────────────────

class MarketDataResponse(BaseModel):
    symbol: str
    name: str = ""
    bid_price: str
    ask_price: str
    last_trade_price: Optional[str] = None
    open_price: str = "0"
    high_price: str = "0"
    low_price: str = "0"
    close_price: str = "0"
    volume: str = "0"
    change: str = "0"
    change_pct: str = "0"


# ── Admin ────────────────────────────────────────────────────

class SystemStatusResponse(BaseModel):
    active_orders: int
    total_orders: int
    buy_book_depth: dict[str, int] = {}
    sell_book_depth: dict[str, int] = {}
    circuit_breaker_state: str = "CLOSED"
    connected_ws_clients: int = 0


class OrderBookLevelResponse(BaseModel):
    price: str
    qty: str
    order_count: int


class OrderBookSnapshotResponse(BaseModel):
    symbol: str
    bids: list[OrderBookLevelResponse] = []
    asks: list[OrderBookLevelResponse] = []


class DepositRequest(BaseModel):
    amount: Decimal = Field(gt=0)


class WithdrawRequest(BaseModel):
    amount: Decimal = Field(gt=0)


# ── Trade Statistics ─────────────────────────────────────────

class TradeStatisticsResponse(BaseModel):
    total_trades: int = 0
    total_volume: str = "0"
    total_buy_trades: int = 0
    total_sell_trades: int = 0
    avg_trade_size: str = "0"
    most_traded_symbols: list[dict] = []
    win_rate: str = "0"
    realized_pnl: str = "0"


# ── Operation Log ────────────────────────────────────────────

class OperationLogEntry(BaseModel):
    timestamp: str
    user_id: str
    operation: str
    path: str
    status_code: int
    detail: str = ""


# ── Trading Analysis ─────────────────────────────────────────

class BehaviorAnalysisResponse(BaseModel):
    buy_sell_ratio: str = "0"
    avg_trade_size: str = "0"
    trade_frequency: str = "0"
    most_traded_symbols: list[dict] = []
    position_concentration: list[dict] = []
    holding_period_avg: str = "N/A"
    win_loss_ratio: str = "0"

class PredictionResponse(BaseModel):
    symbol: str
    momentum_score: str = "0"
    avg_return: str = "0"
    volatility: str = "0"
    recommendation: str = "HOLD"
