import csv
import io
from decimal import Decimal, InvalidOperation
from zipfile import BadZipFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
import openpyxl

from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.models import StoreLocation, StoreSettings
from apps.core.utils import money_round, next_sequence_number
from .models import (
    Brand,
    Category,
    Product,
    ProductVariant,
    StockBalance,
    StockCount,
    StockCountItem,
    StockMovement,
    StockTransfer,
    StockTransferItem,
)


class InsufficientStockError(ValidationError):
    """Raised when a stock deduction would result in negative stock while disallowed."""


@transaction.atomic
def record_stock_movement(
    *,
    variant: ProductVariant,
    movement_type: str,
    quantity: int,
    user,
    location: StoreLocation | None = None,
    direction: str | None = None,
    reference: str = "",
    reason: str = "",
    notes: str = "",
    request=None,
    allow_negative_override: bool = False,
) -> StockMovement:
    """
    Atomically record a StockMovement and synchronize StockBalance after locking
    the variant-location balance row and enforcing negative-stock policy.
    """
    if quantity <= 0:
        raise ValidationError("Quantity must be a positive integer.")

    resolved_location = location or StoreLocation.get_default()
    locked_variant = ProductVariant.all_objects.select_for_update().get(pk=variant.pk)
    balance_obj, created = StockBalance.objects.get_or_create(
        variant=locked_variant,
        location=resolved_location,
        defaults={"quantity": 0},
    )
    balance_obj = StockBalance.objects.select_for_update().get(pk=balance_obj.pk)
    if created:
        existing_sum = locked_variant.stock_movements.filter(location=resolved_location).aggregate(
            total=Coalesce(Sum("signed_quantity"), Value(0))
        )["total"]
        balance_obj.quantity = int(existing_sum)

    if movement_type in StockMovement.INBOUND_TYPES:
        resolved_direction = StockMovement.Direction.IN
    elif movement_type in StockMovement.OUTBOUND_TYPES:
        resolved_direction = StockMovement.Direction.OUT
    elif movement_type == StockMovement.MovementType.ADJUSTMENT:
        if direction not in (StockMovement.Direction.IN, StockMovement.Direction.OUT):
            raise ValidationError("Adjustment requires direction IN or OUT.")
        resolved_direction = direction
    else:
        raise ValidationError(f"Unsupported movement type: {movement_type}")

    delta = quantity if resolved_direction == StockMovement.Direction.IN else -quantity

    if delta < 0 and not allow_negative_override:
        store_settings = StoreSettings.get_settings()
        if not store_settings.allow_negative_stock and (balance_obj.quantity + delta) < 0:
            raise InsufficientStockError(
                f"Insufficient stock for {locked_variant.sku} at {resolved_location.code}: "
                f"available {balance_obj.quantity}, requested {quantity}."
            )

    movement = StockMovement.objects.create(
        variant=locked_variant,
        location=resolved_location,
        movement_type=movement_type,
        direction=resolved_direction,
        quantity=quantity,
        reference=reference,
        reason=reason,
        notes=notes,
        user=user,
    )

    balance_obj.quantity += delta
    balance_obj.save(update_fields=["quantity", "updated_at"])

    if movement_type in (
        StockMovement.MovementType.ADJUSTMENT,
        StockMovement.MovementType.DAMAGE,
        StockMovement.MovementType.TRANSFER_IN,
        StockMovement.MovementType.TRANSFER_OUT,
    ):
        log_audit(
            user=user,
            action=AuditLog.Action.ADJUST,
            instance=movement,
            changes={
                "variant_sku": locked_variant.sku,
                "location": resolved_location.code,
                "movement_type": movement_type,
                "signed_quantity": movement.signed_quantity,
                "reference": reference,
                "reason": reason,
            },
            request=request,
            notes=notes or reason or f"Stock {movement_type.lower()} for {locked_variant.sku}",
        )

    return movement


