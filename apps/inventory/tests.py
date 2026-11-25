from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.core.models import StoreLocation
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockBalance, StockMovement
from apps.inventory.services import (
    InsufficientStockError,
    get_low_stock_variants,
    import_products_from_file,
    record_stock_count,
    record_stock_movement,
    transfer_stock,
)


@pytest.fixture
def sample_variant(db):
    cat = Category.objects.create(name="Loafers")
    brand = Brand.objects.create(name="Marina Classic")
    product = Product.objects.create(
        name="Suede Penny Loafer",
        style_code="MS-LP-01",
        category=cat,
        brand=brand,
        hsn_code="6403",
        tax_rate=Decimal("12.00"),
    )
    return ProductVariant.objects.create(
        product=product,
        size="42",
        color="Tan",
        sku="MS-LP01-42-TAN",
        barcode="890123456001",
        cost_price=Decimal("15.00"),
        wholesale_price=Decimal("22.00"),
        retail_price=Decimal("30.00"),
        reorder_level=5,
    )


@pytest.mark.django_db
def test_stock_calculation_from_movements(sample_variant, storekeeper_user, store_settings):
    assert sample_variant.current_stock == 0

    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=20,
        reference="GRN-001",
        user=storekeeper_user,
    )
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.SALE,
        quantity=7,
        reference="INV-001",
        user=storekeeper_user,
    )
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.SALE_RETURN,
        quantity=2,
        reference="RET-001",
        user=storekeeper_user,
    )
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.DAMAGE,
        quantity=1,
        reference="DMG-001",
        user=storekeeper_user,
    )
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.PURCHASE_RETURN,
        quantity=3,
        reference="PRET-001",
        user=storekeeper_user,
    )
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.ADJUSTMENT,
        direction=StockMovement.Direction.IN,
        quantity=4,
        reference="ADJ-001",
        user=storekeeper_user,
    )

    # 20 - 7 + 2 - 1 - 3 + 4 = 15
    assert sample_variant.current_stock == 15
    assert AuditLog.objects.filter(model_name="StockMovement", action=AuditLog.Action.ADJUST).count() == 2


@pytest.mark.django_db
def test_prevent_negative_stock_unless_owner_allows(sample_variant, storekeeper_user, store_settings):
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=5,
        user=storekeeper_user,
    )

    with pytest.raises(InsufficientStockError):
        record_stock_movement(
            variant=sample_variant,
            movement_type=StockMovement.MovementType.SALE,
            quantity=6,
            user=storekeeper_user,
        )
    assert sample_variant.current_stock == 5

    store_settings.allow_negative_stock = True
    store_settings.save()

    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.SALE,
        quantity=6,
        user=storekeeper_user,
    )
    assert sample_variant.current_stock == -1


@pytest.mark.django_db
def test_stock_movement_is_immutable(sample_variant, storekeeper_user, store_settings):
    mv = record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=10,
        user=storekeeper_user,
    )
    mv.quantity = 99
    with pytest.raises(ValidationError):
        mv.save()
    with pytest.raises(ValidationError):
        mv.delete()


@pytest.mark.django_db
def test_low_stock_alert_and_soft_delete(sample_variant, storekeeper_user, store_settings):
    record_stock_movement(
        variant=sample_variant,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity=5,
        user=storekeeper_user,
    )
    assert get_low_stock_variants().filter(pk=sample_variant.pk).exists()

    sample_variant.soft_delete()
    assert not ProductVariant.objects.filter(pk=sample_variant.pk).exists()
    assert ProductVariant.all_objects.filter(pk=sample_variant.pk).exists()


@pytest.mark.django_db
def test_inventory_role_permissions(client, api_client, sample_variant, billing_user, storekeeper_user):
    # Billing staff can read inventory, cannot create/adjust
    client.force_login(billing_user)
    assert client.get(reverse("inventory:product_list")).status_code == 200
    assert client.get(reverse("inventory:product_create")).status_code == 403
    assert client.get(reverse("inventory:stock_movement")).status_code == 403

    api_client.force_authenticate(user=billing_user)
    assert api_client.get("/api/v1/inventory/variants/").status_code == 200
    assert api_client.post("/api/v1/inventory/stock-movements/", {"variant": sample_variant.pk, "movement_type": "PURCHASE", "quantity": 5}).status_code == 403

    # Storekeeper can access and post stock movements
    client.force_login(storekeeper_user)
    assert client.get(reverse("inventory:stock_movement")).status_code == 200
    api_client.force_authenticate(user=storekeeper_user)
    resp = api_client.post(
        "/api/v1/inventory/stock-movements/",
        {"variant": sample_variant.pk, "movement_type": "PURCHASE", "quantity": 5},
    )
    assert resp.status_code == 201
    assert sample_variant.current_stock == 5


@pytest.mark.django_db
def test_multi_location_transfer_stock_count_and_csv_import(sample_variant, storekeeper_user, store_settings):
    main = StoreLocation.get_default()
    warehouse = StoreLocation.objects.create(code="WH1", name="Bhiwandi Warehouse", location_type=StoreLocation.LocationType.WAREHOUSE)

    record_stock_movement(
        variant=sample_variant,
        location=main,
        movement_type=StockMovement.MovementType.OPENING_STOCK,
        quantity=12,
        user=storekeeper_user,
    )
    assert sample_variant.current_stock_at(main) == 12

    transfer_stock(
        from_location=main,
        to_location=warehouse,
        items_data=[{"variant": sample_variant, "quantity": 5}],
        user=storekeeper_user,
    )
    assert sample_variant.current_stock_at(main) == 7
    assert sample_variant.current_stock_at(warehouse) == 5
    assert sample_variant.current_stock == 12
    assert StockBalance.objects.get(variant=sample_variant, location=main).quantity == 7

    # Physical count at main finds 6 instead of 7 -> posts -1 adjustment
    sc = record_stock_count(
        location=main,
        items_data=[{"variant": sample_variant, "counted_quantity": 6}],
        user=storekeeper_user,
    )
    assert sample_variant.current_stock_at(main) == 6
    assert sc.items.first().variance_quantity == -1

    # CSV import creates product, auto-generates SKU if blank, and posts opening stock
    csv_bytes = (
        b"Product,Brand,Category,Gender,HSN,GST%,Size,Color,SKU,Cost,Wholesale,Retail,Reorder Level,Opening Stock\n"
        b"Metro Slip-On,Marina,Loafers,MEN,6403,12,41,Black,,100,150,220,3,8\n"
    )
    res = import_products_from_file(SimpleUploadedFile("catalog.csv", csv_bytes, content_type="text/csv"), user=storekeeper_user)
    assert res["created"] == 1
    imported = ProductVariant.objects.get(product__name="Metro Slip-On")
    assert imported.sku != ""
    assert imported.current_stock == 8


@pytest.mark.django_db
def test_catalog_import_rejects_corrupted_xlsx_and_negative_prices(storekeeper_user, store_settings):
    bad_xlsx = SimpleUploadedFile("bad.xlsx", b"not-a-zip", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with pytest.raises(ValidationError):
        import_products_from_file(bad_xlsx, user=storekeeper_user)

    neg_csv = b"Product,Brand,Category,Size,Color,Cost,Wholesale,Retail\nBadShoe,Marina,Loafers,42,Black,-10,50,80\n"
    with pytest.raises(ValidationError):
        import_products_from_file(SimpleUploadedFile("neg.csv", neg_csv, content_type="text/csv"), user=storekeeper_user)
