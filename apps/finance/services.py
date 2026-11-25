from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.core.utils import money_round
from apps.expenses.models import Expense
from apps.invoicing.models import CreditNote, CreditNoteItem, Invoice, InvoiceItem, Refund
from apps.payments.models import CustomerPayment
from apps.purchases.models import GoodsReceipt, SupplierPayment


def get_ledger_book(*, modes: tuple[str, ...], start_date: date | None = None, end_date: date | None = None) -> dict:
    """Build chronological cash or bank book entries with running balance."""
    entries = []

    cp_qs = CustomerPayment.objects.select_related("customer").filter(payment_mode__in=modes, is_reversed=False)
    sp_qs = SupplierPayment.objects.select_related("supplier").filter(payment_mode__in=modes)
    ex_qs = Expense.objects.filter(payment_mode__in=modes)
    rf_qs = Refund.objects.select_related("customer").filter(payment_mode__in=modes)

    if start_date:
        cp_qs = cp_qs.filter(payment_date__gte=start_date)
        sp_qs = sp_qs.filter(payment_date__gte=start_date)
        ex_qs = ex_qs.filter(expense_date__gte=start_date)
        rf_qs = rf_qs.filter(refund_date__gte=start_date)
    if end_date:
        cp_qs = cp_qs.filter(payment_date__lte=end_date)
        sp_qs = sp_qs.filter(payment_date__lte=end_date)
        ex_qs = ex_qs.filter(expense_date__lte=end_date)
        rf_qs = rf_qs.filter(refund_date__lte=end_date)

    for p in cp_qs:
        entries.append(
            {
                "date": p.payment_date,
                "ref": p.receipt_number,
                "particulars": f"Customer Receipt — {p.customer.name}",
                "mode": p.get_payment_mode_display(),
                "credit": money_round(p.amount),
                "debit": Decimal("0.00"),
                "sort_id": (p.payment_date, 1, p.id),
            }
        )
    for sp in sp_qs:
        entries.append(
            {
                "date": sp.payment_date,
                "ref": sp.payment_number,
                "particulars": f"Supplier Payment — {sp.supplier.name}",
                "mode": sp.get_payment_mode_display(),
                "credit": Decimal("0.00"),
                "debit": money_round(sp.amount),
                "sort_id": (sp.payment_date, 2, sp.id),
            }
        )
    for ex in ex_qs:
        entries.append(
            {
                "date": ex.expense_date,
                "ref": ex.reference_no or f"EXP-{ex.id}",
                "particulars": f"Expense ({ex.get_category_display()}) {ex.notes}".strip(),
                "mode": ex.get_payment_mode_display(),
                "credit": Decimal("0.00"),
                "debit": money_round(ex.amount),
                "sort_id": (ex.expense_date, 3, ex.id),
            }
        )
    for rf in rf_qs:
        entries.append(
            {
                "date": rf.refund_date,
                "ref": rf.refund_number,
                "particulars": f"Customer Refund — {rf.customer.name}",
                "mode": rf.get_payment_mode_display(),
                "credit": Decimal("0.00"),
                "debit": money_round(rf.amount),
                "sort_id": (rf.refund_date, 4, rf.id),
            }
        )

    entries.sort(key=lambda e: e["sort_id"])
    running = Decimal("0.00")
    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    for e in entries:
        running = money_round(running + e["credit"] - e["debit"])
        total_in = money_round(total_in + e["credit"])
        total_out = money_round(total_out + e["debit"])
        e["balance"] = running

    return {
        "entries": entries,
        "total_in": total_in,
        "total_out": total_out,
        "closing_balance": running,
    }


