from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.models import StoreLocation
from .forms import (
    GoodsReceiptForm,
    PurchaseLineItemFormSet,
    PurchaseOrderForm,
    SupplierForm,
    SupplierPaymentForm,
)
from .models import GoodsReceipt, PurchaseOrder, Supplier, SupplierPayment
from .services import create_purchase_order, receive_goods, record_supplier_payment

PURCHASE_OPS_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER)
SUPPLIER_PAY_ROLES = (User.Role.OWNER, User.Role.MANAGER)


class SupplierListView(RoleRequiredMixin, ListView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = Supplier
    template_name = "purchases/supplier_list.html"
    context_object_name = "suppliers"
    paginate_by = 25

    def get_queryset(self):
        return Supplier.objects.with_balances()


class SupplierCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = Supplier
    form_class = SupplierForm
    template_name = "purchases/supplier_form.html"
    success_url = reverse_lazy("purchases:supplier_list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=self.object,
            changes={"name": self.object.name},
            request=self.request,
            notes="Created supplier",
        )
        messages.success(self.request, f"Supplier '{self.object.name}' added.")
        return resp


class SupplierUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = Supplier
    form_class = SupplierForm
    template_name = "purchases/supplier_form.html"
    success_url = reverse_lazy("purchases:supplier_list")

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            changes={"name": self.object.name, "is_active": self.object.is_active},
            request=self.request,
            notes="Updated supplier",
        )
        return resp


class SupplierDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = Supplier
    template_name = "purchases/supplier_detail.html"
    context_object_name = "supplier"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["receipts"] = self.object.goods_receipts.all()[:20]
        ctx["payments"] = self.object.payments.all()[:20]
        return ctx


