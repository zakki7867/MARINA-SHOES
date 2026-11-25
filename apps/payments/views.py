from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.customers.models import Customer
from .forms import CustomerPaymentForm
from .models import CustomerPayment
from .services import (
    get_customer_aging,
    get_overdue_invoices,
    get_supplier_aging,
    record_customer_payment,
    reverse_customer_payment,
)

PAYMENT_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)


class PaymentListCreateView(RoleRequiredMixin, View):
    allowed_roles = PAYMENT_ROLES
    template_name = "payments/payment_list.html"

    def get(self, request):
        initial = {"payment_date": timezone.localdate()}
        if request.GET.get("customer"):
            initial["customer"] = request.GET.get("customer")
        if request.GET.get("invoice"):
            initial["invoice"] = request.GET.get("invoice")
        form = CustomerPaymentForm(initial=initial)
        payments = CustomerPayment.objects.select_related("customer", "created_by").prefetch_related("allocations__invoice")[:50]
        return render(request, self.template_name, {"form": form, "payments": payments})

    def post(self, request):
        form = CustomerPaymentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                payment = record_customer_payment(
                    customer=cd["customer"],
                    invoice=cd.get("invoice"),
                    amount=cd["amount"],
                    payment_mode=cd["payment_mode"],
                    is_advance=cd.get("is_advance", False),
                    payment_date=cd["payment_date"],
                    reference_no=cd.get("reference_no", ""),
                    notes=cd.get("notes", ""),
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Payment {payment.receipt_number} recorded.")
                return redirect("payments:payment_list")
            except ValidationError as exc:
                form.add_error(None, str(exc))
        payments = CustomerPayment.objects.select_related("customer", "created_by").prefetch_related("allocations__invoice")[:50]
        return render(request, self.template_name, {"form": form, "payments": payments})


class PaymentReverseView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER)

    def post(self, request, pk):
        payment = get_object_or_404(CustomerPayment, pk=pk)
        reason = request.POST.get("reason", "").strip() or "Payment correction"
        try:
            reverse_customer_payment(payment=payment, user=request.user, reason=reason, request=request)
            messages.success(request, f"Payment {payment.receipt_number} reversed.")
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("payments:payment_list")


class CreditLedgerView(RoleRequiredMixin, ListView):
    allowed_roles = PAYMENT_ROLES
    template_name = "payments/credit_ledger.html"
    context_object_name = "customers"

    def get_queryset(self):
        return Customer.objects.with_balances().order_by("name")


class OverdueListView(RoleRequiredMixin, View):
    allowed_roles = PAYMENT_ROLES
    template_name = "payments/overdue_list.html"

    def get(self, request):
        overdue = get_overdue_invoices()
        return render(request, self.template_name, {"overdue_invoices": overdue})


class AgingReportView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER)
    template_name = "payments/aging.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "customer_aging": get_customer_aging(),
                "supplier_aging": get_supplier_aging(),
            },
        )
