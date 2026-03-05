# src/common/types.py
"""Canonical primitive types shared by every module."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import NewType
from uuid import UUID

Timestamp = NewType("Timestamp", datetime.datetime)
Qty = NewType("Qty", Decimal)
Money = NewType("Money", Decimal)
Symbol = NewType("Symbol", str)
OrderID = NewType("OrderID", UUID)
TradeID = NewType("TradeID", UUID)
AccountID = NewType("AccountID", str)
EventID = NewType("EventID", UUID)
CorrelationID = NewType("CorrelationID", UUID)