class PurchaseOrderListView(RoleRequiredMixin, ListView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = PurchaseOrder
    template_name = "purchases/po_list.html"
    context_object_name = "orders"
    paginate_by = 25

    def get_queryset(self):
        return PurchaseOrder.objects.select_related("supplier", "location", "created_by").all()


class PurchaseOrderCreateView(RoleRequiredMixin, View):
    allowed_roles = PURCHASE_OPS_ROLES
    template_name = "purchases/po_form.html"

    def get(self, request):
        form = PurchaseOrderForm(initial={"order_date": timezone.localdate(), "location": StoreLocation.get_default().pk})
        formset = PurchaseLineItemFormSet(prefix="items")
        return render(request, self.template_name, {"form": form, "formset": formset})

    def post(self, request):
        form = PurchaseOrderForm(request.POST)
        formset = PurchaseLineItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = [
                {
                    "variant": f.cleaned_data["variant"],
                    "quantity_ordered": f.cleaned_data["quantity"],
                    "unit_cost": f.cleaned_data["unit_cost"],
                    "discount_amount": f.cleaned_data.get("discount_amount") or 0,
                    "tax_rate": f.cleaned_data.get("tax_rate") or 0,
                }
                for f in formset
                if f.cleaned_data
            ]
            try:
                po = create_purchase_order(
                    supplier=form.cleaned_data["supplier"],
                    location=form.cleaned_data.get("location"),
                    order_date=form.cleaned_data["order_date"],
                    expected_date=form.cleaned_data.get("expected_date"),
                    notes=form.cleaned_data.get("notes", ""),
                    items_data=items,
                    user=request.user,
                )
                messages.success(request, f"Purchase Order {po.po_number} created.")
                return redirect("purchases:po_detail", pk=po.pk)
            except ValidationError as exc:
                form.add_error(None, str(exc))
        return render(request, self.template_name, {"form": form, "formset": formset})


class PurchaseOrderDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = PurchaseOrder
    template_name = "purchases/po_detail.html"
    context_object_name = "po"

    def get_queryset(self):
        return PurchaseOrder.objects.select_related("supplier", "location", "created_by").prefetch_related(
            "items__variant__product"
        )


class GoodsReceiptListView(RoleRequiredMixin, ListView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = GoodsReceipt
    template_name = "purchases/grn_list.html"
    context_object_name = "receipts"
    paginate_by = 25

    def get_queryset(self):
        return GoodsReceipt.objects.select_related("supplier", "location", "purchase_order", "received_by").all()


class GoodsReceiptCreateView(RoleRequiredMixin, View):
    allowed_roles = PURCHASE_OPS_ROLES
    template_name = "purchases/grn_form.html"

    def get(self, request, po_pk=None):
        initial = {"received_date": timezone.localdate(), "location": StoreLocation.get_default().pk}
        item_initials = []
        if po_pk:
            po = get_object_or_404(PurchaseOrder.objects.prefetch_related("items"), pk=po_pk)
            initial["supplier"] = po.supplier_id
            initial["purchase_order"] = po.pk
            if po.location_id:
                initial["location"] = po.location_id
            item_initials = [
                {
                    "variant": it.variant_id,
                    "quantity": it.quantity_ordered,
                    "unit_cost": it.unit_cost,
                    "discount_amount": it.discount_amount,
                    "tax_rate": it.tax_rate,
                }
                for it in po.items.all()
            ]
        form = GoodsReceiptForm(initial=initial)
        formset = PurchaseLineItemFormSet(prefix="items", initial=item_initials or None)
        return render(request, self.template_name, {"form": form, "formset": formset})

    def post(self, request, po_pk=None):
        form = GoodsReceiptForm(request.POST)
        formset = PurchaseLineItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = [
                {
                    "variant": f.cleaned_data["variant"],
                    "quantity_received": f.cleaned_data["quantity"],
                    "damaged_quantity": f.cleaned_data.get("damaged_quantity") or 0,
                    "missing_quantity": f.cleaned_data.get("missing_quantity") or 0,
                    "unit_cost": f.cleaned_data["unit_cost"],
                    "discount_amount": f.cleaned_data.get("discount_amount") or 0,
                    "tax_rate": f.cleaned_data.get("tax_rate") or 0,
                }
                for f in formset
                if f.cleaned_data
            ]
            try:
                grn = receive_goods(
                    supplier=form.cleaned_data["supplier"],
                    location=form.cleaned_data.get("location"),
                    purchase_order=form.cleaned_data.get("purchase_order"),
                    received_date=form.cleaned_data["received_date"],
                    supplier_invoice_no=form.cleaned_data.get("supplier_invoice_no", ""),
                    notes=form.cleaned_data.get("notes", ""),
                    items_data=items,
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Goods Receipt {grn.grn_number} posted and stock updated.")
                return redirect("purchases:grn_detail", pk=grn.pk)
            except ValidationError as exc:
                form.add_error(None, str(exc))
        return render(request, self.template_name, {"form": form, "formset": formset})


class GoodsReceiptDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = PURCHASE_OPS_ROLES
    model = GoodsReceipt
    template_name = "purchases/grn_detail.html"
    context_object_name = "grn"

    def get_queryset(self):
        return GoodsReceipt.objects.select_related("supplier", "location", "purchase_order", "received_by").prefetch_related(
            "items__variant__product"
        )


class SupplierPaymentListCreateView(RoleRequiredMixin, View):
    allowed_roles = SUPPLIER_PAY_ROLES
    template_name = "purchases/supplier_payments.html"

    def get(self, request):
        form = SupplierPaymentForm(initial={"payment_date": timezone.localdate()})
        payments = SupplierPayment.objects.select_related("supplier", "created_by").prefetch_related("allocations__goods_receipt")[:50]
        return render(request, self.template_name, {"form": form, "payments": payments})

    def post(self, request):
        form = SupplierPaymentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                payment = record_supplier_payment(
                    supplier=cd["supplier"],
                    goods_receipt=cd.get("goods_receipt"),
                    amount=cd["amount"],
                    payment_mode=cd["payment_mode"],
                    payment_date=cd["payment_date"],
                    reference_no=cd.get("reference_no", ""),
                    notes=cd.get("notes", ""),
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Supplier payment {payment.payment_number} recorded.")
                return redirect("purchases:supplier_payments")
            except ValidationError as exc:
                form.add_error(None, str(exc))
        payments = SupplierPayment.objects.select_related("supplier", "created_by").prefetch_related("allocations__goods_receipt")[:50]
        return render(request, self.template_name, {"form": form, "payments": payments})
