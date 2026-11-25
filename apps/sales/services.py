from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import StoreLocation, StoreSettings
from apps.core.utils import money_round, next_sequence_number
from apps.inventory.models import StockMovement
from apps.inventory.services import record_stock_movement
from apps.invoicing.services import create_credit_note, create_invoice, issue_refund
from .models import PriceList, SalesExchange, SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem


def _next_sales_num(model_cls, prefix: str) -> str:
    return next_sequence_number(prefix, model_cls)


@transaction.atomic
def create_sales_order(
    *,
    customer,
    items_data: list[dict],
    user,
    location: StoreLocation | None = None,
    price_list: str | None = None,
    tax_mode: str | None = None,
    invoice_discount: Decimal = Decimal("0.00"),
    order_date=None,
    notes: str = "",
) -> SalesOrder:
    if not items_data:
        raise ValidationError("Sales order must contain at least one item.")
    if not customer.is_active or customer.is_deleted:
        raise ValidationError("Cannot create a sales order for an inactive or archived customer.")

    store = StoreSettings.get_settings()
    resolved_loc = location or StoreLocation.get_default()
    resolved_price_list = price_list or (
        PriceList.WHOLESALE if customer.customer_type == customer.CustomerType.WHOLESALE else PriceList.RETAIL
    )
    resolved_tax_mode = tax_mode or store.default_tax_mode
    so = SalesOrder.objects.create(
        order_number=_next_sales_num(SalesOrder, "SO"),
        customer=customer,
        location=resolved_loc,
        price_list=resolved_price_list,
        tax_mode=resolved_tax_mode,
        order_date=order_date or timezone.localdate(),
        status=SalesOrder.Status.CONFIRMED,
        invoice_discount=money_round(invoice_discount),
        notes=notes,
        created_by=user,
    )

    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")

    for row in items_data:
        variant = row["variant"]
        if not variant.is_active or variant.is_deleted or variant.product.is_deleted or variant.product.status != "ACTIVE":
            raise ValidationError(f"Variant {variant.sku} is inactive or archived.")
        qty = int(row["quantity"])
        if qty <= 0:
            raise ValidationError("Item quantity must be at least 1.")
        default_price = variant.wholesale_price if resolved_price_list == PriceList.WHOLESALE else variant.retail_price
        unit_price = money_round(row.get("unit_price") if row.get("unit_price") is not None else default_price)
        if unit_price < Decimal("0.00"):
            raise ValidationError(f"Unit price for {variant.sku} cannot be negative.")
        disc_pct = Decimal(str(row.get("discount_percent") or "0.00"))
        gross = money_round(Decimal(qty) * unit_price)
        disc_amt = money_round(gross * disc_pct / Decimal("100.00"))
        line_total = money_round(gross - disc_amt)

        SalesOrderItem.objects.create(
            sales_order=so,
            variant=variant,
            quantity=qty,
            unit_price=unit_price,
            discount_percent=disc_pct,
            line_total=line_total,
        )
        subtotal += gross
        discount_total += disc_amt

    so.subtotal = money_round(subtotal)
    so.discount_total = money_round(discount_total)
    so.grand_total = max(money_round(subtotal - discount_total - so.invoice_discount), Decimal("0.00"))
    so.save(update_fields=["subtotal", "discount_total", "grand_total", "updated_at"])
    return so


@transaction.atomic
def convert_sales_order_to_invoice(
    *,
    sales_order: SalesOrder,
    user,
    override_credit_limit: bool = False,
    request=None,
):
    if hasattr(sales_order, "invoice") and sales_order.invoice is not None:
        raise ValidationError("This sales order has already been invoiced.")

    items_data = [
        {
            "variant": it.variant,
            "quantity": it.quantity,
            "unit_price": it.unit_price,
            "discount_percent": it.discount_percent,
        }
        for it in sales_order.items.select_related("variant__product").all()
    ]
    return create_invoice(
        customer=sales_order.customer,
        location=sales_order.location,
        price_list=sales_order.price_list,
        tax_mode=sales_order.tax_mode,
        invoice_discount=sales_order.invoice_discount,
        items_data=items_data,
        sales_order=sales_order,
        notes=sales_order.notes,
        user=user,
        issue_now=True,
        override_credit_limit=override_credit_limit,
        request=request,
    )


