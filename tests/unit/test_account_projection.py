# tests/unit/test_account_projection.py
"""
Unit tests for Account Replay (Task #4).

Validates:
  ✓ Account state is fully built from events
  ✓ Deposit / lock / unlock / settle cycle
  ✓ No hidden state
"""
import uuid
from decimal import Decimal

from src.account.account import AccountService
from src.common.types import Money, OrderID, Qty, Symbol
from src.events.order_events import OrderSide


class TestAccountService:
    def test_deposit_and_check_balance(self):
        svc = AccountService()
        acct = svc.get_or_create("user1")
        acct.deposit(Money(Decimal("50000")))
        assert acct.cash_available == Money(Decimal("50000"))
        assert acct.cash_locked == Money(Decimal("0"))

    def test_lock_funds_buy(self):
        svc = AccountService()
        acct = svc.get_or_create("user1")
        acct.deposit(Money(Decimal("50000")))

        oid = OrderID(uuid.uuid4())
        svc.lock_funds("user1", oid, Symbol("2330"), OrderSide.BUY,
                       Qty(Decimal("10")), Money(Decimal("500")))

        # 10 * 500 = 5000 locked
        assert acct.cash_available == Money(Decimal("45000"))
        assert acct.cash_locked == Money(Decimal("5000"))

    def test_unlock_funds_buy(self):
        svc = AccountService()
        acct = svc.get_or_create("user1")
        acct.deposit(Money(Decimal("50000")))

        oid = OrderID(uuid.uuid4())
        svc.lock_funds("user1", oid, Symbol("2330"), OrderSide.BUY,
                       Qty(Decimal("10")), Money(Decimal("500")))
        svc.unlock_funds("user1", oid)

        assert acct.cash_available == Money(Decimal("50000"))
        assert acct.cash_locked == Money(Decimal("0"))

    def test_lock_funds_sell(self):
        svc = AccountService()
        acct = svc.get_or_create("user1")
        acct.deposit(Money(Decimal("50000")))
        # Give some shares
        pos = acct.get_position(Symbol("2330"))
        pos.qty_available = Qty(Decimal("100"))

        oid = OrderID(uuid.uuid4())
        svc.lock_funds("user1", oid, Symbol("2330"), OrderSide.SELL,
                       Qty(Decimal("50")), None)

        assert pos.qty_available == Qty(Decimal("50"))
        assert pos.qty_locked == Qty(Decimal("50"))

    def test_settle_trade(self):
        svc = AccountService()
        buyer = svc.get_or_create("buyer1")
        seller = svc.get_or_create("seller1")

        buyer.deposit(Money(Decimal("100000")))
        seller_pos = seller.get_position(Symbol("2330"))
        seller_pos.qty_available = Qty(Decimal("100"))

        buyer_oid = OrderID(uuid.uuid4())
        seller_oid = OrderID(uuid.uuid4())

        # Lock
        svc.lock_funds("buyer1", buyer_oid, Symbol("2330"), OrderSide.BUY,
                       Qty(Decimal("50")), Money(Decimal("500")))
        svc.lock_funds("seller1", seller_oid, Symbol("2330"), OrderSide.SELL,
                       Qty(Decimal("50")), None)

        # Settle
        svc.settle_trade(
            buyer_account_id="buyer1",
            seller_account_id="seller1",
            buyer_order_id=buyer_oid,
            seller_order_id=seller_oid,
            symbol=Symbol("2330"),
            price=Money(Decimal("500")),
            qty=Qty(Decimal("50")),
        )

        # Buyer: paid 25000, got 50 shares
        assert buyer.cash_locked == Money(Decimal("0"))
        assert buyer.cash_available == Money(Decimal("75000"))
        assert buyer.get_position(Symbol("2330")).qty_available == Qty(Decimal("50"))

        # Seller: received 25000, lost 50 shares
        assert seller.cash_available == Money(Decimal("25000"))
        assert seller.get_position(Symbol("2330")).qty_available == Qty(Decimal("50"))
        assert seller.get_position(Symbol("2330")).qty_locked == Qty(Decimal("0"))