def get_profit_and_loss(*, start_date: date | None = None, end_date: date | None = None) -> dict:
    """Compute Net Revenue (ex-tax), COGS, Gross Profit, Expenses, and Net Profit."""
    inv_qs = Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    cn_qs = CreditNote.objects.all()
    exp_qs = Expense.objects.all()

    if start_date:
        inv_qs = inv_qs.filter(invoice_date__gte=start_date)
        cn_qs = cn_qs.filter(issue_date__gte=start_date)
        exp_qs = exp_qs.filter(expense_date__gte=start_date)
    if end_date:
        inv_qs = inv_qs.filter(invoice_date__lte=end_date)
        cn_qs = cn_qs.filter(issue_date__lte=end_date)
        exp_qs = exp_qs.filter(expense_date__lte=end_date)

    gross_sales_taxable = money_round(inv_qs.aggregate(t=Sum("taxable_amount"))["t"] or Decimal("0.00"))
    returns_taxable = money_round(cn_qs.aggregate(t=Sum("taxable_amount"))["t"] or Decimal("0.00"))
    net_revenue = money_round(gross_sales_taxable - returns_taxable)

    cogs_out = Decimal("0.00")
    for it in InvoiceItem.objects.filter(invoice__in=inv_qs).select_related("variant"):
        cogs_out += Decimal(it.quantity) * it.variant.cost_price
    cogs_returned = Decimal("0.00")
    for cit in CreditNoteItem.objects.filter(credit_note__in=cn_qs).select_related("variant"):
        cogs_returned += Decimal(cit.quantity) * cit.variant.cost_price
    cogs = money_round(cogs_out - cogs_returned)

    gross_profit = money_round(net_revenue - cogs)
    total_expenses = money_round(exp_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0.00"))
    net_profit = money_round(gross_profit - total_expenses)

    return {
        "gross_sales_taxable": gross_sales_taxable,
        "returns_taxable": returns_taxable,
        "net_revenue": net_revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
    }


def get_gst_summary(*, start_date: date | None = None, end_date: date | None = None) -> dict:
    inv_qs = Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    cn_qs = CreditNote.objects.all()
    if start_date:
        inv_qs = inv_qs.filter(invoice_date__gte=start_date)
        cn_qs = cn_qs.filter(issue_date__gte=start_date)
    if end_date:
        inv_qs = inv_qs.filter(invoice_date__lte=end_date)
        cn_qs = cn_qs.filter(issue_date__lte=end_date)

    inv_agg = inv_qs.aggregate(
        taxable=Sum("taxable_amount"),
        cgst=Sum("cgst_total"),
        sgst=Sum("sgst_total"),
        igst=Sum("igst_total"),
    )
    cn_agg = cn_qs.aggregate(
        taxable=Sum("taxable_amount"),
        cgst=Sum("cgst_total"),
        sgst=Sum("sgst_total"),
        igst=Sum("igst_total"),
    )

    out_taxable = money_round(inv_agg["taxable"] or Decimal("0.00"))
    out_cgst = money_round(inv_agg["cgst"] or Decimal("0.00"))
    out_sgst = money_round(inv_agg["sgst"] or Decimal("0.00"))
    out_igst = money_round(inv_agg["igst"] or Decimal("0.00"))

    cn_taxable = money_round(cn_agg["taxable"] or Decimal("0.00"))
    cn_cgst = money_round(cn_agg["cgst"] or Decimal("0.00"))
    cn_sgst = money_round(cn_agg["sgst"] or Decimal("0.00"))
    cn_igst = money_round(cn_agg["igst"] or Decimal("0.00"))

    net_cgst = money_round(out_cgst - cn_cgst)
    net_sgst = money_round(out_sgst - cn_sgst)
    net_igst = money_round(out_igst - cn_igst)

    return {
        "outward_taxable": out_taxable,
        "outward_cgst": out_cgst,
        "outward_sgst": out_sgst,
        "outward_igst": out_igst,
        "credit_note_taxable": cn_taxable,
        "credit_note_cgst": cn_cgst,
        "credit_note_sgst": cn_sgst,
        "credit_note_igst": cn_igst,
        "net_taxable": money_round(out_taxable - cn_taxable),
        "net_cgst": net_cgst,
        "net_sgst": net_sgst,
        "net_igst": net_igst,
        "net_total_gst": money_round(net_cgst + net_sgst + net_igst),
    }


def get_period_summary(*, target_date: date | None = None) -> dict:
    """Daily & monthly operational snapshot distinguishing sales, collections, KNET, and expenses."""
    today = target_date or timezone.localdate()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    valid_inv = Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    daily_sales = money_round(valid_inv.filter(invoice_date=today).aggregate(t=Sum("grand_total"))["t"] or Decimal("0.000"))
    yesterday_sales = money_round(valid_inv.filter(invoice_date=yesterday).aggregate(t=Sum("grand_total"))["t"] or Decimal("0.000"))
    monthly_sales_total = money_round(
        valid_inv.filter(invoice_date__gte=month_start, invoice_date__lte=today).aggregate(t=Sum("grand_total"))["t"] or Decimal("0.000")
    )
    last_month_sales_total = money_round(
        valid_inv.filter(invoice_date__gte=last_month_start, invoice_date__lte=last_month_end).aggregate(t=Sum("grand_total"))["t"]
        or Decimal("0.000")
    )

    sales_vs_yesterday_diff = money_round(daily_sales - yesterday_sales)
    sales_vs_yesterday_pct = (
        round(float(((daily_sales - yesterday_sales) / yesterday_sales) * Decimal("100")), 1)
        if yesterday_sales > Decimal("0.000")
        else None
    )
    sales_vs_last_month_diff = money_round(monthly_sales_total - last_month_sales_total)
    sales_vs_last_month_pct = (
        round(float(((monthly_sales_total - last_month_sales_total) / last_month_sales_total) * Decimal("100")), 1)
        if last_month_sales_total > Decimal("0.000")
        else None
    )

    valid_pay = CustomerPayment.objects.filter(is_reversed=False)
    daily_collections = money_round(valid_pay.filter(payment_date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.000"))
    knet_collections = money_round(
        valid_pay.filter(payment_mode=CustomerPayment.PaymentMode.KNET).aggregate(t=Sum("amount"))["t"] or Decimal("0.000")
    )

    daily_expenses = money_round(Expense.objects.filter(expense_date=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.000"))
    monthly_expenses = money_round(
        Expense.objects.filter(expense_date__gte=month_start, expense_date__lte=today).aggregate(t=Sum("amount"))["t"] or Decimal("0.000")
    )
    daily_purchases = money_round(GoodsReceipt.objects.filter(received_date=today).aggregate(t=Sum("total_amount"))["t"] or Decimal("0.000"))

    monthly_sales = list(
        valid_inv.annotate(month=TruncMonth("invoice_date"))
        .values("month")
        .annotate(total=Sum("grand_total"))
        .order_by("-month")[:12]
    )
    for row in monthly_sales:
        row["total"] = money_round(row["total"] or Decimal("0.000"))

    return {
        "target_date": today,
        "daily_sales": daily_sales,
        "yesterday_sales": yesterday_sales,
        "sales_vs_yesterday_diff": sales_vs_yesterday_diff,
        "sales_vs_yesterday_pct": sales_vs_yesterday_pct,
        "monthly_sales_total": monthly_sales_total,
        "last_month_sales_total": last_month_sales_total,
        "sales_vs_last_month_diff": sales_vs_last_month_diff,
        "sales_vs_last_month_pct": sales_vs_last_month_pct,
        "daily_collections": daily_collections,
        "knet_collections": knet_collections,
        "daily_expenses": daily_expenses,
        "monthly_expenses": monthly_expenses,
        "daily_purchases": daily_purchases,
        "monthly_sales": monthly_sales,
    }