@transaction.atomic
def process_sales_return(
    *,
    invoice,
    items_data: list[dict],
    user,
    condition: str = SalesReturn.Condition.RESTOCK,
    return_date=None,
    reason: str = "",
    refund_amount: Decimal = Decimal("0.00"),
    refund_mode: str = "CASH",
    request=None,
) -> SalesReturn:
    if not items_data:
        raise ValidationError("Sales return must contain at least one item.")

    loc = invoice.location or StoreLocation.get_default()
    sr = SalesReturn.objects.create(
        return_number=_next_sales_num(SalesReturn, "SRET"),
        customer=invoice.customer,
        invoice=invoice,
        location=loc,
        return_date=return_date or timezone.localdate(),
        condition=condition,
        reason=reason,
        created_by=user,
    )

    cn_items = []
    for row in items_data:
        variant = row["variant"]
        qty = int(row["quantity"])
        inv_item = invoice.items.filter(variant=variant).first()
        if not inv_item:
            raise ValidationError(f"Variant {variant.sku} was not billed on invoice {invoice.invoice_number}.")

        already_returned = (
            SalesReturnItem.objects.filter(sales_return__invoice=invoice, variant=variant).aggregate(
                total=Sum("quantity")
            )["total"]
            or 0
        )
        if (already_returned + qty) > inv_item.quantity:
            raise ValidationError(
                f"Total return qty ({already_returned + qty}) exceeds invoiced qty ({inv_item.quantity}) for {variant.sku}."
            )

        billed_base = inv_item.line_total if invoice.tax_mode == StoreSettings.TaxMode.INCLUSIVE else inv_item.taxable_value
        if qty == inv_item.quantity and already_returned == 0:
            prorated_gross = money_round(billed_base)
        else:
            prorated_gross = money_round(billed_base * Decimal(qty) / Decimal(inv_item.quantity))
        unit_price = money_round(prorated_gross / Decimal(qty))
        tax_rate = inv_item.tax_rate
        line_total = prorated_gross

        SalesReturnItem.objects.create(
            sales_return=sr,
            variant=variant,
            quantity=qty,
            unit_price=unit_price,
            tax_rate=tax_rate,
            line_total=line_total,
        )

        record_stock_movement(
            variant=variant,
            location=loc,
            movement_type=StockMovement.MovementType.SALE_RETURN,
            quantity=qty,
            reference=f"SRET:{sr.return_number}",
            reason=reason or "Sales Return",
            notes=reason or f"Sales return against {invoice.invoice_number}",
            user=user,
            request=request,
        )
        if condition == SalesReturn.Condition.DAMAGED:
            record_stock_movement(
                variant=variant,
                location=loc,
                movement_type=StockMovement.MovementType.DAMAGE,
                quantity=qty,
                reference=f"SRET:{sr.return_number}",
                reason="Damaged on Return",
                notes=f"Returned damaged against {invoice.invoice_number}",
                user=user,
                request=request,
            )

        cn_items.append(
            {
                "variant": variant,
                "quantity": qty,
                "unit_price": unit_price,
                "gross_amount": prorated_gross,
                "tax_rate": tax_rate,
            }
        )

    cn = create_credit_note(
        invoice=invoice,
        items_data=cn_items,
        user=user,
        reason=reason or f"Sales Return {sr.return_number}",
        sales_return=sr,
        issue_date=sr.return_date,
        request=request,
    )
    sr.total_amount = cn.total_amount

    rounded_refund = money_round(refund_amount or Decimal("0.00"))
    if rounded_refund > Decimal("0.00"):
        issue_refund(
            credit_note=cn,
            amount=rounded_refund,
            payment_mode=refund_mode,
            refund_date=sr.return_date,
            notes=f"Refund for {sr.return_number}",
            user=user,
            request=request,
        )
        sr.refund_amount = rounded_refund

    sr.save(update_fields=["total_amount", "refund_amount", "updated_at"])
    return sr


@transaction.atomic
def process_sales_exchange(
    *,
    original_invoice,
    return_items: list[dict],
    replacement_items: list[dict],
    user,
    condition: str = SalesReturn.Condition.RESTOCK,
    reason: str = "",
    request=None,
) -> SalesExchange:
    """Process a product exchange: return old item(s) with CreditNote and issue replacement Invoice."""
    if not return_items or not replacement_items:
        raise ValidationError("Exchange requires both returned item(s) and replacement item(s).")

    sr = process_sales_return(
        invoice=original_invoice,
        items_data=return_items,
        condition=condition,
        reason=reason or f"Exchange against {original_invoice.invoice_number}",
        user=user,
        request=request,
    )
    replacement_inv = create_invoice(
        customer=original_invoice.customer,
        location=original_invoice.location,
        price_list=original_invoice.price_list,
        tax_mode=original_invoice.tax_mode,
        items_data=replacement_items,
        notes=f"Exchange replacement for {original_invoice.invoice_number}",
        user=user,
        issue_now=True,
        override_credit_limit=True,
        request=request,
    )
    ex = SalesExchange.objects.create(
        exchange_number=_next_sales_num(SalesExchange, "EXC"),
        original_invoice=original_invoice,
        customer=original_invoice.customer,
        location=original_invoice.location or StoreLocation.get_default(),
        credit_note=sr.credit_note,
        replacement_invoice=replacement_inv,
        notes=reason,
        created_by=user,
    )
    return ex
