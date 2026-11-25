from datetime import date, timedelta
from decimal import Decimal
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import Customer
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import record_stock_movement
from apps.invoicing.models import Invoice
from apps.invoicing.services import create_invoice
from apps.payments.services import (
    apply_customer_advance,
    get_customer_aging,
    get_overdue_invoices,
    record_customer_payment,
    reverse_customer_payment,
)


@pytest.fixture
def ledger_setup(db, store_settings, owner_user):
    cat = Category.objects.create(name="Boots")
    brand = Brand.objects.create(name="DesertWalk")
    prod = Product.objects.create(name="Chelsea Boot", category=cat, brand=brand, hsn_code="6403", tax_rate=Decimal("10.00"))
    variant = ProductVariant.objects.create(
        product=prod,
        size="44",
        color="Brown",
        sku="DW-CB-44-BRN",
        cost_price=Decimal("25.00"),
        wholesale_price=Decimal("40.00"),
        retail_price=Decimal("60.00"),
        reorder_level=2,
    )
    record_stock_movement(
        variant=variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=30,
        user=owner_user,
    )
    cust = Customer.objects.create(
        name="Hawalli Shoe Mart",
        customer_type=Customer.CustomerType.WHOLESALE,
        phone="+96566667777",
        state_code="KW",
        opening_balance=Decimal("50.00"),
        payment_terms_days=15,
    )
    return cust, variant


@pytest.mark.django_db
def test_partial_and_full_payment_and_credit_ledger_balance(ledger_setup, billing_user):
    cust, variant = ledger_setup

    # Invoice total: 5 * 40 = 200.00 + 10% GST = 220.00
    inv = create_invoice(
        customer=cust,
        items_data=[{"variant": variant, "quantity": 5}],
        invoice_date=timezone.localdate() - timedelta(days=20),
        due_date=timezone.localdate() - timedelta(days=5),
        user=billing_user,
    )
    assert inv.status == Invoice.Status.ISSUED
    assert cust.outstanding_balance == Decimal("270.00")  # 50 opening + 220 invoice
    assert inv in get_overdue_invoices()

    # Partial payment of 120.00
    record_customer_payment(
        customer=cust,
        invoice=inv,
        amount=Decimal("120.00"),
        payment_mode="CASH",
        user=billing_user,
    )
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PARTIAL
    assert inv.balance_due == Decimal("100.00")
    assert cust.outstanding_balance == Decimal("150.00")

    # Remaining 100.00 paid via KNET
    record_customer_payment(
        customer=cust,
        invoice=inv,
        amount=Decimal("100.00"),
        payment_mode="KNET",
        user=billing_user,
    )
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PAID
    assert inv.balance_due == Decimal("0.00")
    assert inv not in get_overdue_invoices()
    assert cust.outstanding_balance == Decimal("50.00")


@pytest.mark.django_db
def test_payments_role_permissions(client, billing_user, storekeeper_user):
    client.force_login(billing_user)
    assert client.get(reverse("payments:payment_list")).status_code == 200
    assert client.get(reverse("payments:credit_ledger")).status_code == 200
    assert client.get(reverse("payments:overdue_list")).status_code == 200

    client.force_login(storekeeper_user)
    assert client.get(reverse("payments:payment_list")).status_code == 403


@pytest.mark.django_db
def test_customer_advance_apply_payment_reversal_aging_and_ledger(client, ledger_setup, billing_user, manager_user):
    cust, variant = ledger_setup

    # 1. Advance payment of 100.00 before any invoice
    adv = record_customer_payment(
        customer=cust,
        amount=Decimal("100.00"),
        payment_mode="KNET",
        is_advance=True,
        user=billing_user,
    )
    assert adv.unallocated_amount == Decimal("100.00")

    # 2. Invoice 45 days ago -> aging bucket 31-60 days
    inv = create_invoice(
        customer=cust,
        items_data=[{"variant": variant, "quantity": 5}],  # 220.00 total
        invoice_date=timezone.localdate() - timedelta(days=45),
        due_date=timezone.localdate() - timedelta(days=15),
        user=billing_user,
    )
    applied = apply_customer_advance(customer=cust, invoice=inv, user=billing_user)
    assert applied == Decimal("100.00")
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PARTIAL
    assert inv.balance_due == Decimal("120.00")
    adv.refresh_from_db()
    assert adv.unallocated_amount == Decimal("0.00")

    aging = get_customer_aging()
    assert aging[0]["b_31_60"] == Decimal("120.00")

    # 3. Audited reversal restores invoice balance back to 220.00
    reverse_customer_payment(payment=adv, user=manager_user, reason="KNET reversal")
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.ISSUED
    assert inv.balance_due == Decimal("220.00")

    # 4. Customer ledger view renders cleanly
    client.force_login(manager_user)
    assert client.get(reverse("customers:customer_ledger", kwargs={"pk": cust.pk})).status_code == 200
    assert client.get(reverse("payments:aging")).status_code == 200


@pytest.mark.django_db
def test_payment_allocation_guard_and_no_n_plus_1_customer_balances(
    ledger_setup, billing_user, django_assert_num_queries
):
    from django.core.exceptions import ValidationError
    from apps.payments.models import PaymentAllocation
    cust, variant = ledger_setup

    inv = create_invoice(customer=cust, items_data=[{"variant": variant, "quantity": 1}], user=billing_user)  # 44.00
    pay = record_customer_payment(customer=cust, amount=Decimal("100.00"), payment_mode="CASH", is_advance=True, user=billing_user)

    bad_alloc = PaymentAllocation(payment=pay, invoice=inv, amount=Decimal("50.00"))
    with pytest.raises(ValidationError):
        bad_alloc.clean()

    for i in range(10):
        Customer.objects.create(name=f"BatchShop {i}", phone=f"99000000{i:02d}", state_code="MH")

    # Fetching all customers with balances must run in 1 SQL query (not 4*N)
    with django_assert_num_queries(1):
        balances = [c.outstanding_balance for c in Customer.objects.with_balances()]
    assert len(balances) == 11