@transaction.atomic
def transfer_stock(
    *,
    from_location: StoreLocation,
    to_location: StoreLocation,
    items_data: list[dict],
    user,
    transfer_date=None,
    notes: str = "",
    request=None,
) -> StockTransfer:
    if from_location.pk == to_location.pk:
        raise ValidationError("Source and destination locations must be different.")
    if not items_data:
        raise ValidationError("Stock transfer must contain at least one item.")

    trf_num = next_sequence_number("TRF", StockTransfer)

    transfer = StockTransfer.objects.create(
        transfer_number=trf_num,
        from_location=from_location,
        to_location=to_location,
        transfer_date=transfer_date or timezone.localdate(),
        notes=notes,
        created_by=user,
    )

    for row in items_data:
        variant = row["variant"]
        qty = int(row["quantity"])
        StockTransferItem.objects.create(transfer=transfer, variant=variant, quantity=qty)
        record_stock_movement(
            variant=variant,
            location=from_location,
            movement_type=StockMovement.MovementType.TRANSFER_OUT,
            quantity=qty,
            reference=f"TRF:{transfer.transfer_number}",
            reason=f"Transfer to {to_location.code}",
            notes=notes,
            user=user,
            request=request,
        )
        record_stock_movement(
            variant=variant,
            location=to_location,
            movement_type=StockMovement.MovementType.TRANSFER_IN,
            quantity=qty,
            reference=f"TRF:{transfer.transfer_number}",
            reason=f"Transfer from {from_location.code}",
            notes=notes,
            user=user,
            request=request,
        )

    return transfer


@transaction.atomic
def record_stock_count(
    *,
    location: StoreLocation,
    items_data: list[dict],
    user,
    count_date=None,
    notes: str = "",
    request=None,
) -> StockCount:
    if not items_data:
        raise ValidationError("Stock count must contain at least one item.")

    cnt_num = next_sequence_number("CNT", StockCount)

    sc = StockCount.objects.create(
        count_number=cnt_num,
        location=location,
        count_date=count_date or timezone.localdate(),
        notes=notes,
        created_by=user,
    )

    for row in items_data:
        variant = row["variant"]
        counted = int(row["counted_quantity"])
        if counted < 0:
            raise ValidationError("Counted quantity cannot be negative.")
        sys_qty = variant.current_stock_at(location)
        variance = counted - sys_qty
        StockCountItem.objects.create(
            stock_count=sc,
            variant=variant,
            system_quantity=sys_qty,
            counted_quantity=counted,
            variance_quantity=variance,
        )
        if variance != 0:
            record_stock_movement(
                variant=variant,
                location=location,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                direction=StockMovement.Direction.IN if variance > 0 else StockMovement.Direction.OUT,
                quantity=abs(variance),
                reference=f"CNT:{sc.count_number}",
                reason="Physical Stock Count Variance",
                notes=notes,
                user=user,
                request=request,
            )

    return sc


