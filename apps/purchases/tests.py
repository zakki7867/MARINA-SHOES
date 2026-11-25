from datetime import date
from decimal import Decimal
import pytest
from django.urls import reverse

from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.purchases.models import PurchaseOrder, Supplier
from apps.purchases.services import create_purchase_order, create_purchase_return, receive_goods, record_supplier_payment


@pytest.fixture
def purchase_setup(db):
    cat = Category.objects.create(name="Sandals")
    brand = Brand.objects.create(name="Al-Bahr")
    prod = Product.objects.create(name="Leather Sandal", category=cat, brand=brand, hsn_code="6403", tax_rate=Decimal("5.00"))
    variant = ProductVariant.objects.create(
        product=prod,
        size="43",
        color="Brown",
        sku="AB-SND-43-BRN",
        cost_price=Decimal("8.00"),
        wholesale_price=Decimal("12.00"),
        retail_price=Decimal("16.00"),
        reorder_level=5,
    )
    supplier = Supplier.objects.create(name="Kuwait Footwear Factory", phone="+96522223333", state_code="KW")
    return supplier, variant


@pytest.mark.django_db
def test_po_receive_goods_updates_stock_and_supplier_balance(purchase_setup, storekeeper_user, manager_user, store_settings):
    supplier, variant = purchase_setup

    po = create_purchase_order(
        supplier=supplier,
        order_date=date(2026, 11, 20),
        expected_date=None,
        items_data=[{"variant": variant, "quantity_ordered": 25, "unit_cost": Decimal("8.00")}],
        user=storekeeper_user,
    )
    assert po.total_amount == Decimal("200.00")
    assert po.status == PurchaseOrder.Status.ORDERED

    grn = receive_goods(
        supplier=supplier,
        purchase_order=po,
        items_data=[{"variant": variant, "quantity_received": 25, "unit_cost": Decimal("8.00")}],
        user=storekeeper_user,
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.RECEIVED
    assert variant.current_stock == 25
    assert StockMovement.objects.filter(variant=variant, movement_type=StockMovement.MovementType.PURCHASE, reference=f"GRN:{grn.grn_number}").exists()
    assert supplier.outstanding_balance == Decimal("200.00")

    create_purchase_return(
        supplier=supplier,
        goods_receipt=grn,
        items_data=[{"variant": variant, "quantity": 5, "unit_cost": Decimal("8.00")}],
        user=storekeeper_user,
        reason="Defective stitching",
    )
    assert variant.current_stock == 20
    assert supplier.outstanding_balance == Decimal("160.00")

    record_supplier_payment(
        supplier=supplier,
        amount=Decimal("100.00"),
        payment_mode="BANK",
        user=manager_user,
    )
    assert supplier.outstanding_balance == Decimal("60.00")


@pytest.mark.django_db
def test_purchases_role_permissions(client, billing_user, storekeeper_user, manager_user):
    client.force_login(billing_user)
    assert client.get(reverse("purchases:supplier_list")).status_code == 403
    assert client.get(reverse("purchases:grn_list")).status_code == 403

    client.force_login(storekeeper_user)
    assert client.get(reverse("purchases:supplier_list")).status_code == 200
    assert client.get(reverse("purchases:grn_list")).status_code == 200
    assert client.get(reverse("purchases:supplier_payments")).status_code == 403

    client.force_login(manager_user)
    assert client.get(reverse("purchases:supplier_payments")).status_code == 200


@pytest.mark.django_db
def test_partial_grn_with_tax_discount_damaged_and_payment_allocation(purchase_setup, storekeeper_user, manager_user, store_settings):
    supplier, variant = purchase_setup
    po = create_purchase_order(
        supplier=supplier,
        order_date=date(2026, 11, 20),
        expected_date=None,
        items_data=[{"variant": variant, "quantity_ordered": 20, "unit_cost": Decimal("100.00"), "discount_amount": Decimal("100.00"), "tax_rate": Decimal("12.00")}],
        user=storekeeper_user,
    )
    # 20*100 - 100 = 1900 + 12% (228) = 2128.00
    assert po.total_amount == Decimal("2128.00")

    # Partial receipt of 10 salable + 1 damaged + 1 missing
    grn = receive_goods(
        supplier=supplier,
        purchase_order=po,
        items_data=[{"variant": variant, "quantity_received": 10, "damaged_quantity": 1, "missing_quantity": 1, "unit_cost": Decimal("100.00"), "tax_rate": Decimal("12.00")}],
        user=storekeeper_user,
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.PARTIAL
    assert variant.current_stock == 10  # only salable qty added
    assert grn.total_amount == Decimal("1120.00")

    # Over-payment creates allocated + unallocated advance
    pay = record_supplier_payment(supplier=supplier, amount=Decimal("1500.00"), payment_mode="KNET", user=manager_user)
    grn.refresh_from_db()
    assert grn.balance_due == Decimal("0.00")
    assert pay.allocated_amount == Decimal("1120.00")
    assert pay.unallocated_amount == Decimal("380.00")


@pytest.mark.django_db
def test_cannot_receive_goods_on_received_or_cancelled_po(purchase_setup, storekeeper_user, store_settings):
    from django.core.exceptions import ValidationError
    supplier, variant = purchase_setup
    po = create_purchase_order(
        supplier=supplier,
        order_date=date(2026, 11, 20),
        expected_date=None,
        items_data=[{"variant": variant, "quantity_ordered": 5, "unit_cost": Decimal("8.00")}],
        user=storekeeper_user,
    )
    receive_goods(
        supplier=supplier,
        purchase_order=po,
        items_data=[{"variant": variant, "quantity_received": 5, "unit_cost": Decimal("8.00")}],
        user=storekeeper_user,
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.RECEIVED

    with pytest.raises(ValidationError):
        receive_goods(
            supplier=supplier,
            purchase_order=po,
            items_data=[{"variant": variant, "quantity_received": 1, "unit_cost": Decimal("8.00")}],
            user=storekeeper_user,
        )
