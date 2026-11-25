import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
import openpyxl

from apps.core.utils import money_round
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.finance.services import get_gst_summary, get_profit_and_loss
from apps.inventory.models import ProductVariant, StockBalance, StockMovement
from apps.invoicing.models import Invoice, InvoiceItem
from apps.payments.services import get_customer_aging, get_supplier_aging
from apps.purchases.models import Supplier


def export_csv_response(filename: str, headers: list[str], rows: list[list]) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(resp)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return resp


def export_excel_response(filename: str, title: str, headers: list[str], rows: list[list]) -> HttpResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        clean_row = [float(v) if isinstance(v, Decimal) else v for v in row]
        ws.append(clean_row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return resp


def report_stock() -> tuple[str, list[str], list[list]]:
    headers = ["SKU", "Product", "Brand", "Category", "Size", "Color", "Stock", "Cost Price", "Stock Value", "Wholesale Price", "Retail Price"]
    rows = []
    for v in ProductVariant.objects.select_related("product", "product__brand", "product__category").with_stock():
        qty = int(v.calculated_stock or 0)
        val = money_round(Decimal(max(qty, 0)) * v.cost_price)
        rows.append([v.sku, v.product.name, v.product.brand.name, v.product.category.name, v.size, v.color, qty, v.cost_price, val, v.wholesale_price, v.retail_price])
    return "Stock Report", headers, rows


def report_stock_by_dimension(dim: str) -> tuple[str, list[str], list[list]]:
    totals: dict[str, int] = {}
    for v in ProductVariant.objects.with_stock():
        key = getattr(v, dim)
        totals[key] = totals.get(key, 0) + int(v.calculated_stock or 0)
    label = dim.capitalize()
    rows = [[k, qty] for k, qty in sorted(totals.items())]
    return f"Stock by {label}", [label, "Total Stock"], rows


def report_stock_by_location() -> tuple[str, list[str], list[list]]:
    headers = ["Location Code", "Location Name", "SKU", "Product", "Size", "Color", "Quantity"]
    rows = [
        [b.location.code, b.location.name, b.variant.sku, b.variant.product.name, b.variant.size, b.variant.color, b.quantity]
        for b in StockBalance.objects.select_related("location", "variant", "variant__product").order_by("location__code", "variant__sku")
    ]
    return "Stock by Location", headers, rows


def report_low_stock() -> tuple[str, list[str], list[list]]:
    headers = ["SKU", "Product", "Brand", "Size", "Color", "Current Stock", "Reorder Level", "Shortfall"]
    rows = []
    for v in ProductVariant.objects.select_related("product", "product__brand").low_stock():
        qty = int(v.calculated_stock or 0)
        rows.append([v.sku, v.product.name, v.product.brand.name, v.size, v.color, qty, v.reorder_level, max(v.reorder_level - qty, 0)])
    return "Low Stock Report", headers, rows


def report_stock_movements(start_date: date | None = None, end_date: date | None = None, movement_type: str | None = None) -> tuple[str, list[str], list[list]]:
    qs = StockMovement.objects.select_related("variant", "location", "user").all()
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    if movement_type:
        qs = qs.filter(movement_type=movement_type)
    title = "Damaged Stock Report" if movement_type == StockMovement.MovementType.DAMAGE else "Stock Movement Report"
    headers = ["Timestamp", "Location", "SKU", "Type", "Delta", "Reference", "Reason", "User"]
    rows = [
        [m.created_at.strftime("%Y-%m-%d %H:%M"), m.location.code if m.location else "MAIN", m.variant.sku, m.get_movement_type_display(), m.signed_quantity, m.reference, m.reason, m.user.username]
        for m in qs[:500]
    ]
    return title, headers, rows


def report_daily_or_monthly_sales(monthly: bool = False, start_date: date | None = None, end_date: date | None = None) -> tuple[str, list[str], list[list]]:
    qs = Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    if start_date:
        qs = qs.filter(invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice_date__lte=end_date)
    if monthly:
        agg = qs.annotate(period=TruncMonth("invoice_date")).values("period").annotate(cnt=Count("id"), taxable=Sum("taxable_amount"), total=Sum("grand_total")).order_by("-period")
        headers = ["Month", "Invoices", "Taxable Sales", "Grand Total"]
        rows = [[r["period"].strftime("%Y-%m"), r["cnt"], money_round(r["taxable"] or 0), money_round(r["total"] or 0)] for r in agg]
        return "Monthly Sales Report", headers, rows
    agg = qs.values("invoice_date").annotate(cnt=Count("id"), taxable=Sum("taxable_amount"), total=Sum("grand_total")).order_by("-invoice_date")
    headers = ["Date", "Invoices", "Taxable Sales", "Grand Total"]
    rows = [[r["invoice_date"].isoformat(), r["cnt"], money_round(r["taxable"] or 0), money_round(r["total"] or 0)] for r in agg]
    return "Daily Sales Report", headers, rows


def report_sales_by_product(start_date: date | None = None, end_date: date | None = None) -> tuple[str, list[str], list[list]]:
    qs = InvoiceItem.objects.filter(invoice__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID]).select_related("variant", "variant__product")
    if start_date:
        qs = qs.filter(invoice__invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice__invoice_date__lte=end_date)
    agg = qs.values("variant__sku", "variant__product__name", "variant__color", "variant__size").annotate(qty_sold=Sum("quantity"), taxable=Sum("taxable_value"), total=Sum("line_total")).order_by("-total")
    headers = ["SKU", "Product", "Color", "Size", "Units Sold", "Taxable Sales", "Total Sales (Incl. Tax)"]
    rows = [[r["variant__sku"], r["variant__product__name"], r["variant__color"], r["variant__size"], r["qty_sold"] or 0, money_round(r["taxable"] or 0), money_round(r["total"] or 0)] for r in agg]
    return "Sales by Product", headers, rows


