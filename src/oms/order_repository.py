# src/oms/order_repository.py
"""In-memory order repository – the sole in-memory store for orders."""
from __future__ import annotations

from src.common.errors import OrderNotFoundError
from src.common.types import OrderID
from src.oms.order import Order

class OrderRepository:
    """
    In-memory repository for orders.
    """

    def __init__(self):
        self._orders: dict[OrderID, Order] = {}

    def add(self, order: Order):
        self._orders[order.order_id] = order

    def get(self, order_id: OrderID) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderNotFoundError(f"Order with id {order_id} not found.")

    def list_all(self) -> list[Order]:
        return list(self._orders.values())
