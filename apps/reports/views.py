from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.inventory.models import StockMovement
from .services import (
    export_csv_response,
    export_excel_response,
    report_aging,
    report_daily_or_monthly_sales,
    report_expenses,
    report_gst,
    report_low_stock,
    report_pending_payments,
    report_profit,
    report_sales_by_customer,
    report_sales_by_group,
    report_sales_by_product,
    report_slow_moving,
    report_stock,
    report_stock_by_dimension,
    report_stock_by_location,
    report_stock_movements,
    report_supplier_outstanding,
    report_wholesale_vs_retail,
)

INV_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER)
FIN_ROLES = (User.Role.OWNER, User.Role.MANAGER)

REPORT_CARDS = [
    {"key": "stock", "title": _("Stock Report"), "desc": _("All variants, current stock, cost & valuation"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "stock_by_size", "title": _("Stock by Size"), "desc": _("Total pairs grouped by shoe size"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "stock_by_color", "title": _("Stock by Color"), "desc": _("Total pairs grouped by color"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "stock_by_location", "title": _("Stock by Location"), "desc": _("Variant stock per store & warehouse"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "low_stock", "title": _("Low Stock Report"), "desc": _("Variants at or below reorder level"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "stock_movements", "title": _("Stock Movements"), "desc": _("Full immutable stock ledger history"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "damaged_stock", "title": _("Damaged Stock"), "desc": _("Damaged pairs written off"), "roles": INV_ROLES, "group": "Inventory"},
    {"key": "slow_moving", "title": _("Slow-Moving Stock"), "desc": _("In-stock SKUs with lowest 30-day sales"), "roles": FIN_ROLES, "group": "Inventory"},
    {"key": "daily_sales", "title": _("Daily Sales"), "desc": _("Day-by-day invoice count & revenue"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "monthly_sales", "title": _("Monthly Sales"), "desc": _("Month-by-month sales summary"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "sales_by_product", "title": _("Sales by Product"), "desc": _("Units sold and revenue per SKU"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "sales_by_category", "title": _("Sales by Category"), "desc": _("Revenue grouped by footwear category"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "sales_by_brand", "title": _("Sales by Brand"), "desc": _("Revenue grouped by brand"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "wholesale_vs_retail", "title": _("Wholesale vs Retail"), "desc": _("Comparison of B2B wholesale vs retail POS"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "sales_by_customer", "title": _("Sales by Customer"), "desc": _("Invoiced, credited, paid & balances"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "top_customers", "title": _("Top Customers"), "desc": _("Highest-billing wholesale & retail customers"), "roles": FIN_ROLES, "group": "Sales"},
    {"key": "pending_payments", "title": _("Pending Payments"), "desc": _("Unpaid & overdue customer invoices"), "roles": FIN_ROLES, "group": "Finance"},
    {"key": "customer_aging", "title": _("Customer Aging"), "desc": _("0-30, 31-60, 61-90, 90+ days receivables"), "roles": FIN_ROLES, "group": "Finance"},
    {"key": "supplier_outstanding", "title": _("Supplier Outstanding"), "desc": _("Payable balances across all suppliers"), "roles": FIN_ROLES, "group": "Purchases"},
    {"key": "supplier_aging", "title": _("Supplier Aging"), "desc": _("0-30, 31-60, 61-90, 90+ days payables"), "roles": FIN_ROLES, "group": "Purchases"},
    {"key": "expenses", "title": _("Expense Report"), "desc": _("Categorized store operating expenses"), "roles": FIN_ROLES, "group": "Finance"},
    {"key": "profit", "title": _("Profit Report"), "desc": _("Net revenue, COGS, expenses & net profit"), "roles": FIN_ROLES, "group": "Finance"},
    {"key": "gst_summary", "title": _("Tax Summary Report"), "desc": _("Outward sales & tax less credit notes"), "roles": FIN_ROLES, "group": "Finance"},
]

REPORT_GROUPS = [
    ("Sales", "sales-reports", _("Sales Reports")),
    ("Inventory", "inventory-reports", _("Inventory Reports")),
    ("Purchases", "purchase-reports", _("Purchase Reports")),
    ("Finance", "financial-reports", _("Financial Reports")),
]


class ReportIndexView(RoleRequiredMixin, View):
    allowed_roles = INV_ROLES
    template_name = "reports/index.html"

    def get(self, request):
        cards = [c for c in REPORT_CARDS if request.user.has_any_role(*c["roles"])]
        report_groups = []
        for key, anchor, heading in REPORT_GROUPS:
            items = [c for c in cards if c["group"] == key]
            if items:
                report_groups.append({"key": key, "anchor": anchor, "heading": heading, "items": items})
        return render(request, self.template_name, {"reports": cards, "report_groups": report_groups})


class ReportDetailView(RoleRequiredMixin, View):
    allowed_roles = INV_ROLES
    template_name = "reports/detail.html"

    def get(self, request, report_key: str):
        card = next((c for c in REPORT_CARDS if c["key"] == report_key), None)
        if not card:
            cards = [c for c in REPORT_CARDS if request.user.has_any_role(*c["roles"])]
            return render(request, "reports/index.html", {"reports": cards})
        if not request.user.has_any_role(*card["roles"]):
            raise PermissionDenied("Your role cannot access this financial report.")

        start_str = request.GET.get("start_date", "").strip()
        end_str = request.GET.get("end_date", "").strip()
        try:
            start_date = date.fromisoformat(start_str) if start_str else None
        except ValueError:
            start_date = None
        try:
            end_date = date.fromisoformat(end_str) if end_str else None
        except ValueError:
            end_date = None
        q = request.GET.get("q", "").strip().lower()
        sort_idx = request.GET.get("sort", "").strip()

        builders = {
            "stock": lambda: report_stock(),
            "stock_by_size": lambda: report_stock_by_dimension("size"),
            "stock_by_color": lambda: report_stock_by_dimension("color"),
            "stock_by_location": lambda: report_stock_by_location(),
            "low_stock": lambda: report_low_stock(),
            "stock_movements": lambda: report_stock_movements(start_date, end_date),
            "damaged_stock": lambda: report_stock_movements(start_date, end_date, StockMovement.MovementType.DAMAGE),
            "slow_moving": lambda: report_slow_moving(),
            "daily_sales": lambda: report_daily_or_monthly_sales(False, start_date, end_date),
            "monthly_sales": lambda: report_daily_or_monthly_sales(True, start_date, end_date),
            "sales_by_product": lambda: report_sales_by_product(start_date, end_date),
            "sales_by_category": lambda: report_sales_by_group("variant__product__category__name", "Sales by Category", "Category"),
            "sales_by_brand": lambda: report_sales_by_group("variant__product__brand__name", "Sales by Brand", "Brand"),
            "wholesale_vs_retail": lambda: report_wholesale_vs_retail(),
            "sales_by_customer": lambda: report_sales_by_customer(top_only=False),
            "top_customers": lambda: report_sales_by_customer(top_only=True),
            "pending_payments": lambda: report_pending_payments(),
            "customer_aging": lambda: report_aging(is_supplier=False),
            "supplier_outstanding": lambda: report_supplier_outstanding(),
            "supplier_aging": lambda: report_aging(is_supplier=True),
            "expenses": lambda: report_expenses(start_date, end_date),
            "profit": lambda: report_profit(start_date, end_date),
            "gst_summary": lambda: report_gst(start_date, end_date),
        }
        title, headers, rows = builders[report_key]()

        if q:
            rows = [r for r in rows if any(q in str(cell).lower() for cell in r)]

        if sort_idx.isdigit() and int(sort_idx) < len(headers):
            idx = int(sort_idx)

            def _sort_key(row):
                val = row[idx]
                if isinstance(val, (int, float, Decimal)):
                    return (0, Decimal(str(val)), "")
                try:
                    return (0, Decimal(str(val)), "")
                except (InvalidOperation, ValueError, TypeError):
                    return (1, Decimal("0"), str(val).lower())

            rows = sorted(rows, key=_sort_key)

        export_fmt = request.GET.get("export", "").lower()
        if export_fmt == "csv":
            return export_csv_response(f"marina_{report_key}", headers, rows)
        if export_fmt == "xlsx":
            return export_excel_response(f"marina_{report_key}", title, headers, rows)

        paginator = Paginator(rows, 50)
        page_obj = paginator.get_page(request.GET.get("page"))

        return render(
            request,
            self.template_name,
            {
                "report_key": report_key,
                "title": title,
                "headers": headers,
                "rows": page_obj.object_list,
                "page_obj": page_obj,
                "total_count": len(rows),
                "start_date": start_str,
                "end_date": end_str,
                "sort": sort_idx,
                "q": request.GET.get("q", ""),
                "supports_dates": report_key in ("stock_movements", "damaged_stock", "daily_sales", "monthly_sales", "sales_by_product", "expenses", "profit", "gst_summary"),
            },
        )
