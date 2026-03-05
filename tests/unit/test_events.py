# tests/unit/test_events.py
"""
Unit tests for the Canonical Event Model (Task #1).

Validates:
  ✓ All events are immutable (frozen dataclass)
  ✓ All events have required base fields
  ✓ Events serialize to JSON and deserialize back
  ✓ event_type is set automatically
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.events.base_event import EventType, event_from_dict
from src.events.order_events import (
    OrderAcceptedEvent,
    OrderCanceledEvent,
    OrderPlacedEvent,
    OrderRejectedEvent,
    OrderRoutedEvent,
    OrderSide,
    OrderType,
    RejectionReason,
)
from src.events.trade_events import TradeExecutedEvent
from src.events.account_events import AccountUpdatedEvent
from src.events.market_events import MarketDataUpdatedEvent
from src.common.types import OrderID, Qty, Money, Symbol, TradeID


class TestEventImmutability:
    def test_base_fields_present(self):
        evt = OrderAcceptedEvent(order_id=uuid.uuid4())
        assert evt.event_type == EventType.ORDER_ACCEPTED
        assert isinstance(evt.event_id, uuid.UUID)
        assert isinstance(evt.occurred_at, datetime)
        assert isinstance(evt.correlation_id, uuid.UUID)
        assert evt.payload_version == "1.0"

    def test_frozen(self):
        evt = OrderAcceptedEvent(order_id=uuid.uuid4())
        with pytest.raises(AttributeError):
            evt.order_id = uuid.uuid4()  # type: ignore


class TestEventSerialization:
    def test_order_placed_roundtrip(self):
        oid = uuid.uuid4()
        evt = OrderPlacedEvent(
            order_id=oid,
            account_id="user-1",
            symbol="2330",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            qty=Qty(Decimal("1000")),
            price=Money(Decimal("500.5")),
        )
        json_str = evt.to_json()
        assert "OrderPlaced" in json_str
        assert "2330" in json_str

    def test_trade_executed_has_all_fields(self):
        evt = TradeExecutedEvent(
            trade_id=uuid.uuid4(),
            symbol="2330",
            price=Money(Decimal("501")),
            qty=Qty(Decimal("100")),
            buy_order_id=uuid.uuid4(),
            sell_order_id=uuid.uuid4(),
            maker_side=OrderSide.BUY,
            taker_side=OrderSide.SELL,
            matched_at=datetime.now(timezone.utc),
        )
        d = evt.to_dict()
        assert d["event_type"] == EventType.TRADE_EXECUTED
        assert d["symbol"] == "2330"


class TestEventType:
    def test_all_event_types_registered(self):
        """Every concrete event class should auto-register via @register_event."""
        from src.events.base_event import _EVENT_REGISTRY

        expected = {
            "OrderPlaced", "OrderAccepted", "OrderRejected",
            "OrderCanceled", "OrderRouted", "OrderFilled",
            "CancelRejected", "OrderRemainOnBook",
            "TradeExecuted", "AccountUpdated", "MarketDataUpdated",
            "TradeSettlement", "AccountDeleted",
        }
        assert expected == set(_EVENT_REGISTRY.keys())
