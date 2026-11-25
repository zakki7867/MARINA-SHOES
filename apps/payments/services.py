from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.utils import money_round, next_sequence_number
from apps.customers.models import Customer
from apps.invoicing.models import Invoice
from apps.purchases.models import GoodsReceipt
from .models import CustomerPayment, PaymentAllocation


def _next_receipt_number() -> str:
    return next_sequence_number("RCPT", CustomerPayment)


@transaction.atomic
def record_customer_payment(
    *,
    customer: Customer,
    amount: Decimal,
    payment_mode: str,
    user,
    invoice: Invoice | None = None,
    auto_allocate: bool = True,
    is_advance: bool = False,
    payment_date=None,
    reference_no: str = "",
    notes: str = "",
    request=None,
) -> CustomerPayment:
    """
    Atomically record a full/partial/advance customer payment, allocate against
    specified or oldest open invoices, and update invoice statuses.
    """
    rounded_amount = money_round(amount)
    if rounded_amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than zero.")
    if not customer.is_active or customer.is_deleted:
        raise ValidationError("Cannot record payment for an inactive or archived customer.")

    payment = CustomerPayment.objects.create(
        receipt_number=_next_receipt_number(),
        customer=customer,
        payment_date=payment_date or timezone.localdate(),
        amount=rounded_amount,
        payment_mode=payment_mode,
        is_advance=is_advance,
        reference_no=reference_no,
        notes=notes,
        created_by=user,
    )

    remaining = rounded_amount
    target_invoices: list[Invoice] = []
    if invoice is not None:
        if invoice.customer_id != customer.id:
            raise ValidationError("Selected invoice does not belong to this customer.")
        if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
            raise ValidationError("Cannot allocate payment to a draft or cancelled invoice.")
        target_invoices = [Invoice.objects.select_for_update().get(pk=invoice.pk)]
    elif auto_allocate and not is_advance:
        target_invoices = list(
            Invoice.objects.select_for_update()
            .filter(customer=customer, status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL])
            .order_by("invoice_date", "id")
        )

    for inv in target_invoices:
        if remaining <= Decimal("0.00"):
            break
        due = inv.balance_due
        if due <= Decimal("0.00"):
            continue
        alloc_amt = min(remaining, due)
        PaymentAllocation.objects.create(payment=payment, invoice=inv, amount=alloc_amt)
        inv.amount_paid = money_round(inv.amount_paid + alloc_amt)
        inv.status = Invoice.Status.PAID if inv.balance_due <= Decimal("0.00") else Invoice.Status.PARTIAL
        inv.save(update_fields=["amount_paid", "status", "updated_at"])
        remaining = money_round(remaining - alloc_amt)

    log_audit(
        user=user,
        action=AuditLog.Action.CREATE,
        instance=payment,
        changes={
            "customer": customer.name,
            "amount": rounded_amount,
            "payment_mode": payment_mode,
            "invoice": invoice.invoice_number if invoice else ("Advance" if is_advance else "Auto-allocate"),
        },
        request=request,
        notes="Recorded customer payment",
    )
    return payment


@transaction.atomic
def apply_customer_advance(*, customer: Customer, invoice: Invoice, user, request=None) -> Decimal:
    """Allocate any unallocated advance/payment balance from a customer onto an open invoice."""
    locked_inv = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if locked_inv.customer_id != customer.id:
        raise ValidationError("Invoice does not belong to this customer.")
    if locked_inv.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED, Invoice.Status.PAID):
        return Decimal("0.00")

    applied_total = Decimal("0.00")
    payments = CustomerPayment.objects.select_for_update().filter(customer=customer, is_reversed=False).order_by("payment_date", "id")
    for p in payments:
        due = locked_inv.balance_due
        if due <= Decimal("0.00"):
            break
        avail = p.unallocated_amount
        if avail <= Decimal("0.00"):
            continue
        alloc_amt = min(avail, due)
        alloc, created = PaymentAllocation.objects.get_or_create(
            payment=p,
            invoice=locked_inv,
            defaults={"amount": alloc_amt},
        )
        if not created:
            alloc.amount = money_round(alloc.amount + alloc_amt)
            alloc.save(update_fields=["amount"])
        locked_inv.amount_paid = money_round(locked_inv.amount_paid + alloc_amt)
        locked_inv.status = Invoice.Status.PAID if locked_inv.balance_due <= Decimal("0.00") else Invoice.Status.PARTIAL
        locked_inv.save(update_fields=["amount_paid", "status", "updated_at"])
        applied_total = money_round(applied_total + alloc_amt)

    if applied_total > Decimal("0.00"):
        log_audit(
            user=user,
            action=AuditLog.Action.UPDATE,
            instance=locked_inv,
            changes={"advance_applied": applied_total},
            request=request,
            notes=f"Applied advance to {locked_inv.invoice_number}",
        )
    return applied_total


