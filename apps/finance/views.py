from datetime import date
from django.shortcuts import render
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission, RoleRequiredMixin
from .services import get_gst_summary, get_ledger_book, get_period_summary, get_profit_and_loss

FINANCE_ROLES = (User.Role.OWNER, User.Role.MANAGER)


def _parse_dates(request):
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
    return start_date, end_date


class FinanceOverviewView(RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES
    template_name = "finance/overview.html"

    def get(self, request):
        start_date, end_date = _parse_dates(request)
        ctx = {
            "cash_book": get_ledger_book(modes=("CASH",), start_date=start_date, end_date=end_date),
            "bank_book": get_ledger_book(modes=("BANK", "KNET", "CHEQUE", "OTHER"), start_date=start_date, end_date=end_date),
            "pnl": get_profit_and_loss(start_date=start_date, end_date=end_date),
            "gst": get_gst_summary(start_date=start_date, end_date=end_date),
            "summary": get_period_summary(),
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
        }
        return render(request, self.template_name, ctx)


class CashBookView(RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES
    template_name = "finance/ledger_book.html"

    def get(self, request):
        start_date, end_date = _parse_dates(request)
        book = get_ledger_book(modes=("CASH",), start_date=start_date, end_date=end_date)
        return render(request, self.template_name, {"title": "Cash Book", "book": book})


class BankBookView(RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES
    template_name = "finance/ledger_book.html"

    def get(self, request):
        start_date, end_date = _parse_dates(request)
        book = get_ledger_book(modes=("BANK", "KNET", "CHEQUE", "OTHER"), start_date=start_date, end_date=end_date)
        return render(request, self.template_name, {"title": "Bank Book (Bank / KNET / Cheque / Other)", "book": book})


class FinanceSummaryAPIView(APIView):
    permission_classes = [HasRolePermission]
    allowed_roles = FINANCE_ROLES

    def get(self, request):
        start_date, end_date = _parse_dates(request)
        return Response(
            {
                "pnl": {k: str(v) for k, v in get_profit_and_loss(start_date=start_date, end_date=end_date).items()},
                "gst": {k: str(v) for k, v in get_gst_summary(start_date=start_date, end_date=end_date).items()},
                "cash_closing": str(get_ledger_book(modes=("CASH",), start_date=start_date, end_date=end_date)["closing_balance"]),
                "bank_closing": str(get_ledger_book(modes=("BANK", "KNET", "CHEQUE", "OTHER"), start_date=start_date, end_date=end_date)["closing_balance"]),
            }
        )
