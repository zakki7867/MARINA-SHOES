from datetime import date
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import InsufficientStockError, record_stock_movement
from apps.invoicing.models import Invoice
from apps.invoicing.services import CreditLimitExceededError, cancel_invoice, create_invoice
from apps.sales.models import PriceList, SalesReturn
from apps.sales.services import convert_sales_order_to_invoice, create_sales_order, process_sales_exchange, process_sales_return


@pytest.fixture
def billing_setup(db, store_settings, owner_user):
    cat = Category.objects.create(name="Formal")
    brand = Brand.objects.create(name="Marina Gold")
    product = Product.objects.create(
        name="Oxford Brogue",
        category=cat,
        brand=brand,
        hsn_code="6403",
        tax_rate=Decimal("12.00"),
    )
    variant = ProductVariant.objects.create(
        product=product,
        size="42",
        color="Black",
        sku="MG-OX-42-BLK",
        cost_price=Decimal("20.00"),
        wholesale_price=Decimal("30.00"),
        retail_price=Decimal("45.00"),
        reorder_level=3,
    )
    record_stock_movement(
        variant=variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=50,
        reference="OPENING",
        user=owner_user,
    )
    local_wholesale = Customer.objects.create(
        name="Salmiya Footwear Hub",
        customer_type=Customer.CustomerType.WHOLESALE,
        phone="+96598200011",
        state_code="KW",
        credit_limit=Decimal("500.000"),
        payment_terms_days=30,
    )
    interstate_wholesale = Customer.objects.create(
        name="Hawalli / External Shoe Palace",
        customer_type=Customer.CustomerType.WHOLESALE,
        phone="+96598110022",
        state_code="SA",
        credit_limit=Decimal("0.000"),
    )
    return variant, local_wholesale, interstate_wholesale


@pytest.mark.django_db
def test_gst_split_cgst_sgst_vs_igst_and_fy_numbering(billing_setup, billing_user):
    variant, local_cust, inter_cust = billing_setup

    # Intra-state (KW == KW): 10 * 30.00 = 300.00, 12% GST => 6% CGST (18.00) + 6% SGST (18.00) = 336.00
    inv1 = create_invoice(
        customer=local_cust,
        items_data=[{"variant": variant, "quantity": 10}],
        invoice_date=date(2026, 11, 20),
        user=billing_user,
    )
    assert inv1.invoice_number == "MS/2026-27/0001"
    assert inv1.taxable_amount == Decimal("300.00")
    assert inv1.cgst_total == Decimal("18.00")
    assert inv1.sgst_total == Decimal("18.00")
    assert inv1.igst_total == Decimal("0.00")
    assert inv1.grand_total == Decimal("336.00")
    assert variant.current_stock == 40

    # Inter-state (SA != KW) with 10% discount in next FY (May 2027 -> 2027-28):
    # 5 * 30.00 = 150.00, 10% disc = 15.00 => taxable 135.00, 12% IGST = 16.20 => total 151.20
    inv2 = create_invoice(
        customer=inter_cust,
        items_data=[{"variant": variant, "quantity": 5, "discount_percent": Decimal("10.00")}],
        invoice_date=date(2027, 5, 10),
        user=billing_user,
    )
    assert inv2.invoice_number == "MS/2027-28/0001"
    assert inv2.cgst_total == Decimal("0.00")
    assert inv2.sgst_total == Decimal("0.00")
    assert inv2.igst_total == Decimal("16.20")
    assert inv2.grand_total == Decimal("151.20")
    assert variant.current_stock == 35


@pytest.mark.django_db
def test_credit_limit_enforcement_and_insufficient_stock(billing_setup, billing_user, manager_user):
    variant, local_cust, _ = billing_setup

    # 20 * 30.00 + 12% = 672.00 > credit limit 500.00
    with pytest.raises(CreditLimitExceededError):
        create_invoice(
            customer=local_cust,
            items_data=[{"variant": variant, "quantity": 20}],
            user=billing_user,
        )
    # Manager override succeeds
    inv = create_invoice(
        customer=local_cust,
        items_data=[{"variant": variant, "quantity": 20}],
        user=manager_user,
        override_credit_limit=True,
    )
    assert inv.status == Invoice.Status.ISSUED
    assert variant.current_stock == 30

    # Exceeds remaining stock (30)
    with pytest.raises(InsufficientStockError):
        create_invoice(
            customer=local_cust,
            items_data=[{"variant": variant, "quantity": 31}],
            user=manager_user,
            override_credit_limit=True,
        )