@transaction.atomic
def reverse_customer_payment(*, payment: CustomerPayment, user, reason: str = "", request=None) -> CustomerPayment:
    """Audited payment reversal: reverses allocations and restores invoice balances."""
    locked = CustomerPayment.objects.select_for_update().get(pk=payment.pk)
    if locked.is_reversed:
        raise ValidationError("Payment is already reversed.")

    for alloc in locked.allocations.select_related("invoice").all():
        inv = Invoice.objects.select_for_update().get(pk=alloc.invoice_id)
        inv.amount_paid = max(money_round(inv.amount_paid - alloc.amount), Decimal("0.00"))
        if inv.status != Invoice.Status.CANCELLED:
            inv.status = Invoice.Status.PARTIAL if inv.amount_paid > Decimal("0.00") else Invoice.Status.ISSUED
        inv.save(update_fields=["amount_paid", "status", "updated_at"])

    locked.allocations.all().delete()
    locked.is_reversed = True
    locked.reversed_by = user
    locked.reversed_at = timezone.now()
    locked.reversal_reason = reason
    locked.save(update_fields=["is_reversed", "reversed_by", "reversed_at", "reversal_reason", "updated_at"])

    log_audit(
        user=user,
        action=AuditLog.Action.CANCEL,
        instance=locked,
        changes={"amount": locked.amount, "reason": reason},
        request=request,
        notes=f"Reversed payment {locked.receipt_number}",
    )
    return locked


def get_overdue_invoices():
    today = timezone.localdate()
    candidates = (
        Invoice.objects.select_related("customer")
        .filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL], due_date__lt=today)
        .order_by("due_date", "id")
    )
    return [inv for inv in candidates if inv.balance_due > Decimal("0.00")]


def get_customer_aging() -> list[dict]:
    """Compute 0-30, 31-60, 61-90, 90+ day aging buckets for open customer invoices."""
    today = timezone.localdate()
    by_cust: dict[int, dict] = {}
    invoices = Invoice.objects.select_related("customer").filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL])
    for inv in invoices:
        bal = inv.balance_due
        if bal <= Decimal("0.00"):
            continue
        age_days = max((today - inv.invoice_date).days, 0)
        entry = by_cust.setdefault(
            inv.customer_id,
            {
                "customer": inv.customer,
                "b_0_30": Decimal("0.00"),
                "b_31_60": Decimal("0.00"),
                "b_61_90": Decimal("0.00"),
                "b_90_plus": Decimal("0.00"),
                "total": Decimal("0.00"),
            },
        )
        if age_days <= 30:
            entry["b_0_30"] = money_round(entry["b_0_30"] + bal)
        elif age_days <= 60:
            entry["b_31_60"] = money_round(entry["b_31_60"] + bal)
        elif age_days <= 90:
            entry["b_61_90"] = money_round(entry["b_61_90"] + bal)
        else:
            entry["b_90_plus"] = money_round(entry["b_90_plus"] + bal)
        entry["total"] = money_round(entry["total"] + bal)
    return sorted(by_cust.values(), key=lambda r: r["total"], reverse=True)


def get_supplier_aging() -> list[dict]:
    """Compute 0-30, 31-60, 61-90, 90+ day aging buckets for open supplier GRNs."""
    today = timezone.localdate()
    by_sup: dict[int, dict] = {}
    for grn in GoodsReceipt.objects.select_related("supplier").all():
        bal = grn.balance_due
        if bal <= Decimal("0.00"):
            continue
        age_days = max((today - grn.received_date).days, 0)
        entry = by_sup.setdefault(
            grn.supplier_id,
            {
                "supplier": grn.supplier,
                "b_0_30": Decimal("0.00"),
                "b_31_60": Decimal("0.00"),
                "b_61_90": Decimal("0.00"),
                "b_90_plus": Decimal("0.00"),
                "total": Decimal("0.00"),
            },
        )
        if age_days <= 30:
            entry["b_0_30"] = money_round(entry["b_0_30"] + bal)
        elif age_days <= 60:
            entry["b_31_60"] = money_round(entry["b_31_60"] + bal)
        elif age_days <= 90:
            entry["b_61_90"] = money_round(entry["b_61_90"] + bal)
        else:
            entry["b_90_plus"] = money_round(entry["b_90_plus"] + bal)
        entry["total"] = money_round(entry["total"] + bal)
    return sorted(by_sup.values(), key=lambda r: r["total"], reverse=True)


def get_total_pending_receivables() -> Decimal:
    total = Decimal("0.00")
    for c in Customer.objects.with_balances():
        bal = c.outstanding_balance
        if bal > Decimal("0.00"):
            total += bal
    return money_round(total)
