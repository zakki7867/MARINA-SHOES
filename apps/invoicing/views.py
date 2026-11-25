from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.core.models import StoreLocation, StoreSettings
from apps.inventory.services import InsufficientStockError
from .forms import InvoiceCreateForm, InvoiceLineFormSet
from .models import CreditNote, Invoice
from .services import (
    CreditLimitExceededError,
    cancel_invoice,
    create_invoice,
    issue_invoice,
    render_invoice_pdf_bytes,
)

INVOICE_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)
INVOICE_ADMIN_ROLES = (User.Role.OWNER, User.Role.MANAGER)


class InvoiceListView(RoleRequiredMixin, ListView):
    allowed_roles = INVOICE_ROLES
    model = Invoice
    template_name = "invoicing/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 25

    def get_queryset(self):
        qs = Invoice.objects.select_related("customer", "created_by").all()
        q = self.request.GET.get("q", "").strip()
        status_filter = self.request.GET.get("status", "").strip()
        if q:
            qs = qs.filter(Q(invoice_number__icontains=q) | Q(customer__name__icontains=q))
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = Invoice.Status.choices
        return ctx


class InvoiceCreateView(RoleRequiredMixin, View):
    allowed_roles = INVOICE_ROLES
    template_name = "invoicing/invoice_form.html"

    def get(self, request):
        store = StoreSettings.get_settings()
        form = InvoiceCreateForm(
            initial={
                "invoice_date": timezone.localdate(),
                "issue_now": True,
                "location": StoreLocation.get_default().pk,
                "tax_mode": store.default_tax_mode,
            }
        )
        formset = InvoiceLineFormSet(prefix="items")
        return render(request, self.template_name, {"form": form, "formset": formset})

    def post(self, request):
        form = InvoiceCreateForm(request.POST)
        formset = InvoiceLineFormSet(request.POST, prefix="items")
        override = bool(request.POST.get("override_credit_limit")) and request.user.has_any_role(*INVOICE_ADMIN_ROLES)

        if form.is_valid() and formset.is_valid():
            items = [
                {
                    "variant": f.cleaned_data["variant"],
                    "quantity": f.cleaned_data["quantity"],
                    "unit_price": f.cleaned_data.get("unit_price"),
                    "discount_percent": f.cleaned_data.get("discount_percent") or 0,
                }
                for f in formset
                if f.cleaned_data
            ]
            cd = form.cleaned_data
            try:
                inv = create_invoice(
                    customer=cd["customer"],
                    location=cd["location"],
                    price_list=cd["price_list"],
                    tax_mode=cd["tax_mode"],
                    invoice_discount=cd.get("invoice_discount") or 0,
                    invoice_date=cd["invoice_date"],
                    due_date=cd.get("due_date"),
                    place_of_supply_state=cd.get("place_of_supply_state") or None,
                    notes=cd.get("notes", ""),
                    items_data=items,
                    issue_now=cd.get("issue_now", True),
                    override_credit_limit=override,
                    enforce_price_permission=True,
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Invoice {inv} saved.")
                return redirect("invoicing:invoice_detail", pk=inv.pk)
            except (InsufficientStockError, CreditLimitExceededError, ValidationError) as exc:
                form.add_error(None, str(exc))
        return render(request, self.template_name, {"form": form, "formset": formset})


class InvoiceDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = INVOICE_ROLES
    model = Invoice
    template_name = "invoicing/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return Invoice.objects.select_related("customer", "sales_order", "created_by").prefetch_related(
            "items__variant__product", "credit_notes"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        abs_url = self.request.build_absolute_uri(self.request.path)
        ctx["whatsapp_url"] = self.object.get_whatsapp_url(abs_url)
        unallocated_adv = sum(
            p.unallocated_amount
            for p in self.object.customer.customer_payments.filter(is_reversed=False)
        )
        ctx["customer_unallocated_advance"] = unallocated_adv
        return ctx


class InvoiceApplyAdvanceView(RoleRequiredMixin, View):
    allowed_roles = INVOICE_ROLES

    def post(self, request, pk):
        from apps.payments.services import apply_customer_advance

        invoice = get_object_or_404(Invoice, pk=pk)
        try:
            applied = apply_customer_advance(customer=invoice.customer, invoice=invoice, user=request.user, request=request)
            if applied > 0:
                messages.success(request, f"Applied advance of {applied} to {invoice.invoice_number}.")
            else:
                messages.info(request, "No unallocated advance available to apply.")
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("invoicing:invoice_detail", pk=invoice.pk)


class InvoiceIssueView(RoleRequiredMixin, View):
    allowed_roles = INVOICE_ROLES

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        override = bool(request.POST.get("override_credit_limit")) and request.user.has_any_role(*INVOICE_ADMIN_ROLES)
        try:
            issue_invoice(invoice=invoice, user=request.user, override_credit_limit=override, request=request)
            messages.success(request, f"Invoice {invoice.invoice_number} issued.")
        except (InsufficientStockError, CreditLimitExceededError, ValidationError) as exc:
            messages.error(request, str(exc))
        return redirect("invoicing:invoice_detail", pk=invoice.pk)


class InvoiceCancelView(RoleRequiredMixin, View):
    allowed_roles = INVOICE_ADMIN_ROLES

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        reason = request.POST.get("cancel_reason", "").strip()
        try:
            cancel_invoice(invoice=invoice, user=request.user, reason=reason, request=request)
            messages.success(request, f"Invoice {invoice.invoice_number or invoice.pk} cancelled and stock restored.")
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("invoicing:invoice_detail", pk=invoice.pk)


class InvoicePrintView(RoleRequiredMixin, DetailView):
    allowed_roles = INVOICE_ROLES
    model = Invoice
    template_name = "invoicing/invoice_print.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return Invoice.objects.select_related("customer").prefetch_related("items__variant__product")


class InvoicePDFView(RoleRequiredMixin, View):
    allowed_roles = INVOICE_ROLES

    def get(self, request, pk):
        invoice = get_object_or_404(
            Invoice.objects.select_related("customer").prefetch_related("items__variant__product"),
            pk=pk,
        )
        try:
            pdf_bytes = render_invoice_pdf_bytes(invoice)
        except RuntimeError as exc:
            messages.error(request, str(exc))
            return redirect("invoicing:invoice_print", pk=invoice.pk)
        filename = (invoice.invoice_number or f"invoice-{invoice.pk}").replace("/", "-") + ".pdf"
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp


class CreditNoteListView(RoleRequiredMixin, ListView):
    allowed_roles = INVOICE_ROLES
    model = CreditNote
    template_name = "invoicing/credit_note_list.html"
    context_object_name = "credit_notes"
    paginate_by = 25

    def get_queryset(self):
        return CreditNote.objects.select_related("invoice", "customer", "created_by").all()
