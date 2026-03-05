# src/oms/order.py
"""Order entity – the canonical order data object."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from src.common.clock import SYSTEM_CLOCK
from src.common.enums import OrderSide, OrderStatus, OrderType  # canonical
from src.common.types import AccountID, Money, OrderID, Qty, Symbol

# Re-export so ``from src.oms.order import OrderStatus`` keeps working.
__all__ = ["Order", "OrderStatus", "OrderSide", "OrderType"]


@dataclass
class Order:
    """Canonical order entity – Bounded Context: only OMS may mutate fields.

    IMPORTANT: All state transitions MUST go through OrderStateMachine.
    Direct field mutation from outside the OMS module violates the
    bounded context. The mutable design is intentional for in-process
    performance; enforcement is architectural, not runtime.
    """
    order_id: OrderID
    account_id: AccountID
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    qty: Qty
    price: Money | None
    status: OrderStatus = field(default=OrderStatus.PENDING)
    created_at: datetime = field(default_factory=SYSTEM_CLOCK.now)
    filled_qty: Qty = field(default_factory=lambda: Qty(Decimal(0)))

    @property
    def remaining_qty(self) -> Qty:
        return Qty(self.qty - self.filled_qty)

    @property
    def is_open(self) -> bool:
        return self.status in {
            OrderStatus.ACCEPTED,
            OrderStatus.ROUTED,
            OrderStatus.PARTIALLY_FILLED,
        }

    @property
    def is_cancelable(self) -> bool:
        return self.status in {
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.ROUTED,
            OrderStatus.PARTIALLY_FILLED,
        }

    # ── Serialization (DSD §2.5) ─────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "order_id": str(self.order_id),
            "account_id": str(self.account_id),
            "symbol": str(self.symbol),
            "side": self.side.value,
            "order_type": self.order_type.value,
            "qty": str(self.qty),
            "price": str(self.price) if self.price is not None else None,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if hasattr(self.created_at, "isoformat") else str(self.created_at),
            "filled_qty": str(self.filled_qty),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Order:
        """Reconstruct from a dict."""
        from uuid import UUID as _UUID
        price_raw = data.get("price")
        return cls(
            order_id=OrderID(_UUID(data["order_id"]) if isinstance(data["order_id"], str) else data["order_id"]),
            account_id=AccountID(data["account_id"]),
            symbol=Symbol(data["symbol"]),
            side=OrderSide(data["side"]),
            order_type=OrderType(data["order_type"]),
            qty=Qty(Decimal(str(data["qty"]))),
            price=Money(Decimal(str(price_raw))) if price_raw is not None else None,
            status=OrderStatus(data.get("status", "PENDING")),
            filled_qty=Qty(Decimal(str(data.get("filled_qty", 0)))),
        )