@transaction.atomic
def import_products_from_file(uploaded_file, user, location: StoreLocation | None = None, request=None) -> dict:
    """Import products and variants from CSV or Excel (.xlsx). Optionally sets opening stock."""
    if uploaded_file.size > 5 * 1024 * 1024:
        raise ValidationError("Import file size cannot exceed 5 MB.")
    name = uploaded_file.name.lower()
    rows: list[dict] = []

    if name.endswith(".csv"):
        try:
            decoded = uploaded_file.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV file must be UTF-8 encoded.") from exc
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
    elif name.endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(uploaded_file, data_only=True)
        except (BadZipFile, OSError, ValueError) as exc:
            raise ValidationError("Invalid or corrupted .xlsx file.") from exc
        ws = wb.active
        raw_rows = list(ws.iter_rows(values_only=True))
        if not raw_rows:
            raise ValidationError("Uploaded Excel file is empty.")
        headers = [str(h or "").strip() for h in raw_rows[0]]
        for r in raw_rows[1:]:
            if any(cell is not None and str(cell).strip() != "" for cell in r):
                rows.append({headers[i]: r[i] for i in range(len(headers)) if i < len(r)})
    else:
        raise ValidationError("Unsupported file format. Upload .csv or .xlsx.")

    created_variants = 0
    updated_variants = 0
    loc = location or StoreLocation.get_default()

    for idx, r in enumerate(rows, start=2):
        norm = {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in r.items()}
        prod_name = norm.get("product") or norm.get("product name")
        cat_name = norm.get("category") or "General"
        brand_name = norm.get("brand") or "Marina"
        size = norm.get("size") or "Free"
        color = norm.get("color") or "Standard"
        sku = norm.get("sku") or ""
        if not prod_name:
            raise ValidationError(f"Row {idx}: missing product name.")

        try:
            tax_rate = Decimal(norm.get("tax%", "") or norm.get("gst%", "") or norm.get("tax rate", "") or "0.00")
            cost = money_round(norm.get("cost") or norm.get("cost price") or "0")
            wholesale = money_round(norm.get("wholesale") or norm.get("wholesale price") or "0")
            retail = money_round(norm.get("retail") or norm.get("retail price") or "0")
            reorder = int(float(norm.get("reorder level") or norm.get("reorder") or 5))
            opening = int(float(norm.get("opening stock") or 0))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"Row {idx}: invalid numeric value ({exc}).") from exc

        if any(p < Decimal("0.00") for p in (cost, wholesale, retail, tax_rate)) or reorder < 0 or opening < 0:
            raise ValidationError(f"Row {idx}: prices, tax%, reorder level, and opening stock cannot be negative.")

        gender_val = (norm.get("gender") or "UNISEX").upper()
        if gender_val not in Product.Gender.values:
            gender_val = Product.Gender.UNISEX

        cat, _ = Category.objects.get_or_create(name=cat_name)
        brand, _ = Brand.objects.get_or_create(name=brand_name)
        product, _ = Product.objects.get_or_create(
            name=prod_name,
            brand=brand,
            defaults={
                "category": cat,
                "hsn_code": norm.get("itemcode", "") or norm.get("item code", "") or norm.get("hsn", "") or norm.get("hsn code", ""),
                "tax_rate": tax_rate,
                "gender": gender_val,
            },
        )

        barcode = norm.get("barcode") or None

        existing = ProductVariant.all_objects.filter(sku=sku).first() if sku else None
        if existing:
            existing.cost_price = cost
            existing.wholesale_price = wholesale
            existing.retail_price = retail
            existing.reorder_level = reorder
            if barcode:
                existing.barcode = barcode
            existing.is_deleted = False
            existing.save()
            variant = existing
            updated_variants += 1
        else:
            variant = ProductVariant.objects.create(
                product=product,
                size=size,
                color=color,
                sku=sku,
                barcode=barcode,
                cost_price=cost,
                wholesale_price=wholesale,
                retail_price=retail,
                reorder_level=reorder,
            )
            created_variants += 1

        if opening > 0:
            record_stock_movement(
                variant=variant,
                location=loc,
                movement_type=StockMovement.MovementType.OPENING_STOCK,
                quantity=opening,
                reference="IMPORT",
                reason="Catalog import opening stock",
                user=user,
                request=request,
            )

    return {"created": created_variants, "updated": updated_variants}


def get_low_stock_variants():
    return ProductVariant.objects.select_related("product", "product__brand", "product__category").low_stock()


def get_out_of_stock_count() -> int:
    return ProductVariant.objects.with_stock().filter(is_active=True, calculated_stock__lte=0).count()


def get_total_stock_value() -> Decimal:
    """Compute total inventory valuation at cost price from active variants."""
    total = Decimal("0.000")
    for v in ProductVariant.objects.with_stock().only("cost_price"):
        qty = max(int(v.calculated_stock or 0), 0)
        total += Decimal(qty) * v.cost_price
    return money_round(total)


def get_stock_value_by_category() -> list[dict]:
    """Compute stock units and cost valuation grouped by product category."""
    buckets: dict[str, dict] = {}
    grand_total = Decimal("0.000")
    qs = ProductVariant.objects.select_related("product__category").with_stock().only(
        "cost_price", "product__category__name"
    )
    for v in qs:
        qty = max(int(v.calculated_stock or 0), 0)
        val = Decimal(qty) * v.cost_price
        cat_name = v.product.category.name if v.product and v.product.category else "Uncategorized"
        if cat_name not in buckets:
            buckets[cat_name] = {"category": cat_name, "units": 0, "value": Decimal("0.000")}
        buckets[cat_name]["units"] += qty
        buckets[cat_name]["value"] += val
        grand_total += val

    rows = []
    for row in buckets.values():
        row["value"] = money_round(row["value"])
        row["pct"] = round(float((row["value"] / grand_total) * Decimal("100")), 1) if grand_total > Decimal("0.000") else 0.0
        rows.append(row)
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows
