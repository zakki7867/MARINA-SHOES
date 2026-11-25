from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.models import StoreLocation
from apps.core.utils import money_round, next_sequence_number
from apps.inventory.models import StockMovement
from apps.inventory.services import record_stock_movement
from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    PurchaseReturnItem,
    SupplierPayment,
    SupplierPaymentAllocation,
)


def _next_doc_number(model_cls, prefix: str) -> str:
    return next_sequence_number(prefix, model_cls)


@transaction.atomic
def create_purchase_order(
    *,
    supplier,
    order_date,
    expected_date,
    items_data: list[dict],
    user,
    location: StoreLocation | None = None,
    notes: str = "",
) -> PurchaseOrder:
    if not items_data:
        raise ValidationError("Purchase order must contain at least one item.")
    resolved_loc = location or StoreLocation.get_default()
    po = PurchaseOrder.objects.create(
        po_number=_next_doc_number(PurchaseOrder, "PO"),
        supplier=supplier,
        location=resolved_loc,
        order_date=order_date or timezone.localdate(),
        expected_date=expected_date,
        status=PurchaseOrder.Status.ORDERED,
        notes=notes,
        created_by=user,
    )
    total = Decimal("0.00")
    disc_total = Decimal("0.00")
    tax_total = Decimal("0.00")
    for item in items_data:
        qty = int(item["quantity_ordered"])
        cost = money_round(item["unit_cost"])
        disc = money_round(item.get("discount_amount") or Decimal("0.00"))
        tax_rate = Decimal(str(item.get("tax_rate") or Decimal("0.00")))
        gross = money_round(Decimal(qty) * cost)
        taxable = max(money_round(gross - disc), Decimal("0.00"))
        tax_amt = money_round(taxable * tax_rate / Decimal("100.00"))
        line_total = money_round(taxable + tax_amt)
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            variant=item["variant"],
            quantity_ordered=qty,
            unit_cost=cost,
            discount_amount=disc,
            tax_rate=tax_rate,
            tax_amount=tax_amt,
            line_total=line_total,
        )
        total += line_total
        disc_total += disc
        tax_total += tax_amt
    po.discount_total = money_round(disc_total)
    po.tax_total = money_round(tax_total)
    po.total_amount = money_round(total)
    po.save(update_fields=["discount_total", "tax_total", "total_amount", "updated_at"])
    return po


@transaction.atomic
def receive_goods(
    *,
    supplier,
    items_data: list[dict],
    user,
    purchase_order: PurchaseOrder | None = None,
    location: StoreLocation | None = None,
    received_date=None,
    due_date=None,
    supplier_invoice_no: str = "",
    notes: str = "",
    request=None,
) -> GoodsReceipt:
    """Create a GoodsReceipt and atomically record PURCHASE StockMovements for received salable items."""
    if not items_data:
        raise ValidationError("Goods receipt must include at least one item.")

    if purchase_order is not None:
        purchase_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if purchase_order.status == PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Cannot receive goods against a cancelled Purchase Order.")
        if purchase_order.status == PurchaseOrder.Status.RECEIVED:
            raise ValidationError("This Purchase Order has already been fully received.")

    resolved_loc = location or (purchase_order.location if purchase_order and purchase_order.location else StoreLocation.get_default())
    rec_date = received_date or timezone.localdate()
    resolved_due = due_date or (rec_date + timedelta(days=supplier.payment_terms_days))

    grn = GoodsReceipt.objects.create(
        grn_number=_next_doc_number(GoodsReceipt, "GRN"),
        purchase_order=purchase_order,
        supplier=supplier,
        location=resolved_loc,
        received_date=rec_date,
        due_date=resolved_due,
        supplier_invoice_no=supplier_invoice_no,
        notes=notes,
        received_by=user,
    )

    total = Decimal("0.00")
    disc_total = Decimal("0.00")
    tax_total = Decimal("0.00")
    for item in items_data:
        variant = item["variant"]
        qty = int(item["quantity_received"])
        damaged = int(item.get("damaged_quantity") or 0)
        missing = int(item.get("missing_quantity") or 0)
        cost = money_round(item["unit_cost"])
        disc = money_round(item.get("discount_amount") or Decimal("0.00"))
        tax_rate = Decimal(str(item.get("tax_rate") or Decimal("0.00")))
        gross = money_round(Decimal(qty) * cost)
        taxable = max(money_round(gross - disc), Decimal("0.00"))
        tax_amt = money_round(taxable * tax_rate / Decimal("100.00"))
        line_total = money_round(taxable + tax_amt)

        GoodsReceiptItem.objects.create(
            goods_receipt=grn,
            variant=variant,
            quantity_received=qty,
            damaged_quantity=damaged,
            missing_quantity=missing,
            unit_cost=cost,
            discount_amount=disc,
            tax_rate=tax_rate,
            tax_amount=tax_amt,
            line_total=line_total,
        )
        total += line_total
        disc_total += disc
        tax_total += tax_amt

        record_stock_movement(
            variant=variant,
            location=resolved_loc,
            movement_type=StockMovement.MovementType.PURCHASE,
            quantity=qty,
            reference=f"GRN:{grn.grn_number}",
            reason="Goods Received",
            notes=f"Received from {supplier.name}" + (f" (damaged: {damaged}, missing: {missing})" if (damaged or missing) else ""),
            user=user,
            request=request,
        )

    grn.discount_total = money_round(disc_total)
    grn.tax_total = money_round(tax_total)
    grn.total_amount = money_round(total)
    grn.save(update_fields=["discount_total", "tax_total", "total_amount", "updated_at"])

    if purchase_order is not None:
        ordered_qty = purchase_order.items.aggregate(total=Sum("quantity_ordered"))["total"] or 0
        received_qty = (
            GoodsReceiptItem.objects.filter(goods_receipt__purchase_order=purchase_order)
            .aggregate(total=Sum("quantity_received"))["total"]
            or 0
        )
        purchase_order.status = (
            PurchaseOrder.Status.RECEIVED if received_qty >= ordered_qty else PurchaseOrder.Status.PARTIAL
        )
        purchase_order.save(update_fields=["status", "updated_at"])

    log_audit(
        user=user,
        action=AuditLog.Action.CREATE,
        instance=grn,
        changes={"supplier": supplier.name, "location": resolved_loc.code, "total_amount": grn.total_amount},
        request=request,
        notes="Goods received and stock updated",
    )
    return grn


@transaction.atomic
def create_purchase_return(
    *,
    supplier,
    items_data: list[dict],
    user,
    goods_receipt: GoodsReceipt | None = None,
    location: StoreLocation | None = None,
    return_date=None,
    reason: str = "",
    request=None,
) -> PurchaseReturn:
    if not items_data:
        raise ValidationError("Purchase return must include at least one item.")

    resolved_loc = location or (goods_receipt.location if goods_receipt and goods_receipt.location else StoreLocation.get_default())
    pr = PurchaseReturn.objects.create(
        return_number=_next_doc_number(PurchaseReturn, "PRET"),
        supplier=supplier,
        goods_receipt=goods_receipt,
        location=resolved_loc,
        return_date=return_date or timezone.localdate(),
        reason=reason,
        created_by=user,
    )

    total = Decimal("0.00")
    for item in items_data:
        variant = item["variant"]
        qty = int(item["quantity"])
        if qty <= 0:
            raise ValidationError("Return quantity must be positive.")
        if goods_receipt is not None:
            grn_qty = (
                goods_receipt.items.filter(variant=variant).aggregate(total=Sum("quantity_received"))["total"] or 0
            )
            already_ret = (
                PurchaseReturnItem.objects.filter(purchase_return__goods_receipt=goods_receipt, variant=variant)
                .aggregate(total=Sum("quantity"))["total"]
                or 0
            )
            if grn_qty == 0:
                raise ValidationError(f"Variant {variant.sku} was not received on GRN {goods_receipt.grn_number}.")
            if (already_ret + qty) > grn_qty:
                raise ValidationError(
                    f"Total return qty ({already_ret + qty}) exceeds received qty ({grn_qty}) for {variant.sku}."
                )
        cost = money_round(item["unit_cost"])
        line_total = money_round(Decimal(qty) * cost)
        PurchaseReturnItem.objects.create(
            purchase_return=pr,
            variant=variant,
            quantity=qty,
            unit_cost=cost,
            line_total=line_total,
        )
        total += line_total

        record_stock_movement(
            variant=variant,
            location=resolved_loc,
            movement_type=StockMovement.MovementType.PURCHASE_RETURN,
            quantity=qty,
            reference=f"PRET:{pr.return_number}",
            reason=reason or "Purchase Return",
            notes=reason or f"Returned to {supplier.name}",
            user=user,
            request=request,
        )

    pr.total_amount = money_round(total)
    pr.save(update_fields=["total_amount", "updated_at"])
    return pr


@transaction.atomic
def record_supplier_payment(
    *,
    supplier,
    amount: Decimal,
    payment_mode: str,
    user,
    goods_receipt: GoodsReceipt | None = None,
    auto_allocate: bool = True,
    payment_date=None,
    reference_no: str = "",
    notes: str = "",
    request=None,
) -> SupplierPayment:
    rounded_amount = money_round(amount)
    if rounded_amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than zero.")
    if not supplier.is_active or supplier.is_deleted:
        raise ValidationError("Cannot record payment for an inactive or archived supplier.")

    payment = SupplierPayment.objects.create(
        payment_number=_next_doc_number(SupplierPayment, "SPAY"),
        supplier=supplier,
        payment_date=payment_date or timezone.localdate(),
        amount=rounded_amount,
        payment_mode=payment_mode,
        reference_no=reference_no,
        notes=notes,
        created_by=user,
    )

    remaining = rounded_amount
    targets: list[GoodsReceipt] = []
    if goods_receipt is not None:
        if goods_receipt.supplier_id != supplier.id:
            raise ValidationError("Selected GRN does not belong to this supplier.")
        targets = [GoodsReceipt.objects.select_for_update().get(pk=goods_receipt.pk)]
    elif auto_allocate:
        targets = [
            grn
            for grn in GoodsReceipt.objects.select_for_update().filter(supplier=supplier).order_by("received_date", "id")
            if grn.balance_due > Decimal("0.00")
        ]

    for grn in targets:
        if remaining <= Decimal("0.00"):
            break
        due = grn.balance_due
        if due <= Decimal("0.00"):
            continue
        alloc = min(remaining, due)
        SupplierPaymentAllocation.objects.create(payment=payment, goods_receipt=grn, amount=alloc)
        grn.amount_paid = money_round(grn.amount_paid + alloc)
        grn.save(update_fields=["amount_paid", "updated_at"])
        remaining = money_round(remaining - alloc)

    log_audit(
        user=user,
        action=AuditLog.Action.CREATE,
        instance=payment,
        changes={"supplier": supplier.name, "amount": rounded_amount, "mode": payment_mode},
        request=request,
        notes="Recorded supplier payment",
    )
    return payment
