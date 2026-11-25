"""Comprehensive QA test suite: Phases 2-5 (Models/DB, Inventory, Purchases, Customers)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockBalance, StockMovement
from apps.inventory.services import (
    InsufficientStockError,
    record_stock_count,
    record_stock_movement,
    transfer_stock,
)
from apps.payments.services import get_customer_aging, get_supplier_aging
from apps.purchases.models import GoodsReceipt, PurchaseOrder, Supplier
from apps.purchases.services import (
    create_purchase_order,
    create_purchase_return,
    receive_goods,
    record_supplier_payment,
)
from apps.sales.services import process_sales_return
from apps.invoicing.services import create_invoice


@pytest.fixture
def qa_catalog(db):
    cat = Category.objects.create(name="QA Sneakers")
    brand = Brand.objects.create(name="QA Walk")
    prod = Product.objects.create(
        name="QA Runner",
        style_code="QAR-01",
        category=cat,
        brand=brand,
        hsn_code="6404",
        tax_rate=Decimal("12.00"),
    )
    v7 = ProductVariant.objects.create(
        product=prod,
        size="7",
        color="Black",
        sku="QA-RUN-7-BLK",
        barcode="8901111000011",
        cost_price=Decimal("400.00"),
        wholesale_price=Decimal("600.00"),
        retail_price=Decimal("900.00"),
        reorder_level=5,
    )
    v8 = ProductVariant.objects.create(
        product=prod,
        size="8",
        color="White",
        sku="QA-RUN-8-WHT",
        barcode="8901111000028",
        cost_price=Decimal("450.00"),
        wholesale_price=Decimal("650.00"),
        retail_price=Decimal("950.00"),
        reorder_level=3,
    )
    main = StoreLocation.get_default()
    godown = StoreLocation.objects.create(name="QA Godown", code="QAGD", is_default=False)
    return {
        "category": cat,
        "brand": brand,
        "product": prod,
        "v7": v7,
        "v8": v8,
        "main": main,
        "godown": godown,
    }


# ---------------------------------------------------------------------------
# PHASE 2: MODEL & DATABASE INTEGRITY TESTS
# ---------------------------------------------------------------------------
def test_phase2_model_constraints_validation_soft_delete_and_decimals(db, owner_user, qa_catalog):
    v7 = qa_catalog["v7"]
    cat = qa_catalog["category"]
    brand = qa_catalog["brand"]

    # Unique SKU and barcode enforced at DB level
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.create(
                product=qa_catalog["product"],
                size="9",
                color="Blue",
                sku="QA-RUN-7-BLK",
                cost_price=Decimal("100.00"),
                wholesale_price=Decimal("200.00"),
                retail_price=Decimal("300.00"),
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.create(
                product=qa_catalog["product"],
                size="9",
                color="Blue",
                sku="QA-RUN-9-BLU",
                barcode="8901111000011",
                cost_price=Decimal("100.00"),
                wholesale_price=Decimal("200.00"),
                retail_price=Decimal("300.00"),
            )

    # Unique size+color per product
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.create(
                product=qa_catalog["product"],
                size="7",
                color="Black",
                sku="QA-RUN-7-BLK-DUP",
                cost_price=Decimal("100.00"),
                wholesale_price=Decimal("200.00"),
                retail_price=Decimal("300.00"),
            )

    # Validation: negative prices rejected via full_clean()
    bad_var = ProductVariant(
        product=qa_catalog["product"],
        size="10",
        color="Red",
        sku="QA-RUN-10-RED",
        cost_price=Decimal("-10.00"),
        wholesale_price=Decimal("200.00"),
        retail_price=Decimal("300.00"),
    )
    with pytest.raises(ValidationError):
        bad_var.full_clean()

    # Check StockMovement positive quantity CheckConstraint / clean
    with pytest.raises(ValidationError):
        record_stock_movement(
            variant=v7,
            movement_type=StockMovement.MovementType.OPENING_STOCK,
            quantity=0,
            user=owner_user,
        )

    # Soft deletion retains FK integrity and separates objects vs all_objects
    prod2 = Product.objects.create(name="QA Temp Sandal", category=cat, brand=brand, hsn_code="6402")
    prod2.soft_delete()
    assert not Product.objects.filter(pk=prod2.pk).exists()
    assert Product.all_objects.filter(pk=prod2.pk).exists()
    prod2.restore()
    assert Product.objects.filter(pk=prod2.pk).exists()

    # Protected FK prevents deleting brand with existing products
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        brand.delete()

    # Decimal precision preserved (never float)
    assert isinstance(v7.cost_price, Decimal)
    assert str(v7.cost_price) == "400.00"


# ---------------------------------------------------------------------------
# PHASE 3: COMPREHENSIVE INVENTORY TESTS
# ---------------------------------------------------------------------------
def test_phase3_inventory_full_ledger_manual_math_locations_transfers_counts(db, owner_user, store_settings, qa_catalog):
    v7 = qa_catalog["v7"]
    v8 = qa_catalog["v8"]
    main = qa_catalog["main"]
    godown = qa_catalog["godown"]
    cust = Customer.objects.create(
        name="QA Stock Cust",
        customer_type=Customer.CustomerType.WHOLESALE,
        phone="9000001001",
        state_code="KW",
    )

    # Exact manual ledger test from specification:
    # Opening = +20, Purchase = +10, Sale = -5, Return = +2, Damage = -1 -> Expected = 26
    m1 = record_stock_movement(variant=v7, location=main, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=20, reference="INIT", user=owner_user)
    assert m1.direction == StockMovement.Direction.IN and m1.signed_quantity == 20

    m2 = record_stock_movement(variant=v7, location=main, movement_type=StockMovement.MovementType.PURCHASE, quantity=10, reference="PO-1", user=owner_user)
    assert m2.signed_quantity == 10

    inv = create_invoice(
        customer=cust,
        location=main,
        items_data=[{"variant": v7, "quantity": 5}],
        user=owner_user,
        issue_now=True,
    )
    sale_mv = StockMovement.objects.get(variant=v7, movement_type=StockMovement.MovementType.SALE, reference=f"INV:{inv.invoice_number}")
    assert sale_mv.direction == StockMovement.Direction.OUT and sale_mv.signed_quantity == -5

    sr = process_sales_return(invoice=inv, items_data=[{"variant": v7, "quantity": 2}], user=owner_user)
    ret_mv = StockMovement.objects.get(variant=v7, movement_type=StockMovement.MovementType.SALE_RETURN, reference=f"SRET:{sr.return_number}")
    assert ret_mv.direction == StockMovement.Direction.IN and ret_mv.signed_quantity == 2

    dmg_mv = record_stock_movement(
        variant=v7,
        location=main,
        movement_type=StockMovement.MovementType.DAMAGE,
        quantity=1,
        reference="DMG-1",
        reason="Display water damage",
        user=owner_user,
    )
    assert dmg_mv.signed_quantity == -1

    # Manual expected = 26
    assert v7.current_stock == 26
    assert v7.current_stock_at(main) == 26
    assert StockBalance.objects.get(variant=v7, location=main).quantity == 26
    assert StockMovement.objects.filter(variant=v7, location=main).count() == 5

    # Adjustment IN +3, OUT -2 -> 27
    record_stock_movement(variant=v7, location=main, movement_type=StockMovement.MovementType.ADJUSTMENT, direction=StockMovement.Direction.IN, quantity=3, reason="Found shelf", user=owner_user)
    record_stock_movement(variant=v7, location=main, movement_type=StockMovement.MovementType.ADJUSTMENT, direction=StockMovement.Direction.OUT, quantity=2, reason="Sample", user=owner_user)
    assert v7.current_stock_at(main) == 27
    assert AuditLog.objects.filter(model_name="StockMovement", action=AuditLog.Action.ADJUST).count() >= 3

    # Transfer 7 from MAIN -> QAGD: MAIN=20, QAGD=7, total=27
    trf = transfer_stock(from_location=main, to_location=godown, items_data=[{"variant": v7, "quantity": 7}], user=owner_user)
    assert trf.transfer_number.startswith("TRF-")
    assert v7.current_stock_at(main) == 20
    assert v7.current_stock_at(godown) == 7
    assert v7.current_stock == 27

    # Purchase Return 2 from QAGD -> MAIN=20, QAGD=5, total=25
    record_stock_movement(variant=v7, location=godown, movement_type=StockMovement.MovementType.PURCHASE_RETURN, quantity=2, reference="PRET-QA", user=owner_user)
    assert v7.current_stock_at(godown) == 5
    assert v7.current_stock == 25

    # Physical stock count at MAIN: counted 18 vs system 20 -> discrepancy -2, final MAIN=18
    sc = record_stock_count(location=main, items_data=[{"variant": v7, "counted_quantity": 18}], user=owner_user, notes="Cycle count")
    item_cnt = sc.items.get(variant=v7)
    assert item_cnt.system_quantity == 20
    assert item_cnt.counted_quantity == 18
    assert item_cnt.variance_quantity == -2
    assert v7.current_stock_at(main) == 18
    assert v7.current_stock == 23

    # Low-stock alert check: v8 stock 0 <= reorder_level 3; v7 stock 23 > 5
    low_skus = set(ProductVariant.objects.low_stock().values_list("sku", flat=True))
    assert "QA-RUN-8-WHT" in low_skus
    assert "QA-RUN-7-BLK" not in low_skus

    # Negative stock prevention & rollback
    store_settings.allow_negative_stock = False
    store_settings.save()
    before_count = StockMovement.objects.filter(variant=v7).count()
    with pytest.raises(InsufficientStockError):
        create_invoice(
            customer=cust,
            location=main,
            items_data=[{"variant": v7, "quantity": 5}, {"variant": v7, "quantity": 99}],
            user=owner_user,
            issue_now=True,
        )
    # Rolled back cleanly: no partial deduction
    assert StockMovement.objects.filter(variant=v7).count() == before_count
    assert v7.current_stock_at(main) == 18


@pytest.mark.django_db(transaction=True)
def test_phase3_concurrent_stock_and_invoice_sequence(owner_user, store_settings, qa_catalog):
    """Concurrent billing against exact available stock must not oversell when negative stock disallowed."""
    store_settings.allow_negative_stock = False
    store_settings.save()
    v8 = qa_catalog["v8"]
    main = qa_catalog["main"]
    record_stock_movement(variant=v8, location=main, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=5, user=owner_user)
    c1 = Customer.objects.create(name="Conc A", customer_type=Customer.CustomerType.RETAIL, phone="9000001011", state_code="MH")
    c2 = Customer.objects.create(name="Conc B", customer_type=Customer.CustomerType.RETAIL, phone="9000001012", state_code="MH")

    def attempt_sale(cust):
        connection.close()
        try:
            inv = create_invoice(customer=cust, location=main, items_data=[{"variant": v8, "quantity": 4}], user=owner_user, issue_now=True)
            return ("OK", inv.invoice_number)
        except Exception as exc:
            return ("ERR", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt_sale, [c1, c2]))

    ok_count = sum(1 for r in results if r[0] == "OK")
    # Under SQLite concurrent writes may serialize or raise OperationalError on lock, but NEVER 2 OK oversells (-3 stock)!
    assert ok_count <= 1
    assert v8.current_stock_at(main) >= 1


# ---------------------------------------------------------------------------
# PHASE 4: COMPLETE PURCHASE WORKFLOW TESTS
# ---------------------------------------------------------------------------
def test_phase4_purchase_lifecycle_partial_damage_missing_returns_and_payments(db, owner_user, qa_catalog):
    v7 = qa_catalog["v7"]
    main = qa_catalog["main"]
    sup = Supplier.objects.create(name="QA Footwear Factory", phone="9811112222", opening_balance=Decimal("500.00"), payment_terms_days=30)

    # 1. PO Create + modify draft/ordered before receipt
    po = create_purchase_order(
        supplier=sup,
        order_date=timezone.localdate(),
        expected_date=timezone.localdate() + timedelta(days=7),
        location=main,
        items_data=[{"variant": v7, "quantity_ordered": 20, "unit_cost": Decimal("400.00"), "discount_amount": Decimal("200.00"), "tax_rate": Decimal("12.00")}],
        user=owner_user,
    )
    # Manual PO math: gross = 20*400 = 8000; disc = 200; taxable = 7800; tax 12% = 936; total = 8736
    assert po.total_amount == Decimal("8736.00")
    assert po.status == PurchaseOrder.Status.ORDERED

    # Cancelled PO cannot receive goods
    po_cancel = create_purchase_order(
        supplier=sup,
        order_date=timezone.localdate(),
        expected_date=None,
        location=main,
        items_data=[{"variant": v7, "quantity_ordered": 5, "unit_cost": Decimal("400.00")}],
        user=owner_user,
    )
    po_cancel.status = PurchaseOrder.Status.CANCELLED
    po_cancel.save(update_fields=["status"])
    with pytest.raises(ValidationError):
        receive_goods(supplier=sup, purchase_order=po_cancel, items_data=[{"variant": v7, "quantity_received": 5, "unit_cost": Decimal("400.00")}], user=owner_user)

    # 2. Partial Receipt 1: accepted=12, damaged=1, missing=1 -> stock += 12 ONLY (not damaged/missing!)
    grn1 = receive_goods(
        supplier=sup,
        purchase_order=po,
        supplier_invoice_no="SUP-INV-101",
        received_date=timezone.localdate() - timedelta(days=40),
        items_data=[
            {
                "variant": v7,
                "quantity_received": 12,
                "damaged_quantity": 1,
                "missing_quantity": 1,
                "unit_cost": Decimal("400.00"),
                "discount_amount": Decimal("100.00"),
                "tax_rate": Decimal("12.00"),
            }
        ],
        user=owner_user,
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.PARTIAL
    # GRN1 manual math: gross=12*400=4800 - 100 = 4700; 12% tax=564; total=5264.00
    assert grn1.total_amount == Decimal("5264.00")
    assert v7.current_stock_at(main) == 12
    assert StockMovement.objects.filter(reference=f"GRN:{grn1.grn_number}").count() == 1

    # 3. Receipt 2 completes PO: accepted=8 -> total received=20 >= ordered 20 -> PO RECEIVED
    grn2 = receive_goods(
        supplier=sup,
        purchase_order=po,
        supplier_invoice_no="SUP-INV-102",
        items_data=[
            {
                "variant": v7,
                "quantity_received": 8,
                "unit_cost": Decimal("400.00"),
                "discount_amount": Decimal("0.00"),
                "tax_rate": Decimal("12.00"),
            }
        ],
        user=owner_user,
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.RECEIVED
    # GRN2: 8*400=3200 + 12% (384) = 3584.00; total stock = 12+8 = 20 (no double counting!)
    assert grn2.total_amount == Decimal("3584.00")
    assert v7.current_stock_at(main) == 20

    # Received PO blocks extra receipts
    with pytest.raises(ValidationError):
        receive_goods(supplier=sup, purchase_order=po, items_data=[{"variant": v7, "quantity_received": 1, "unit_cost": Decimal("400.00")}], user=owner_user)

    # 4. Purchase Return 2 pairs @ 400 = 800 -> stock=18
    pret = create_purchase_return(
        supplier=sup,
        goods_receipt=grn2,
        items_data=[{"variant": v7, "quantity": 2, "unit_cost": Decimal("400.00")}],
        reason="Sole defect",
        user=owner_user,
    )
    assert pret.total_amount == Decimal("800.00")
    assert v7.current_stock_at(main) == 18

    # 5. Multiple Supplier Payments (partial + auto-allocate + aging)
    sp1 = record_supplier_payment(supplier=sup, goods_receipt=grn1, amount=Decimal("2000.00"), payment_mode="BANK", user=owner_user)
    sp2 = record_supplier_payment(supplier=sup, amount=Decimal("4000.00"), payment_mode="KNET", auto_allocate=True, user=owner_user)
    grn1.refresh_from_db()
    grn2.refresh_from_db()
    # sp1 paid 2000 on grn1 (leaving 3264); sp2 auto-allocated 3264 to finish grn1 and 736 onto grn2
    assert sp1.amount == Decimal("2000.00")
    assert grn1.balance_due == Decimal("0.00")
    assert grn2.amount_paid == Decimal("736.00")
    assert grn2.balance_due == Decimal("2848.00")

    # Manual supplier outstanding balance:
    # Opening (500) + GRN1 (5264) + GRN2 (3584) - PRET (800) - SP1 (2000) - SP2 (4000) = 2548.00
    assert sup.outstanding_balance == Decimal("2548.00")

    aging = [r for r in get_supplier_aging() if r["supplier"].pk == sup.pk]
    assert len(aging) == 1
    assert aging[0]["b_0_30"] == Decimal("2848.00")  # grn2 was received today; grn1 is paid off


def test_phase4_grn_single_row_formset_post_with_untouched_extra_row(db, client, storekeeper_user, qa_catalog):
    """Submitting /purchases/receipts/new/ with Row 1 filled and untouched Row 2 must succeed cleanly."""
    v7 = qa_catalog["v7"]
    main = qa_catalog["main"]
    sup = Supplier.objects.create(name="QA Single Row Supplier", phone="9811113333")
    client.force_login(storekeeper_user)
    resp = client.post(
        reverse("purchases:grn_create"),
        {
            "supplier": sup.pk,
            "location": main.pk,
            "received_date": timezone.localdate().isoformat(),
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-variant": v7.pk,
            "items-0-quantity": "3",
            "items-0-damaged_quantity": "",
            "items-0-missing_quantity": "",
            "items-0-unit_cost": "400.00",
            "items-1-variant": "",
            "items-1-quantity": "",
            "items-1-damaged_quantity": "",
            "items-1-missing_quantity": "",
            "items-1-unit_cost": "",
        },
    )
    assert resp.status_code == 302
    assert GoodsReceipt.objects.filter(supplier=sup).count() == 1
    assert v7.current_stock_at(main) == 3


# ---------------------------------------------------------------------------
# PHASE 5: CUSTOMER LIFECYCLE & LEDGER TESTS
# ---------------------------------------------------------------------------
def test_phase5_customer_lifecycle_opening_credit_limit_soft_delete_and_aging(db, client, owner_user, qa_catalog):
    v7 = qa_catalog["v7"]
    record_stock_movement(variant=v7, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=50, user=owner_user)

    c_ws = Customer.objects.create(
        name="QA Wholesale Buyer",
        customer_type=Customer.CustomerType.WHOLESALE,
        phone="9820012345",
        gstin="27AAACA1234A1Z5",
        address="Block 4 Salmiya",
        state_code="KW",
        credit_limit=Decimal("5000.00"),
        payment_terms_days=15,
        opening_balance=Decimal("1000.00"),
    )
    assert c_ws.outstanding_balance == Decimal("1000.00")

    # Invoice aged 45 days back (672.00) -> 31-60 bucket
    old_date = timezone.localdate() - timedelta(days=45)
    inv_old = create_invoice(
        customer=c_ws,
        items_data=[{"variant": v7, "quantity": 1, "unit_price": Decimal("600.00")}],
        invoice_date=old_date,
        user=owner_user,
        issue_now=True,
    )
    assert inv_old.grand_total == Decimal("672.00")
    assert c_ws.outstanding_balance == Decimal("1672.00")

    # Aging verifies 31-60 days bucket
    aging = [a for a in get_customer_aging() if a["customer"].pk == c_ws.pk]
    assert aging and aging[0]["b_31_60"] == Decimal("672.00")

    # Credit limit check: 1672 + 4000 > 5000 -> CreditLimitExceededError unless overridden
    from apps.invoicing.services import CreditLimitExceededError

    with pytest.raises(CreditLimitExceededError):
        create_invoice(
            customer=c_ws,
            items_data=[{"variant": v7, "quantity": 7, "unit_price": Decimal("600.00")}],
            user=owner_user,
            issue_now=True,
        )

    # Customer editing & soft delete preserves historical invoices
    client.force_login(owner_user)
    resp = client.post(reverse("customers:customer_delete", args=[c_ws.pk]))
    assert resp.status_code == 302
    assert not Customer.objects.filter(pk=c_ws.pk).exists()
    assert Customer.all_objects.filter(pk=c_ws.pk).exists()
    inv_old.refresh_from_db()
    assert inv_old.customer_id == c_ws.pk
