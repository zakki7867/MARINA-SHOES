from decimal import Decimal
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.core.utils import money_round
from apps.finance.services import get_ledger_book, get_period_summary, get_profit_and_loss
from apps.inventory.models import StockMovement
from apps.inventory.services import (
    get_low_stock_variants,
    get_out_of_stock_count,
    get_stock_value_by_category,
    get_total_stock_value,
)
from apps.invoicing.models import Invoice, InvoiceItem
from apps.payments.services import get_overdue_invoices, get_total_pending_receivables
from apps.purchases.models import Supplier


class DashboardHomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        summary = get_period_summary()
        pnl = get_profit_and_loss()
        cash_book = get_ledger_book(modes=("CASH",))
        bank_book = get_ledger_book(modes=("BANK", "KNET", "CHEQUE", "OTHER"))

        monthly_desc = list(summary["monthly_sales"])
        monthly_asc = list(reversed(monthly_desc))
        max_sales = max([m["total"] for m in monthly_asc], default=Decimal("0.00"))
        chart_bars = [
            {
                "label": m["month"].strftime("%b %Y") if m["month"] else "",
                "total": m["total"],
                "pct": max(int((m["total"] / max_sales) * 100), 6) if (max_sales > 0 and m["total"] > 0) else 0,
            }
            for m in monthly_asc
        ]

        total_qty = StockMovement.objects.aggregate(t=Coalesce(Sum("signed_quantity"), Value(0)))["t"] or 0

        top_products = list(
            InvoiceItem.objects.filter(invoice__status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
            .values("variant__sku", "variant__product__name")
            .annotate(units=Sum("quantity"), rev=Sum("line_total"))
            .order_by("-units")[:5]
        )
        for tp in top_products:
            tp["rev"] = money_round(tp["rev"] or 0)

        recent_invoices = Invoice.objects.select_related("customer").filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID]).order_by("-id")[:5]

        can_view_finance = self.request.user.has_any_role(User.Role.OWNER, User.Role.MANAGER)
        can_view_stock_value = self.request.user.has_any_role(
            User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER
        )
        can_view_receivables = self.request.user.has_any_role(
            User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF
        )

        today = summary["target_date"]
        todays_pnl = get_profit_and_loss(start_date=today, end_date=today) if can_view_finance else None

        suppliers_with_bal = list(Supplier.objects.with_balances()) if can_view_finance else []
        supplier_payables = money_round(sum((max(s.outstanding_balance, Decimal("0.000")) for s in suppliers_with_bal), Decimal("0.000")))
        upcoming_suppliers = sorted(
            [s for s in suppliers_with_bal if s.outstanding_balance > Decimal("0.000")],
            key=lambda s: s.outstanding_balance,
            reverse=True,
        )[:6]

        low_stock_qs = get_low_stock_variants()
        low_stock_items = list(low_stock_qs.order_by("calculated_stock", "sku")[:6])

        pending_customer_invoices = []
        if can_view_receivables:
            open_invoices = (
                Invoice.objects.select_related("customer")
                .filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL])
                .order_by("due_date", "-id")[:25]
            )
            for inv in open_invoices:
                if inv.balance_due > Decimal("0.000"):
                    inv.is_overdue_flag = bool(inv.due_date and inv.due_date < today)
                    pending_customer_invoices.append(inv)
                if len(pending_customer_invoices) >= 6:
                    break

        ctx.update(
            {
                "can_view_finance": can_view_finance,
                "can_view_stock_value": can_view_stock_value,
                "can_view_receivables": can_view_receivables,
                "todays_sales": summary["daily_sales"],
                "yesterday_sales": summary["yesterday_sales"],
                "sales_vs_yesterday_diff": summary["sales_vs_yesterday_diff"],
                "sales_vs_yesterday_pct": summary["sales_vs_yesterday_pct"],
                "todays_collections": summary["daily_collections"],
                "monthly_sales_total": summary["monthly_sales_total"],
                "last_month_sales_total": summary["last_month_sales_total"],
                "sales_vs_last_month_diff": summary["sales_vs_last_month_diff"],
                "sales_vs_last_month_pct": summary["sales_vs_last_month_pct"],
                "todays_expenses": summary["daily_expenses"] if can_view_finance else None,
                "monthly_expenses": summary["monthly_expenses"] if can_view_finance else None,
                "knet_collections": summary["knet_collections"],
                "gross_profit_estimate": pnl["gross_profit"] if can_view_finance else None,
                "todays_gross_profit": todays_pnl["gross_profit"] if todays_pnl else None,
                "todays_net_profit": todays_pnl["net_profit"] if todays_pnl else None,
                "inventory_qty": int(total_qty),
                "stock_value": get_total_stock_value() if can_view_stock_value else None,
                "stock_by_category": get_stock_value_by_category()[:6] if can_view_stock_value else [],
                "pending_receivables": get_total_pending_receivables(),
                "supplier_payables": supplier_payables if can_view_finance else None,
                "upcoming_suppliers": upcoming_suppliers,
                "cash_balance": cash_book["closing_balance"] if can_view_finance else None,
                "bank_balance": bank_book["closing_balance"] if can_view_finance else None,
                "low_stock_count": low_stock_qs.count(),
                "out_of_stock_count": get_out_of_stock_count(),
                "low_stock_items": low_stock_items,
                "overdue_count": len(get_overdue_invoices()),
                "pending_customer_invoices": pending_customer_invoices,
                "top_products": top_products,
                "recent_invoices": recent_invoices,
                "chart_bars": chart_bars,
            }
        )
        return ctx