@pytest.mark.django_db
def test_issued_invoice_cannot_be_deleted_cancel_and_return_restore_stock(billing_setup, billing_user, manager_user):
    variant, local_cust, _ = billing_setup
    so = create_sales_order(
        customer=local_cust,
        price_list=PriceList.RETAIL,
        items_data=[{"variant": variant, "quantity": 4}],
        user=billing_user,
    )
    inv = convert_sales_order_to_invoice(sales_order=so, user=billing_user)
    assert inv.grand_total == Decimal("201.60")  # 4 * 45 = 180 + 12% (21.60)
    assert variant.current_stock == 46

    with pytest.raises(ValidationError):
        inv.delete()

    # Sales return of 1 unit restores stock & issues CreditNote
    sr = process_sales_return(
        invoice=inv,
        items_data=[{"variant": variant, "quantity": 1}],
        user=billing_user,
        reason="Size mismatch",
    )
    assert sr.total_amount == Decimal("50.40")
    assert variant.current_stock == 47
    inv.refresh_from_db()
    assert inv.balance_due == Decimal("151.20")

    # Separate invoice cancel restores full stock and logs audit
    inv2 = create_invoice(
        customer=local_cust,
        items_data=[{"variant": variant, "quantity": 2}],
        user=billing_user,
    )
    assert variant.current_stock == 45
    cancel_invoice(invoice=inv2, user=manager_user, reason="Customer changed order")
    inv2.refresh_from_db()
    assert inv2.status == Invoice.Status.CANCELLED
    assert variant.current_stock == 47
    assert AuditLog.objects.filter(model_name="Invoice", action=AuditLog.Action.CANCEL).exists()


@pytest.mark.django_db
def test_sales_invoicing_role_permissions_and_print_whatsapp(client, api_client, billing_setup, billing_user, storekeeper_user):
    variant, local_cust, _ = billing_setup

    # Storekeeper blocked from customers, sales, invoicing
    client.force_login(storekeeper_user)
    assert client.get(reverse("customers:customer_list")).status_code == 403
    assert client.get(reverse("sales:order_list")).status_code == 403
    assert client.get(reverse("invoicing:invoice_list")).status_code == 403

    # Billing staff can access and create via API, cannot cancel
    api_client.force_authenticate(user=billing_user)
    resp = api_client.post(
        "/api/v1/invoicing/invoices/",
        {
            "customer": local_cust.pk,
            "items": [{"variant": variant.pk, "quantity": 2}],
        },
        format="json",
    )
    assert resp.status_code == 201
    inv_id = resp.data["id"]

    cancel_resp = api_client.post(f"/api/v1/invoicing/invoices/{inv_id}/cancel/", {"reason": "test"}, format="json")
    assert cancel_resp.status_code == 403

    client.force_login(billing_user)
    print_resp = client.get(reverse("invoicing:invoice_print", kwargs={"pk": inv_id}))
    assert print_resp.status_code == 200
    detail_resp = client.get(reverse("invoicing:invoice_detail", kwargs={"pk": inv_id}))
    assert detail_resp.status_code == 200
    assert "https://wa.me/96598200011" in detail_resp.content.decode()


@pytest.mark.django_db
def test_gst_inclusive_custom_price_permission_damaged_return_and_exchange(client, billing_setup, billing_user, manager_user):
    variant, local_cust, _ = billing_setup

    # 1. GST-inclusive pricing: custom unit_price = 112.00 (12% GST included) -> taxable 100.00, CGST 6.00, SGST 6.00, total 112.00
    with pytest.raises(PermissionDenied):
        create_invoice(
            customer=local_cust,
            items_data=[{"variant": variant, "quantity": 1, "unit_price": Decimal("112.00")}],
            tax_mode=StoreSettings.TaxMode.INCLUSIVE,
            enforce_price_permission=True,
            user=billing_user,
        )

    inv = create_invoice(
        customer=local_cust,
        items_data=[{"variant": variant, "quantity": 2, "unit_price": Decimal("112.00")}],
        tax_mode=StoreSettings.TaxMode.INCLUSIVE,
        enforce_price_permission=True,
        user=manager_user,
    )
    assert inv.taxable_amount == Decimal("200.00")
    assert inv.cgst_total == Decimal("12.00")
    assert inv.sgst_total == Decimal("12.00")
    assert inv.grand_total == Decimal("224.00")
    assert variant.current_stock == 48

    # 2. Damaged return of 1 unit + refund 112.00 -> salable stock remains 48 (SALE_RETURN +1 then DAMAGE -1)
    sr = process_sales_return(
        invoice=inv,
        items_data=[{"variant": variant, "quantity": 1}],
        condition=SalesReturn.Condition.DAMAGED,
        refund_amount=Decimal("112.00"),
        refund_mode="CASH",
        user=manager_user,
    )
    assert variant.current_stock == 48
    assert sr.refund_amount == Decimal("112.00")

    # 3. Exchange remaining 1 unit for another variant
    replacement = ProductVariant.objects.create(
        product=variant.product,
        size="43",
        color="Black",
        sku="MG-OX-43-BLK",
        cost_price=Decimal("20.00"),
        wholesale_price=Decimal("30.00"),
        retail_price=Decimal("45.00"),
    )
    record_stock_movement(variant=replacement, movement_type=StockMovement.MovementType.PURCHASE, quantity=10, user=manager_user)

    ex = process_sales_exchange(
        original_invoice=inv,
        return_items=[{"variant": variant, "quantity": 1}],
        replacement_items=[{"variant": replacement, "quantity": 1}],
        user=manager_user,
        reason="Size 42 -> 43",
    )
    assert variant.current_stock == 49
    assert replacement.current_stock == 9
    assert ex.replacement_invoice.status == Invoice.Status.ISSUED

    # 4. POS endpoint checkout
    client.force_login(billing_user)
    assert client.get(reverse("sales:pos")).status_code == 200
    assert client.get(reverse("sales:pos_search") + "?q=MG-OX-43").status_code == 200
    loc = StoreLocation.get_default()
    pos_resp = client.post(
        reverse("sales:pos"),
        {
            "customer": local_cust.pk,
            "location": loc.pk,
            "price_list": PriceList.RETAIL,
            "tax_mode": StoreSettings.TaxMode.EXCLUSIVE,
            "invoice_discount": "0.00",
            "amount_paid": "50.00",
            "payment_mode": "KNET",
            "variant_id": [str(replacement.pk)],
            "qty": ["1"],
            "unit_price": [""],
            "discount_percent": ["0"],
        },
    )
    assert pos_resp.status_code == 302
    assert replacement.current_stock == 8


@pytest.mark.django_db
def test_invoice_discount_gst_reconciliation_api_price_guard_and_pos_idempotency(
    client, api_client, billing_setup, billing_user, manager_user
):
    variant, local_cust, _ = billing_setup

    # 1. Invoice-level discount reduces taxable value and GST proportionally:
    # 10 * 30 = 300.00 gross, invoice_discount = 50.00 -> taxable = 250.00, 12% GST = 15.00 CGST + 15.00 SGST, total = 280.00
    inv = create_invoice(
        customer=local_cust,
        items_data=[{"variant": variant, "quantity": 10}],
        invoice_discount=Decimal("50.00"),
        user=manager_user,
    )
    assert inv.taxable_amount == Decimal("250.00")
    assert inv.cgst_total == Decimal("15.00")
    assert inv.sgst_total == Decimal("15.00")
    assert inv.grand_total == Decimal("280.00")
    item = inv.items.first()
    assert item.taxable_value == inv.taxable_amount
    assert item.line_total == inv.grand_total

    # 2. Credit note cannot exceed invoice grand total
    from apps.invoicing.services import create_credit_note
    with pytest.raises(ValidationError):
        create_credit_note(
            invoice=inv,
            items_data=[{"variant": variant, "quantity": 12, "unit_price": Decimal("30.00")}],
            user=manager_user,
        )

    # 3. API enforces custom price permission for billing staff
    api_client.force_authenticate(user=billing_user)
    resp = api_client.post(
        "/api/v1/invoicing/invoices/",
        {"customer": local_cust.pk, "items": [{"variant": variant.pk, "quantity": 1, "unit_price": "5.00"}]},
        format="json",
    )
    assert resp.status_code == 403

    # 4. POS double-submit protection with pos_token
    client.force_login(billing_user)
    get_resp = client.get(reverse("sales:pos"))
    token = get_resp.context["pos_token"]
    loc = StoreLocation.get_default()
    payload = {
        "pos_token": token,
        "customer": local_cust.pk,
        "location": loc.pk,
        "price_list": PriceList.WHOLESALE,
        "tax_mode": StoreSettings.TaxMode.EXCLUSIVE,
        "invoice_discount": "0.00",
        "amount_paid": "0.00",
        "payment_mode": "CASH",
        "variant_id": [str(variant.pk)],
        "qty": ["1"],
        "unit_price": [""],
        "discount_percent": ["0"],
    }
    first_post = client.post(reverse("sales:pos"), payload)
    assert first_post.status_code == 302
    assert "/invoicing/" in first_post.url

    count_before = Invoice.objects.count()
    dup_post = client.post(reverse("sales:pos"), payload)
    assert dup_post.status_code == 302
    assert dup_post.url == reverse("sales:pos")
    assert Invoice.objects.count() == count_before
