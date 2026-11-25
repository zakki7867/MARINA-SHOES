from decimal import Decimal
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.utils import money_round
from apps.invoicing.models import Refund
from .forms import CustomerForm
from .models import Customer

CUSTOMER_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)


class CustomerListView(RoleRequiredMixin, ListView):
    allowed_roles = CUSTOMER_ROLES
    model = Customer
    template_name = "customers/customer_list.html"
    context_object_name = "customers"
    paginate_by = 25

    def get_queryset(self):
        qs = Customer.objects.with_balances()
        q = self.request.GET.get("q", "").strip()
        ctype = self.request.GET.get("type", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(gstin__icontains=q) | Q(city__icontains=q))
        if ctype:
            qs = qs.filter(customer_type=ctype)
        return qs


class CustomerCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = CUSTOMER_ROLES
    model = Customer
    form_class = CustomerForm
    template_name = "customers/customer_form.html"
    success_url = reverse_lazy("customers:customer_list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=self.object,
            changes={"name": self.object.name, "type": self.object.customer_type},
            request=self.request,
            notes="Created customer",
        )
        messages.success(self.request, f"Customer '{self.object.name}' added.")
        return resp


class CustomerUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = CUSTOMER_ROLES
    model = Customer
    form_class = CustomerForm
    template_name = "customers/customer_form.html"
    success_url = reverse_lazy("customers:customer_list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            changes={"name": self.object.name, "credit_limit": self.object.credit_limit},
            request=self.request,
            notes="Updated customer",
        )
        return resp


class CustomerDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = CUSTOMER_ROLES
    model = Customer
    template_name = "customers/customer_detail.html"
    context_object_name = "customer"


class CustomerSoftDeleteView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER)

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        customer.soft_delete()
        log_audit(
            user=request.user,
            action=AuditLog.Action.DELETE,
            instance=customer,
            request=request,
            notes="Soft-deleted customer",
        )
        messages.success(request, f"Customer '{customer.name}' archived.")
        return redirect("customers:customer_list")


class CustomerLedgerView(RoleRequiredMixin, View):
    allowed_roles = CUSTOMER_ROLES
    template_name = "customers/customer_ledger.html"

    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        entries = []
        for inv in customer.invoices.filter(status__in=["ISSUED", "PARTIAL", "PAID"]):
            entries.append({
                "date": inv.invoice_date,
                "ref": inv.invoice_number,
                "desc": "Tax Invoice",
                "debit": money_round(inv.grand_total),
                "credit": Decimal("0.00"),
                "sort": (inv.invoice_date, 1, inv.id),
            })
        for cn in customer.credit_notes.all():
            entries.append({
                "date": cn.issue_date,
                "ref": cn.credit_note_number,
                "desc": f"Credit Note ({cn.invoice.invoice_number})",
                "debit": Decimal("0.00"),
                "credit": money_round(cn.total_amount),
                "sort": (cn.issue_date, 2, cn.id),
            })
        for ref in Refund.objects.filter(customer=customer):
            entries.append({
                "date": ref.refund_date,
                "ref": ref.refund_number,
                "desc": f"Refund Paid ({ref.get_payment_mode_display()})",
                "debit": money_round(ref.amount),
                "credit": Decimal("0.00"),
                "sort": (ref.refund_date, 3, ref.id),
            })
        for pay in customer.customer_payments.filter(is_reversed=False):
            entries.append({
                "date": pay.payment_date,
                "ref": pay.receipt_number,
                "desc": f"Payment Received ({pay.get_payment_mode_display()})",
                "debit": Decimal("0.00"),
                "credit": money_round(pay.amount),
                "sort": (pay.payment_date, 4, pay.id),
            })
        entries.sort(key=lambda e: e["sort"])
        running = money_round(customer.opening_balance)
        for e in entries:
            running = money_round(running + e["debit"] - e["credit"])
            e["balance"] = running
        return render(request, self.template_name, {"customer": customer, "entries": entries, "closing_balance": running})