def report_sales_by_group(group_field: str, title: str, label: str) -> tuple[str, list[str], list[list]]:
    qs = InvoiceItem.objects.filter(invoice__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    agg = qs.values(group_field).annotate(qty=Sum("quantity"), total=Sum("line_total")).order_by("-total")
    return title, [label, "Units Sold", "Total Sales"], [[r[group_field], r["qty"] or 0, money_round(r["total"] or 0)] for r in agg]


def report_wholesale_vs_retail() -> tuple[str, list[str], list[list]]:
    qs = Invoice.objects.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
    agg = qs.values("price_list").annotate(cnt=Count("id"), taxable=Sum("taxable_amount"), total=Sum("grand_total")).order_by("price_list")
    return "Wholesale vs Retail Sales", ["Price List", "Invoices", "Taxable Sales", "Total Sales"], [[r["price_list"], r["cnt"], money_round(r["taxable"] or 0), money_round(r["total"] or 0)] for r in agg]


def report_slow_moving() -> tuple[str, list[str], list[list]]:
    cutoff = timezone.localdate() - timedelta(days=30)
    sold_map = {
        r["variant_id"]: r["qty"]
        for r in InvoiceItem.objects.filter(
            invoice__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID],
            invoice__invoice_date__gte=cutoff,
        ).values("variant_id").annotate(qty=Sum("quantity"))
    }
    rows = []
    for v in ProductVariant.objects.select_related("product").with_stock():
        stock = int(v.calculated_stock or 0)
        sold_30d = int(sold_map.get(v.id, 0) or 0)
        if stock > 0:
            rows.append([v.sku, v.product.name, v.size, v.color, stock, sold_30d])
    rows.sort(key=lambda r: (r[5], -r[4]))
    return "Slow-Moving Products (30 Days)", ["SKU", "Product", "Size", "Color", "Current Stock", "Units Sold (30d)"], rows


def report_sales_by_customer(top_only: bool = False) -> tuple[str, list[str], list[list]]:
    headers = ["Customer", "Type", "Phone", "Invoices", "Total Invoiced", "Credited", "Paid", "Outstanding Balance"]
    rows = []
    for c in Customer.objects.with_balances().annotate(
        inv_count=Count("invoices", filter=Q(invoices__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID]), distinct=True)
    ):
        rows.append([c.name, c.get_customer_type_display(), c.phone, c.inv_count, c.total_invoiced, c.total_credit_notes, c.total_paid, c.outstanding_balance])
    if top_only:
        rows.sort(key=lambda r: r[4], reverse=True)
        return "Top Customers", headers, rows[:20]
    rows.sort(key=lambda r: r[0])
    return "Sales by Customer", headers, rows


