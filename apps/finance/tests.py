from decimal import Decimal
import pytest
from django.urls import reverse

from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.finance.services import get_gst_summary, get_ledger_book, get_profit_and_loss
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import record_stock_movement
from apps.invoicing.services import create_invoice
from apps.payments.services import record_customer_payment
from apps.purchases.models import Supplier
from apps.purchases.services import record_supplier_payment


@pytest.mark.django_db
def test_cash_bank_books_pnl_and_gst_summary(store_settings, owner_user, billing_user, manager_user):
    cat = Category.objects.create(name="Sneakers")
    brand = Brand.objects.create(name="Marina")
    prod = Product.objects.create(name="Runner", category=cat, brand=brand, hsn_code="6404", tax_rate=Decimal("10.00"))
    var = ProductVariant.objects.create(
        product=prod,
        size="42",
        color="White",
        sku="MR-42-W",
        cost_price=Decimal("10.00"),
        wholesale_price=Decimal("20.00"),
        retail_price=Decimal("25.00"),
    )
    record_stock_movement(variant=var, movement_type=StockMovement.MovementType.PURCHASE, quantity=20, user=owner_user)

    cust = Customer.objects.create(name="Retail Walkin", customer_type=Customer.CustomerType.RETAIL, phone="+96598200123", state_code="KW")
    sup = Supplier.objects.create(name="SupCo", phone="+96598200999", state_code="KW")

    # 5 units @ 25.00 = 125.00 taxable + 12.50 GST (6.25 CGST + 6.25 SGST) = 137.50
    inv = create_invoice(customer=cust, items_data=[{"variant": var, "quantity": 5}], user=billing_user)

    record_customer_payment(customer=cust, invoice=inv, amount=Decimal("100.00"), payment_mode="CASH", user=billing_user)
    record_customer_payment(customer=cust, invoice=inv, amount=Decimal("37.50"), payment_mode="BANK", user=billing_user)
    record_supplier_payment(supplier=sup, amount=Decimal("40.00"), payment_mode="CASH", user=manager_user)
    Expense.objects.create(category=Expense.Category.RENT, amount=Decimal("15.00"), payment_mode="CASH", created_by=manager_user)

    cash_book = get_ledger_book(modes=("CASH",))
    bank_book = get_ledger_book(modes=("BANK", "KNET", "CHEQUE"))
    assert cash_book["closing_balance"] == Decimal("45.00")  # 100 - 40 - 15
    assert bank_book["closing_balance"] == Decimal("37.50")

    pnl = get_profit_and_loss()
    # Net revenue = 125.00, COGS = 5 * 10 = 50.00, Gross = 75.00, Expenses = 15.00, Net Profit = 60.00
    assert pnl["net_revenue"] == Decimal("125.00")
    assert pnl["cogs"] == Decimal("50.00")
    assert pnl["net_profit"] == Decimal("60.00")

    gst = get_gst_summary()
    assert gst["net_cgst"] == Decimal("6.25")
    assert gst["net_sgst"] == Decimal("6.25")
    assert gst["net_total_gst"] == Decimal("12.50")


@pytest.mark.django_db
def test_finance_and_expenses_role_permissions(client, billing_user, storekeeper_user, manager_user):
    for user in (billing_user, storekeeper_user):
        client.force_login(user)
        assert client.get(reverse("expenses:expense_list")).status_code == 403
        assert client.get(reverse("finance:overview")).status_code == 403

    client.force_login(manager_user)
    assert client.get(reverse("expenses:expense_list")).status_code == 200
    assert client.get(reverse("finance:overview")).status_code == 200
