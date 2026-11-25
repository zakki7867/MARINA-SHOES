from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.models import StoreLocation, StoreSettings
from apps.core.utils import get_financial_year, money_round, next_sequence_number
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.inventory.services import record_stock_movement
from apps.sales.models import PriceList, SalesOrder
from .models import CreditNote, CreditNoteItem, FinancialYearSequence, Invoice, InvoiceItem, Refund


class CreditLimitExceededError(ValidationError):
    """Raised when issuing a credit invoice exceeds a wholesale customer's credit limit."""


def calculate_gst_line(
    *,
    gross_after_discount: Decimal,
    tax_rate: Decimal,
    place_of_supply_state: str,
    store_state: str,
    tax_mode: str = StoreSettings.TaxMode.EXCLUSIVE,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """
    Calculate (taxable_value, cgst, sgst, igst, line_total) for GST Exclusive or Inclusive pricing.
    """
    amount = money_round(gross_after_discount)
    if amount <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00")

    same_state = place_of_supply_state.strip().upper() == store_state.strip().upper()

    if tax_rate <= Decimal("0.00"):
        return amount, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), amount

    if tax_mode == StoreSettings.TaxMode.INCLUSIVE:
        divisor = Decimal("1.00") + (tax_rate / Decimal("100.00"))
        taxable = money_round(amount / divisor)
        total_tax = money_round(amount - taxable)
        if same_state:
            cgst = money_round(total_tax / Decimal("2"))
            sgst = money_round(total_tax - cgst)
            igst = Decimal("0.00")
        else:
            cgst = Decimal("0.00")
            sgst = Decimal("0.00")
            igst = total_tax
        return taxable, cgst, sgst, igst, amount

    # EXCLUSIVE
    taxable = amount
    if same_state:
        half_rate = tax_rate / Decimal("2")
        cgst = money_round(taxable * half_rate / Decimal("100.00"))
        sgst = money_round(taxable * half_rate / Decimal("100.00"))
        igst = Decimal("0.00")
    else:
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
        igst = money_round(taxable * tax_rate / Decimal("100.00"))
    line_total = money_round(taxable + cgst + sgst + igst)
    return taxable, cgst, sgst, igst, line_total


