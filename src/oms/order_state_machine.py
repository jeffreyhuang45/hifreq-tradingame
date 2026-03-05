# src/oms/order_state_machine.py
"""Strict order state machine – enforces valid transitions only."""
from __future__ import annotations

from src.common.errors import InvalidOrderStateError
from src.oms.order import Order, OrderStatus

class OrderStateMachine:
    """
    Manages the state transitions of an order.
    This ensures that state transitions are valid.
    """

    TRANSITIONS = {
        OrderStatus.PENDING: {OrderStatus.ACCEPTED, OrderStatus.REJECTED},
        OrderStatus.ACCEPTED: {OrderStatus.ROUTED, OrderStatus.CANCELED},
        OrderStatus.ROUTED: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
        },
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELED: set(),
        OrderStatus.REJECTED: set(),
    }

    @classmethod
    def transition(cls, order: Order, new_status: OrderStatus):
        """
        Transitions the order to a new state if the transition is valid.
        """
        if new_status not in cls.TRANSITIONS[order.status]:
            raise InvalidOrderStateError(
                f"Cannot transition order {order.order_id} from {order.status} to {new_status}"
            )
        order.status = new_status
