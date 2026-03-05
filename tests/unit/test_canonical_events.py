# tests/unit/test_canonical_events.py
"""
DSD §2.6 – Canonical Event Model & Topics/Channels.

Validates:
  A. Event registry – every EventType has a registered concrete class
  B. Serialization roundtrip – to_dict → event_from_dict reproduces the event
  C. JSON safety – to_dict() returns only primitives (no UUID/Decimal/Enum/datetime)
  D. to_json() / from_dict roundtrip via JSON string
  E. Enum coercion – event_from_dict restores OrderSide, OrderType, etc.
  F. Topic routing – event_to_topics returns correct channels
  G. EVENT_CHANNEL_MAP covers every EventType
  H. events/__init__ exports all public symbols
  I. No hard-coded channel strings outside topics.py / websocket.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.common.enums import OrderSide, OrderType, RejectionReason
from src.common.types import Money, OrderID, Qty, Symbol, TradeID
from src.events.base_event import (
    BaseEvent,
    EventType,
    event_from_dict,
    get_event_registry,
    _json_default,
    _sanitize,
)
from src.common.topics import (
    CHANNEL_MARKET,
    CHANNEL_ORDERS,
    CHANNEL_TRADES,
    CHANNEL_MARKET_ALL,
    EVENT_CHANNEL_MAP,
    event_to_topics,
)


# ═══════════════════════════════════════════════════════════════
# A. Registry completeness
# ═══════════════════════════════════════════════════════════════

class TestEventRegistryCompleteness:
    """Every EventType enum member must have a corresponding registered class."""

    def test_every_event_type_has_class(self):
        registry = get_event_registry()
        for et in EventType:
            assert et.value in registry, f"EventType.{et.name} ({et.value}) not in registry"

    def test_registry_count_equals_enum_count(self):
        assert len(get_event_registry()) == len(EventType)

    def test_get_event_registry_returns_copy(self):
        r1 = get_event_registry()
        r2 = get_event_registry()
        assert r1 == r2
        r1["FAKE"] = object  # type: ignore
        assert "FAKE" not in get_event_registry()

    def test_all_registered_classes_are_base_event_subclasses(self):
        for name, cls in get_event_registry().items():
            assert issubclass(cls, BaseEvent), f"{name} → {cls} is not a BaseEvent subclass"


# ═══════════════════════════════════════════════════════════════
# B. Serialization roundtrip (to_dict → event_from_dict)
# ═══════════════════════════════════════════════════════════════

def _make_order_placed() -> BaseEvent:
    from src.events.order_events import OrderPlacedEvent
    return OrderPlacedEvent(
        order_id=uuid.uuid4(),
        account_id="acct-1",
        symbol="2330",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Qty(Decimal("1000")),
        price=Money(Decimal("595.50")),
    )


def _make_trade_executed() -> BaseEvent:
    from src.events.trade_events import TradeExecutedEvent
    return TradeExecutedEvent(
        trade_id=uuid.uuid4(),
        symbol="2330",
        price=Money(Decimal("595.50")),
        qty=Qty(Decimal("200")),
        buy_order_id=uuid.uuid4(),
        sell_order_id=uuid.uuid4(),
        maker_side=OrderSide.BUY,
        taker_side=OrderSide.SELL,
        matched_at=datetime.now(timezone.utc),
    )


def _make_market_data() -> BaseEvent:
    from src.events.market_events import MarketDataUpdatedEvent
    return MarketDataUpdatedEvent(
        symbol="2317",
        bid_price=Money(Decimal("150.00")),
        bid_qty=Qty(Decimal("5000")),
        ask_price=Money(Decimal("150.50")),
        ask_qty=Qty(Decimal("3000")),
        volume=Qty(Decimal("1000000")),
        trades_count=4200,
    )


def _make_order_rejected() -> BaseEvent:
    from src.events.order_events import OrderRejectedEvent
    return OrderRejectedEvent(
        order_id=uuid.uuid4(),
        reason=RejectionReason.INSUFFICIENT_CASH,
    )


def _make_trade_settlement() -> BaseEvent:
    from src.events.account_events import TradeSettlementEvent
    return TradeSettlementEvent(
        buyer_account_id="buyer-1",
        seller_account_id="seller-1",
        buyer_order_id=uuid.uuid4(),
        seller_order_id=uuid.uuid4(),
        symbol="2330",
        price=Money(Decimal("600")),
        qty=Qty(Decimal("500")),
    )


# Parametrise over all factory functions so every event type gets tested
_ALL_FACTORIES = [
    _make_order_placed,
    _make_trade_executed,
    _make_market_data,
    _make_order_rejected,
    _make_trade_settlement,
]


class TestSerializationRoundtrip:

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_to_dict_then_from_dict_preserves_event_type(self, factory):
        evt = factory()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert restored.event_type == evt.event_type

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_to_dict_then_from_dict_preserves_event_id(self, factory):
        evt = factory()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert restored.event_id == evt.event_id

    def test_order_placed_full_roundtrip(self):
        evt = _make_order_placed()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert restored.symbol == evt.symbol
        assert restored.side == evt.side
        assert restored.order_type == evt.order_type
        assert restored.qty == evt.qty
        assert restored.price == evt.price
        assert restored.account_id == evt.account_id

    def test_trade_executed_full_roundtrip(self):
        evt = _make_trade_executed()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert restored.symbol == evt.symbol
        assert restored.price == evt.price
        assert restored.qty == evt.qty
        assert restored.buy_order_id == evt.buy_order_id
        assert restored.sell_order_id == evt.sell_order_id
        assert restored.maker_side == evt.maker_side
        assert restored.taker_side == evt.taker_side

    def test_order_rejected_enum_roundtrip(self):
        evt = _make_order_rejected()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert restored.reason == RejectionReason.INSUFFICIENT_CASH
        assert isinstance(restored.reason, RejectionReason)


# ═══════════════════════════════════════════════════════════════
# C. JSON safety — to_dict() returns only primitives
# ═══════════════════════════════════════════════════════════════

class TestJsonSafety:

    def _check_primitives(self, obj: Any, path: str = "root") -> None:
        """Recursively assert every leaf is a JSON-safe primitive."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert isinstance(k, str), f"Non-str dict key at {path}.{k}"
                self._check_primitives(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                self._check_primitives(v, f"{path}[{i}]")
        elif obj is None:
            pass
        else:
            assert isinstance(obj, (str, int, float, bool)), (
                f"Non-primitive at {path}: {type(obj).__name__} = {obj!r}"
            )

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_to_dict_all_primitives(self, factory):
        evt = factory()
        d = evt.to_dict()
        self._check_primitives(d)

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_json_dumps_without_custom_default(self, factory):
        """json.dumps should work WITHOUT _json_default because to_dict is already safe."""
        evt = factory()
        d = evt.to_dict()
        # Must not raise TypeError
        result = json.dumps(d, ensure_ascii=False)
        assert isinstance(result, str)

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_to_json_produces_valid_json(self, factory):
        evt = factory()
        j = evt.to_json()
        parsed = json.loads(j)
        assert parsed["event_type"] == evt.event_type.value


# ═══════════════════════════════════════════════════════════════
# D. JSON string roundtrip (to_json → json.loads → event_from_dict)
# ═══════════════════════════════════════════════════════════════

class TestJsonStringRoundtrip:

    @pytest.mark.parametrize("factory", _ALL_FACTORIES, ids=lambda f: f.__name__)
    def test_to_json_then_from_dict(self, factory):
        evt = factory()
        j = evt.to_json()
        data = json.loads(j)
        restored = event_from_dict(data)
        assert restored.event_type == evt.event_type
        assert restored.event_id == evt.event_id

    def test_full_ndjson_roundtrip(self):
        """Simulate write→read for NDJSON event log."""
        events = [f() for f in _ALL_FACTORIES]
        ndjson = "\n".join(e.to_json() for e in events)

        restored = []
        for line in ndjson.strip().split("\n"):
            restored.append(event_from_dict(json.loads(line)))

        assert len(restored) == len(events)
        for orig, rest in zip(events, restored):
            assert rest.event_type == orig.event_type
            assert rest.event_id == orig.event_id


# ═══════════════════════════════════════════════════════════════
# E. Enum coercion in event_from_dict
# ═══════════════════════════════════════════════════════════════

class TestEnumCoercion:

    def test_order_side_coerced_from_string(self):
        evt = _make_order_placed()
        d = evt.to_dict()
        assert isinstance(d["side"], str)  # serialised as string
        restored = event_from_dict(d)
        assert isinstance(restored.side, OrderSide)
        assert restored.side == OrderSide.BUY

    def test_order_type_coerced_from_string(self):
        evt = _make_order_placed()
        d = evt.to_dict()
        assert isinstance(d["order_type"], str)
        restored = event_from_dict(d)
        assert isinstance(restored.order_type, OrderType)
        assert restored.order_type == OrderType.LIMIT

    def test_rejection_reason_coerced(self):
        evt = _make_order_rejected()
        d = evt.to_dict()
        restored = event_from_dict(d)
        assert isinstance(restored.reason, RejectionReason)

    def test_unknown_event_type_raises(self):
        with pytest.raises(ValueError, match="Unknown event_type"):
            event_from_dict({"event_type": "NoSuchEvent"})


# ═══════════════════════════════════════════════════════════════
# F. Topic routing (event_to_topics)
# ═══════════════════════════════════════════════════════════════

class TestTopicRouting:

    def test_market_event_routes_to_market_symbol(self):
        evt = _make_market_data()
        topics = event_to_topics(evt)
        assert topics == [f"{CHANNEL_MARKET}.2317"]

    def test_order_placed_routes_to_orders_account(self):
        evt = _make_order_placed()
        topics = event_to_topics(evt)
        assert topics == [f"{CHANNEL_ORDERS}.acct-1"]

    def test_order_accepted_needs_resolver(self):
        from src.events.order_events import OrderAcceptedEvent
        oid = uuid.uuid4()
        evt = OrderAcceptedEvent(order_id=oid)
        # Without resolver: no account_id → empty
        topics_no_resolver = event_to_topics(evt)
        assert topics_no_resolver == []

        # With resolver: resolves to account_id
        def resolver(order_id):
            return "resolved-user"

        topics = event_to_topics(evt, resolve_account_id=resolver)
        assert topics == [f"{CHANNEL_ORDERS}.resolved-user"]

    def test_trade_executed_routes_to_both_participants(self):
        evt = _make_trade_executed()
        buy_oid = evt.buy_order_id
        sell_oid = evt.sell_order_id

        accounts = {}
        accounts[buy_oid] = "buyer-1"
        accounts[sell_oid] = "seller-1"

        def resolver(oid):
            return accounts.get(oid, "")

        topics = event_to_topics(evt, resolve_account_id=resolver)
        assert f"{CHANNEL_TRADES}.buyer-1" in topics
        assert f"{CHANNEL_TRADES}.seller-1" in topics
        assert len(topics) == 2

    def test_trade_executed_deduplicates_same_user(self):
        """If buyer == seller (self-trade), only one channel."""
        evt = _make_trade_executed()

        def resolver(oid):
            return "same-user"

        topics = event_to_topics(evt, resolve_account_id=resolver)
        assert topics == [f"{CHANNEL_TRADES}.same-user"]

    def test_trade_settlement_routes_to_both(self):
        evt = _make_trade_settlement()
        topics = event_to_topics(evt)
        assert f"{CHANNEL_TRADES}.buyer-1" in topics
        assert f"{CHANNEL_TRADES}.seller-1" in topics

    def test_unknown_event_returns_empty(self):
        """A bare BaseEvent with no registered event_type → empty topics."""

        class FakeEvent:
            event_type = None

        topics = event_to_topics(FakeEvent())  # type: ignore
        assert topics == []


# ═══════════════════════════════════════════════════════════════
# G. EVENT_CHANNEL_MAP covers every EventType
# ═══════════════════════════════════════════════════════════════

class TestEventChannelMap:

    def test_every_event_type_has_channel(self):
        for et in EventType:
            assert et in EVENT_CHANNEL_MAP, f"EventType.{et.name} not in EVENT_CHANNEL_MAP"

    def test_channel_prefixes_are_known(self):
        valid = {CHANNEL_MARKET, CHANNEL_ORDERS, CHANNEL_TRADES}
        for et, prefix in EVENT_CHANNEL_MAP.items():
            assert prefix in valid, f"{et.name} maps to unknown prefix: {prefix}"

    def test_market_event_maps_to_market(self):
        assert EVENT_CHANNEL_MAP[EventType.MARKET_DATA_UPDATED] == CHANNEL_MARKET

    def test_trade_events_map_to_trades(self):
        assert EVENT_CHANNEL_MAP[EventType.TRADE_EXECUTED] == CHANNEL_TRADES
        assert EVENT_CHANNEL_MAP[EventType.TRADE_SETTLEMENT] == CHANNEL_TRADES


# ═══════════════════════════════════════════════════════════════
# H. events/__init__ exports
# ═══════════════════════════════════════════════════════════════

class TestEventsPackageExports:

    def test_all_event_classes_importable(self):
        import src.events as events_pkg
        for name, cls in get_event_registry().items():
            cls_name = cls.__name__
            assert hasattr(events_pkg, cls_name), (
                f"{cls_name} not importable from src.events"
            )

    def test_utility_functions_importable(self):
        import src.events as events_pkg
        assert hasattr(events_pkg, "event_from_dict")
        assert hasattr(events_pkg, "get_event_registry")
        assert hasattr(events_pkg, "register_event")
        assert hasattr(events_pkg, "EventType")
        assert hasattr(events_pkg, "BaseEvent")

    def test_dunder_all_complete(self):
        import src.events as events_pkg
        all_names = set(events_pkg.__all__)
        # Every registered event class should be in __all__
        for name, cls in get_event_registry().items():
            assert cls.__name__ in all_names, f"{cls.__name__} not in events.__all__"
        # Base infrastructure
        for util in ("BaseEvent", "EventType", "event_from_dict", "get_event_registry", "register_event"):
            assert util in all_names, f"{util} not in events.__all__"


# ═══════════════════════════════════════════════════════════════
# I. No hard-coded channel strings in src/ (except topics.py & websocket.py)
# ═══════════════════════════════════════════════════════════════

class TestNoHardCodedChannels:

    def test_no_hardcoded_orders_channel_in_app(self):
        """src/app.py should not contain literal 'orders.' channel strings."""
        import pathlib
        app_path = pathlib.Path(__file__).resolve().parents[2] / "src" / "app.py"
        if not app_path.exists():
            pytest.skip("app.py not found")
        content = app_path.read_text(encoding="utf-8")
        # Look for suspicious patterns like "orders.{" or 'orders.'
        # but NOT inside comments or topic imports
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("from ") or stripped.startswith("import "):
                continue
            assert '"orders.' not in stripped and "'orders." not in stripped, (
                f"Hard-coded 'orders.' channel found in app.py line {i}: {stripped}"
            )

    def test_channel_constants_consistent(self):
        assert CHANNEL_MARKET == "market"
        assert CHANNEL_ORDERS == "orders"
        assert CHANNEL_TRADES == "trades"
        assert CHANNEL_MARKET_ALL == "market.*"