def calculate_gst_components(
    *,
    taxable_value: Decimal,
    tax_rate: Decimal,
    place_of_supply_state: str,
    store_state: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Backward-compatible exclusive GST split helper."""
    _, cgst, sgst, igst, _ = calculate_gst_line(
        gross_after_discount=taxable_value,
        tax_rate=tax_rate,
        place_of_supply_state=place_of_supply_state,
        store_state=store_state,
        tax_mode=StoreSettings.TaxMode.EXCLUSIVE,
    )
    return cgst, sgst, igst


def generate_next_invoice_number(invoice_date, store_settings: StoreSettings) -> tuple[str, str]:
    """Atomically increment per-financial-year invoice sequence using StoreSettings format."""
    fy = get_financial_year(invoice_date, start_month=store_settings.financial_year_start_month)
    FinancialYearSequence.objects.get_or_create(financial_year=fy)
    seq = FinancialYearSequence.objects.select_for_update().get(financial_year=fy)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    fmt = store_settings.invoice_number_format or "{prefix}/{fy}/{seq:04d}"
    try:
        number = fmt.format(prefix=store_settings.invoice_prefix, fy=fy, seq=seq.last_number)
    except (KeyError, ValueError):
        number = f"{store_settings.invoice_prefix}/{fy}/{seq.last_number:04d}"
    return number, fy


@transaction.atomic
def create_invoice(
    *,
    customer: Customer,
    items_data: list[dict],
    user,
    location: StoreLocation | None = None,
    price_list: str | None = None,
    tax_mode: str | None = None,
    invoice_discount: Decimal = Decimal("0.00"),
    invoice_date=None,
    due_date=None,
    place_of_supply_state: str | None = None,
    billing_address: str | None = None,
    shipping_address: str | None = None,
    sales_order: SalesOrder | None = None,
    notes: str = "",
    issue_now: bool = True,
    override_credit_limit: bool = False,
    enforce_price_permission: bool = False,
    request=None,
) -> Invoice:
    if not items_data:
        raise ValidationError("Invoice must contain at least one item.")
    if not customer.is_active or customer.is_deleted:
        raise ValidationError("Cannot issue an invoice for an inactive or archived customer.")

    store_settings = StoreSettings.get_settings()
    resolved_loc = location or StoreLocation.get_default()
    inv_date = invoice_date or timezone.localdate()
    resolved_price_list = price_list or (
        PriceList.WHOLESALE if customer.customer_type == Customer.CustomerType.WHOLESALE else PriceList.RETAIL
    )
    resolved_tax_mode = tax_mode or store_settings.default_tax_mode
    resolved_due_date = due_date or (
        inv_date + timedelta(days=customer.payment_terms_days if customer.customer_type == Customer.CustomerType.WHOLESALE else 0)
    )
    pos_state = (place_of_supply_state or customer.state_code or resolved_loc.state_code or store_settings.state_code).strip().upper()

    invoice = Invoice.objects.create(
        customer=customer,
        location=resolved_loc,
        sales_order=sales_order,
        price_list=resolved_price_list,
        tax_mode=resolved_tax_mode,
        invoice_date=inv_date,
        due_date=resolved_due_date,
        place_of_supply_state=pos_state,
        billing_address=billing_address if billing_address is not None else customer.address,
        shipping_address=shipping_address if shipping_address is not None else (customer.shipping_address or customer.address),
        status=Invoice.Status.DRAFT,
        notes=notes,
        terms_and_conditions=store_settings.invoice_terms,
        created_by=user,
    )

    prepared_lines = []
    subtotal = Decimal("0.00")
    line_discount_total = Decimal("0.00")
    total_net_after_line_disc = Decimal("0.00")

    for row in items_data:
        variant = row["variant"]
        if not variant.is_active or variant.is_deleted or variant.product.is_deleted or variant.product.status != "ACTIVE":
            raise ValidationError(f"Variant {variant.sku} is inactive or archived and cannot be billed.")
        qty = int(row["quantity"])
        if qty <= 0:
            raise ValidationError("Item quantity must be at least 1.")

        default_price = variant.wholesale_price if resolved_price_list == PriceList.WHOLESALE else variant.retail_price
        raw_unit_price = row.get("unit_price")
        unit_price = money_round(raw_unit_price if raw_unit_price not in (None, "") else default_price)
        if unit_price < Decimal("0.00"):
            raise ValidationError(f"Unit price for {variant.sku} cannot be negative.")

        if enforce_price_permission and unit_price != money_round(default_price):
            if not user.has_any_role(User.Role.OWNER, User.Role.MANAGER):
                raise PermissionDenied("Custom price override requires Owner or Manager permission.")

        line_gross = money_round(Decimal(qty) * unit_price)

        if row.get("discount_amount") is not None:
            line_discount = money_round(row["discount_amount"])
        elif row.get("discount_percent") is not None:
            pct = Decimal(str(row["discount_percent"]))
            line_discount = money_round(line_gross * pct / Decimal("100.00"))
        else:
            line_discount = Decimal("0.00")

        if line_discount < Decimal("0.00") or line_discount > line_gross:
            raise ValidationError(f"Invalid discount for {variant.sku}.")

        net_after_disc = money_round(line_gross - line_discount)
        prepared_lines.append({
            "variant": variant,
            "qty": qty,
            "unit_price": unit_price,
            "line_gross": line_gross,
            "line_discount": line_discount,
            "net_after_disc": net_after_disc,
        })
        subtotal += line_gross
        line_discount_total += line_discount
        total_net_after_line_disc += net_after_disc

    inv_disc = money_round(invoice_discount)
    if inv_disc < Decimal("0.00") or inv_disc > total_net_after_line_disc:
        raise ValidationError("Invalid invoice-level discount.")

    # Allocate invoice-level trade discount proportionally before GST so taxable & tax totals reconcile
    remaining_inv_disc = inv_disc
    taxable_total = Decimal("0.00")
    cgst_total = Decimal("0.00")
    sgst_total = Decimal("0.00")
    igst_total = Decimal("0.00")
    grand_total = Decimal("0.00")

    for idx, line in enumerate(prepared_lines):
        if inv_disc > Decimal("0.00") and total_net_after_line_disc > Decimal("0.00"):
            if idx == len(prepared_lines) - 1:
                alloc_disc = remaining_inv_disc
            else:
                alloc_disc = money_round(inv_disc * line["net_after_disc"] / total_net_after_line_disc)
                alloc_disc = min(alloc_disc, remaining_inv_disc, line["net_after_disc"])
                remaining_inv_disc = money_round(remaining_inv_disc - alloc_disc)
        else:
            alloc_disc = Decimal("0.00")

        variant = line["variant"]
        effective_disc = money_round(line["line_discount"] + alloc_disc)
        effective_net = money_round(line["net_after_disc"] - alloc_disc)
        tax_rate = variant.effective_gst_rate
        taxable_val, cgst, sgst, igst, line_total = calculate_gst_line(
            gross_after_discount=effective_net,
            tax_rate=tax_rate,
            place_of_supply_state=pos_state,
            store_state=resolved_loc.state_code or store_settings.state_code,
            tax_mode=resolved_tax_mode,
        )

        InvoiceItem.objects.create(
            invoice=invoice,
            variant=variant,
            description=f"{variant.product.brand.name} {variant.product.name} ({variant.color} / {variant.size})",
            hsn_code=variant.product.hsn_code,
            quantity=line["qty"],
            unit_price=line["unit_price"],
            discount_amount=effective_disc,
            taxable_value=taxable_val,
            tax_rate=tax_rate,
            cgst_amount=cgst,
            sgst_amount=sgst,
            igst_amount=igst,
            line_total=line_total,
        )

        taxable_total += taxable_val
        cgst_total += cgst
        sgst_total += sgst
        igst_total += igst
        grand_total += line_total

    invoice.subtotal = money_round(subtotal)
    invoice.discount_total = money_round(line_discount_total + inv_disc)
    invoice.invoice_discount = inv_disc
    invoice.taxable_amount = money_round(taxable_total)
    invoice.cgst_total = money_round(cgst_total)
    invoice.sgst_total = money_round(sgst_total)
    invoice.igst_total = money_round(igst_total)
    invoice.grand_total = money_round(grand_total)
    invoice.save()

    if issue_now:
        return issue_invoice(invoice=invoice, user=user, override_credit_limit=override_credit_limit, request=request)

    return invoice


@transaction.atomic
def issue_invoice(*, invoice: Invoice, user, override_credit_limit: bool = False, request=None) -> Invoice:
    # Lock Invoice row directly without nullable outer joins so PostgreSQL FOR UPDATE never raises NotSupportedError
    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if locked.status != Invoice.Status.DRAFT:
        raise ValidationError("Only draft invoices can be issued.")

    store_settings = StoreSettings.get_settings()
    customer = locked.customer
    resolved_loc = locked.location or StoreLocation.get_default()

    if (
        customer.customer_type == Customer.CustomerType.WHOLESALE
        and customer.credit_limit > Decimal("0.00")
        and not override_credit_limit
    ):
        projected = customer.outstanding_balance + locked.grand_total
        if projected > customer.credit_limit:
            raise CreditLimitExceededError(
                f"Credit limit exceeded for {customer.name}: limit {customer.credit_limit}, projected balance {projected}."
            )

    inv_number, fy = generate_next_invoice_number(locked.invoice_date, store_settings)
    locked.invoice_number = inv_number
    locked.financial_year = fy
    locked.status = Invoice.Status.ISSUED
    locked.issued_at = timezone.now()
    locked.save(update_fields=["invoice_number", "financial_year", "status", "issued_at", "updated_at"])

    for item in locked.items.select_related("variant").all():
        record_stock_movement(
            variant=item.variant,
            location=resolved_loc,
            movement_type=StockMovement.MovementType.SALE,
            quantity=item.quantity,
            reference=f"INV:{locked.invoice_number}",
            reason="Invoice Issued",
            notes=f"Invoiced to {customer.name}",
            user=user,
            request=request,
        )

    if locked.sales_order and locked.sales_order.status != SalesOrder.Status.INVOICED:
        locked.sales_order.status = SalesOrder.Status.INVOICED
        locked.sales_order.save(update_fields=["status", "updated_at"])

    log_audit(
        user=user,
        action=AuditLog.Action.ISSUE,
        instance=locked,
        changes={"invoice_number": locked.invoice_number, "grand_total": locked.grand_total, "customer": customer.name},
        request=request,
        notes="Issued tax invoice",
    )
    return locked


@transaction.atomic
def cancel_invoice(*, invoice: Invoice, user, reason: str = "", request=None) -> Invoice:
    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if locked.status == Invoice.Status.CANCELLED:
        raise ValidationError("Invoice is already cancelled.")
    if locked.amount_paid > Decimal("0.00"):
        raise ValidationError("Cannot cancel an invoice with recorded payments. Issue a credit note instead.")
    if locked.credit_notes.exists():
        raise ValidationError("Cannot cancel an invoice that already has credit notes.")

    was_issued = locked.status != Invoice.Status.DRAFT
    locked.status = Invoice.Status.CANCELLED
    locked.cancelled_by = user
    locked.cancelled_at = timezone.now()
    locked.cancel_reason = reason
    locked.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason", "updated_at"])

    if was_issued:
        loc = locked.location or StoreLocation.get_default()
        for item in locked.items.select_related("variant").all():
            record_stock_movement(
                variant=item.variant,
                location=loc,
                movement_type=StockMovement.MovementType.SALE_RETURN,
                quantity=item.quantity,
                reference=f"CANCEL:{locked.invoice_number}",
                reason=reason or "Invoice Cancelled",
                notes=reason or "Invoice cancelled",
                user=user,
                request=request,
            )

    log_audit(
        user=user,
        action=AuditLog.Action.CANCEL,
        instance=locked,
        changes={"reason": reason},
        request=request,
        notes=f"Cancelled invoice {locked.invoice_number or locked.pk}",
    )
    return locked


@transaction.atomic
def create_credit_note(
    *,
    invoice: Invoice,
    items_data: list[dict],
    user,
    reason: str = "",
    sales_return=None,
    issue_date=None,
    request=None,
) -> CreditNote:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        raise ValidationError("Credit notes can only be created against issued invoices.")
    if not items_data:
        raise ValidationError("Credit note must contain at least one item.")

    store_settings = StoreSettings.get_settings()
    store_state = (invoice.location.state_code if invoice.location else store_settings.state_code)
    cn_num = next_sequence_number("CN", CreditNote)

    cn = CreditNote.objects.create(
        credit_note_number=cn_num,
        invoice=invoice,
        customer=invoice.customer,
        sales_return=sales_return,
        issue_date=issue_date or timezone.localdate(),
        reason=reason,
        created_by=user,
    )

    taxable_total = Decimal("0.00")
    cgst_total = Decimal("0.00")
    sgst_total = Decimal("0.00")
    igst_total = Decimal("0.00")
    grand_total = Decimal("0.00")

    for row in items_data:
        variant = row["variant"]
        qty = int(row["quantity"])
        if qty <= 0:
            raise ValidationError("Credit note item quantity must be positive.")
        tax_rate = Decimal(str(row.get("tax_rate", variant.effective_gst_rate)))
        if row.get("gross_amount") is not None:
            gross = money_round(row["gross_amount"])
            unit_price = money_round(row.get("unit_price") or (gross / Decimal(qty)))
        else:
            unit_price = money_round(row["unit_price"])
            gross = money_round(Decimal(qty) * unit_price)
        taxable_val, cgst, sgst, igst, line_total = calculate_gst_line(
            gross_after_discount=gross,
            tax_rate=tax_rate,
            place_of_supply_state=invoice.place_of_supply_state,
            store_state=store_state,
            tax_mode=invoice.tax_mode,
        )

        CreditNoteItem.objects.create(
            credit_note=cn,
            variant=variant,
            hsn_code=variant.product.hsn_code,
            quantity=qty,
            unit_price=unit_price,
            tax_rate=tax_rate,
            taxable_value=taxable_val,
            cgst_amount=cgst,
            sgst_amount=sgst,
            igst_amount=igst,
            line_total=line_total,
        )
        taxable_total += taxable_val
        cgst_total += cgst
        sgst_total += sgst
        igst_total += igst
        grand_total += line_total

    cn.taxable_amount = money_round(taxable_total)
    cn.cgst_total = money_round(cgst_total)
    cn.sgst_total = money_round(sgst_total)
    cn.igst_total = money_round(igst_total)
    cn.total_amount = money_round(grand_total)
    prior_credited = invoice.credit_notes.exclude(pk=cn.pk).values_list("total_amount", flat=True)
    if money_round(sum(prior_credited, Decimal("0.00")) + cn.total_amount) > invoice.grand_total:
        raise ValidationError("Total credit notes cannot exceed the original invoice grand total.")
    cn.save()

    if invoice.balance_due <= Decimal("0.00") and invoice.status != Invoice.Status.PAID:
        invoice.status = Invoice.Status.PAID
        invoice.save(update_fields=["status", "updated_at"])

    log_audit(
        user=user,
        action=AuditLog.Action.CREATE,
        instance=cn,
        changes={"invoice_number": invoice.invoice_number, "total_amount": cn.total_amount},
        request=request,
        notes="Created credit note",
    )
    return cn


@transaction.atomic
def issue_refund(
    *,
    credit_note: CreditNote,
    amount: Decimal,
    payment_mode: str,
    user,
    refund_date=None,
    reference_no: str = "",
    notes: str = "",
    request=None,
) -> Refund:
    rounded = money_round(amount)
    already_refunded = money_round(sum(credit_note.refunds.values_list("amount", flat=True), Decimal("0.00")))
    if rounded <= Decimal("0.00") or (already_refunded + rounded) > credit_note.total_amount:
        raise ValidationError("Total refund amount cannot exceed the credit note total.")

    ref_num = next_sequence_number("REF", Refund)

    refund = Refund.objects.create(
        refund_number=ref_num,
        credit_note=credit_note,
        customer=credit_note.customer,
        refund_date=refund_date or timezone.localdate(),
        amount=rounded,
        payment_mode=payment_mode,
        reference_no=reference_no,
        notes=notes,
        created_by=user,
    )
    log_audit(
        user=user,
        action=AuditLog.Action.CREATE,
        instance=refund,
        changes={"credit_note": credit_note.credit_note_number, "amount": rounded, "mode": payment_mode},
        request=request,
        notes="Issued customer refund",
    )
    return refund


def render_invoice_pdf_bytes(invoice: Invoice) -> bytes:
    """Render an invoice PDF using WeasyPrint."""
    store = StoreSettings.get_settings()
    html_string = render_to_string(
        "invoicing/invoice_pdf.html",
        {"invoice": invoice, "store_settings": store},
    )
    try:
        import weasyprint
    except OSError as exc:
        raise RuntimeError(
            "WeasyPrint system libraries (GTK3/Pango) are not installed on this OS. "
            "See README.md for installation steps or use the Print View."
        ) from exc
    return weasyprint.HTML(string=html_string).write_pdf()