def report_pending_payments() -> tuple[str, list[str], list[list]]:
    today = timezone.localdate()
    headers = ["Invoice #", "Customer", "Phone", "Invoice Date", "Due Date", "Days Overdue", "Grand Total", "Balance Due"]
    rows = []
    for inv in Invoice.objects.select_related("customer").filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL]).order_by("due_date"):
        bal = inv.balance_due
        if bal > Decimal("0.00"):
            rows.append([inv.invoice_number, inv.customer.name, inv.customer.phone, inv.invoice_date.isoformat(), inv.due_date.isoformat(), max((today - inv.due_date).days, 0), inv.grand_total, bal])
    return "Pending Payments", headers, rows


def report_supplier_outstanding() -> tuple[str, list[str], list[list]]:
    headers = ["Supplier", "Phone", "Opening", "Purchased", "Returned", "Paid", "Outstanding Payable"]
    rows = [[s.name, s.phone, s.opening_balance, s.total_purchased, s.total_returned, s.total_paid, s.outstanding_balance] for s in Supplier.objects.with_balances()]
    return "Supplier Outstanding", headers, rows


def report_aging(is_supplier: bool = False) -> tuple[str, list[str], list[list]]:
    if is_supplier:
        rows = [[r["supplier"].name, r["b_0_30"], r["b_31_60"], r["b_61_90"], r["b_90_plus"], r["total"]] for r in get_supplier_aging()]
        return "Supplier Aging Report", ["Supplier", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days", "Total"], rows
    rows = [[r["customer"].name, r["b_0_30"], r["b_31_60"], r["b_61_90"], r["b_90_plus"], r["total"]] for r in get_customer_aging()]
    return "Customer Aging Report", ["Customer", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days", "Total"], rows


def report_expenses(start_date: date | None = None, end_date: date | None = None) -> tuple[str, list[str], list[list]]:
    qs = Expense.objects.all()
    if start_date:
        qs = qs.filter(expense_date__gte=start_date)
    if end_date:
        qs = qs.filter(expense_date__lte=end_date)
    headers = ["Date", "Category", "Payment Mode", "Reference", "Notes", "Amount"]
    rows = [[e.expense_date.isoformat(), e.get_category_display(), e.get_payment_mode_display(), e.reference_no, e.notes, e.amount] for e in qs]
    return "Expense Report", headers, rows


def report_profit(start_date: date | None = None, end_date: date | None = None) -> tuple[str, list[str], list[list]]:
    pnl = get_profit_and_loss(start_date=start_date, end_date=end_date)
    headers = ["Metric", "Amount"]
    rows = [
        ["Gross Sales (Ex-Tax)", pnl["gross_sales_taxable"]],
        ["Less Credit Notes (Ex-Tax)", pnl["returns_taxable"]],
        ["Net Revenue", pnl["net_revenue"]],
        ["Cost of Goods Sold (COGS)", pnl["cogs"]],
        ["Gross Profit", pnl["gross_profit"]],
        ["Operating Expenses", pnl["total_expenses"]],
        ["Net Profit", pnl["net_profit"]],
    ]
    return "Profit Report", headers, rows


def report_gst(start_date: date | None = None, end_date: date | None = None) -> tuple[str, list[str], list[list]]:
    gst = get_gst_summary(start_date=start_date, end_date=end_date)
    rows = [
        ["Outward Taxable", gst["outward_taxable"]],
        ["Credit Note Taxable", gst["credit_note_taxable"]],
        ["Net Taxable Value", gst["net_taxable"]],
        ["Net CGST", gst["net_cgst"]],
        ["Net SGST", gst["net_sgst"]],
        ["Net IGST", gst["net_igst"]],
        ["Net Total GST", gst["net_total_gst"]],
    ]
    return "Tax Summary Report", ["Component", "Amount"], rows
