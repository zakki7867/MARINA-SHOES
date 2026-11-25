from decimal import Decimal
import uuid
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.core.models import StoreLocation, StoreSettings
from apps.core.utils import money_round
from apps.customers.models import Customer
from apps.inventory.models import ProductVariant
from apps.inventory.services import InsufficientStockError
from apps.invoicing.models import Invoice
from apps.invoicing.services import CreditLimitExceededError, create_invoice
from apps.payments.models import CustomerPayment
from apps.payments.services import record_customer_payment
from .forms import SalesLineItemFormSet, SalesOrderForm, SalesReturnLineFormSet, SalesReturnOptionsForm
from .models import PriceList, SalesExchange, SalesOrder, SalesReturn
from .services import convert_sales_order_to_invoice, create_sales_order, process_sales_exchange, process_sales_return

SALES_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)


class SalesOrderListView(RoleRequiredMixin, ListView):
    allowed_roles = SALES_ROLES
    model = SalesOrder
    template_name = "sales/order_list.html"
    context_object_name = "orders"
    paginate_by = 25

    def get_queryset(self):
        return SalesOrder.objects.select_related("customer", "location", "created_by").all()


class SalesOrderCreateView(RoleRequiredMixin, View):
    allowed_roles = SALES_ROLES
    template_name = "sales/order_form.html"

    def get(self, request):
        store = StoreSettings.get_settings()
        form = SalesOrderForm(
            initial={
                "order_date": timezone.localdate(),
                "location": StoreLocation.get_default().pk,
                "tax_mode": store.default_tax_mode,
            }
        )
        formset = SalesLineItemFormSet(prefix="items")
        return render(request, self.template_name, {"form": form, "formset": formset})

    def post(self, request):
        form = SalesOrderForm(request.POST)
        formset = SalesLineItemFormSet(request.POST, prefix="items")
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
            try:
                so = create_sales_order(
                    customer=form.cleaned_data["customer"],
                    location=form.cleaned_data["location"],
                    price_list=form.cleaned_data["price_list"],
                    tax_mode=form.cleaned_data["tax_mode"],
                    invoice_discount=form.cleaned_data.get("invoice_discount") or 0,
                    order_date=form.cleaned_data["order_date"],
                    notes=form.cleaned_data.get("notes", ""),
                    items_data=items,
                    user=request.user,
                )
                messages.success(request, f"Sales Order {so.order_number} created.")
                return redirect("sales:order_detail", pk=so.pk)
            except ValidationError as exc:
                form.add_error(None, str(exc))
        return render(request, self.template_name, {"form": form, "formset": formset})


class SalesOrderDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = SALES_ROLES
    model = SalesOrder
    template_name = "sales/order_detail.html"
    context_object_name = "order"

    def get_queryset(self):
        return SalesOrder.objects.select_related("customer", "location", "created_by").prefetch_related("items__variant__product")


class ConvertOrderToInvoiceView(RoleRequiredMixin, View):
    allowed_roles = SALES_ROLES

    def post(self, request, pk):
        order = get_object_or_404(SalesOrder, pk=pk)
        override = bool(request.POST.get("override_credit_limit")) and request.user.has_any_role(
            User.Role.OWNER, User.Role.MANAGER
        )
        try:
            inv = convert_sales_order_to_invoice(
                sales_order=order,
                user=request.user,
                override_credit_limit=override,
                request=request,
            )
            messages.success(request, f"Invoice {inv.invoice_number} issued from {order.order_number}.")
            return redirect("invoicing:invoice_detail", pk=inv.pk)
        except (InsufficientStockError, CreditLimitExceededError, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("sales:order_detail", pk=order.pk)


class POSView(RoleRequiredMixin, View):
    """Fast HTMX-assisted Point of Sale screen for retail and wholesale billing."""

    allowed_roles = SALES_ROLES
    template_name = "sales/pos.html"

    def _context(self, request=None):
        store = StoreSettings.get_settings()
        pos_token = uuid.uuid4().hex
        if request is not None:
            request.session["active_pos_token"] = pos_token
        can_override_price = bool(request and request.user.has_any_role(User.Role.OWNER, User.Role.MANAGER))
        return {
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
            "locations": StoreLocation.objects.filter(is_active=True),
            "default_location": StoreLocation.get_default(),
            "variants": ProductVariant.objects.filter(
                is_active=True, product__is_deleted=False, product__status="ACTIVE"
            )
            .select_related("product", "product__brand")
            .with_stock()[:16],
            "price_lists": PriceList.choices,
            "tax_modes": StoreSettings.TaxMode.choices,
            "default_tax_mode": store.default_tax_mode,
            "payment_modes": CustomerPayment.PaymentMode.choices,
            "pos_token": pos_token,
            "can_override_price": can_override_price,
        }

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    def post(self, request):
        posted_token = request.POST.get("pos_token", "").strip()
        used_tokens = request.session.get("used_pos_tokens", [])
        if posted_token and posted_token in used_tokens:
            messages.warning(request, "This POS bill was already submitted.")
            return redirect("sales:pos")

        customer_id = request.POST.get("customer")
        location_id = request.POST.get("location")
        price_list = request.POST.get("price_list") or PriceList.RETAIL
        tax_mode = request.POST.get("tax_mode") or StoreSettings.get_settings().default_tax_mode
        invoice_discount = money_round(request.POST.get("invoice_discount") or "0.00")
        amount_paid = money_round(request.POST.get("amount_paid") or "0.00")
        payment_mode = request.POST.get("payment_mode") or CustomerPayment.PaymentMode.CASH
        payment_ref = request.POST.get("payment_reference", "").strip()
        override_credit = bool(request.POST.get("override_credit_limit")) and request.user.has_any_role(
            User.Role.OWNER, User.Role.MANAGER
        )

        variant_ids = request.POST.getlist("variant_id")
        qtys = request.POST.getlist("qty")
        prices = request.POST.getlist("unit_price")
        discs = request.POST.getlist("discount_percent")

        try:
            customer = get_object_or_404(Customer, pk=customer_id)
            location = get_object_or_404(StoreLocation, pk=location_id) if location_id else StoreLocation.get_default()
            items_data = []
            for idx, vid in enumerate(variant_ids):
                if not vid:
                    continue
                variant = get_object_or_404(ProductVariant, pk=vid)
                qty = int(qtys[idx])
                up = prices[idx].strip() if idx < len(prices) else ""
                dp = discs[idx].strip() if idx < len(discs) and discs[idx].strip() else "0"
                items_data.append(
                    {
                        "variant": variant,
                        "quantity": qty,
                        "unit_price": Decimal(up) if up else None,
                        "discount_percent": Decimal(dp),
                    }
                )

            with transaction.atomic():
                inv = create_invoice(
                    customer=customer,
                    location=location,
                    price_list=price_list,
                    tax_mode=tax_mode,
                    invoice_discount=invoice_discount,
                    items_data=items_data,
                    issue_now=True,
                    override_credit_limit=override_credit,
                    enforce_price_permission=True,
                    user=request.user,
                    request=request,
                )
                if amount_paid > Decimal("0.00"):
                    record_customer_payment(
                        customer=customer,
                        invoice=inv,
                        amount=amount_paid,
                        payment_mode=payment_mode,
                        reference_no=payment_ref,
                        user=request.user,
                        request=request,
                    )
            if posted_token:
                used_tokens.append(posted_token)
                request.session["used_pos_tokens"] = used_tokens[-20:]
            messages.success(request, f"POS sale complete: Invoice {inv.invoice_number} issued.")
            return redirect("invoicing:invoice_detail", pk=inv.pk)
        except (InsufficientStockError, CreditLimitExceededError, PermissionDenied, ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._context(request))


class POSVariantSearchView(RoleRequiredMixin, View):
    allowed_roles = SALES_ROLES

    def get(self, request):
        q = request.GET.get("q", "").strip()
        qs = (
            ProductVariant.objects.filter(is_active=True, product__is_deleted=False, product__status="ACTIVE")
            .select_related("product", "product__brand")
            .with_stock()
        )
        if q:
            qs = qs.filter(
                Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(product__name__icontains=q)
                | Q(product__style_code__icontains=q)
                | Q(product__brand__name__icontains=q)
                | Q(size__icontains=q)
                | Q(color__icontains=q)
            )
        return render(request, "sales/partials/pos_search_results.html", {"variants": qs[:16]})


class SalesReturnListView(RoleRequiredMixin, ListView):
    allowed_roles = SALES_ROLES
    model = SalesReturn
    template_name = "sales/return_list.html"
    context_object_name = "returns"
    paginate_by = 25

    def get_queryset(self):
        return SalesReturn.objects.select_related("customer", "invoice", "location", "created_by").all()


class SalesReturnCreateView(RoleRequiredMixin, View):
    allowed_roles = SALES_ROLES
    template_name = "sales/return_form.html"

    def get(self, request, invoice_pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer").prefetch_related("items__variant"), pk=invoice_pk)
        initial = [{"variant": it.variant_id, "quantity": 1} for it in invoice.items.all()[:1]]
        formset = SalesReturnLineFormSet(prefix="items", initial=initial)
        opts = SalesReturnOptionsForm()
        return render(request, self.template_name, {"invoice": invoice, "formset": formset, "opts": opts})

    def post(self, request, invoice_pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer").prefetch_related("items__variant"), pk=invoice_pk)
        formset = SalesReturnLineFormSet(request.POST, prefix="items")
        opts = SalesReturnOptionsForm(request.POST)
        if formset.is_valid() and opts.is_valid():
            items = [
                {"variant": f.cleaned_data["variant"], "quantity": f.cleaned_data["quantity"]}
                for f in formset
                if f.cleaned_data
            ]
            od = opts.cleaned_data
            try:
                sr = process_sales_return(
                    invoice=invoice,
                    items_data=items,
                    condition=od["condition"],
                    reason=od.get("reason", ""),
                    refund_amount=od.get("refund_amount") or Decimal("0.00"),
                    refund_mode=od.get("refund_mode") or "CASH",
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Sales return {sr.return_number} & Credit Note created.")
                return redirect("invoicing:invoice_detail", pk=invoice.pk)
            except ValidationError as exc:
                messages.error(request, str(exc))
        return render(request, self.template_name, {"invoice": invoice, "formset": formset, "opts": opts})


class SalesExchangeCreateView(RoleRequiredMixin, View):
    allowed_roles = SALES_ROLES
    template_name = "sales/exchange_form.html"

    def get(self, request, invoice_pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer").prefetch_related("items__variant"), pk=invoice_pk)
        ret_formset = SalesReturnLineFormSet(prefix="ret", initial=[{"variant": it.variant_id, "quantity": 1} for it in invoice.items.all()[:1]])
        rep_formset = SalesReturnLineFormSet(prefix="rep")
        return render(
            request,
            self.template_name,
            {
                "invoice": invoice,
                "ret_formset": ret_formset,
                "rep_formset": rep_formset,
                "conditions": SalesReturn.Condition.choices,
            },
        )

    def post(self, request, invoice_pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer").prefetch_related("items__variant"), pk=invoice_pk)
        ret_formset = SalesReturnLineFormSet(request.POST, prefix="ret")
        rep_formset = SalesReturnLineFormSet(request.POST, prefix="rep")
        condition = request.POST.get("condition") or SalesReturn.Condition.RESTOCK
        reason = request.POST.get("reason", "").strip()
        if ret_formset.is_valid() and rep_formset.is_valid():
            ret_items = [{"variant": f.cleaned_data["variant"], "quantity": f.cleaned_data["quantity"]} for f in ret_formset if f.cleaned_data]
            rep_items = [{"variant": f.cleaned_data["variant"], "quantity": f.cleaned_data["quantity"]} for f in rep_formset if f.cleaned_data]
            try:
                ex = process_sales_exchange(
                    original_invoice=invoice,
                    return_items=ret_items,
                    replacement_items=rep_items,
                    condition=condition,
                    reason=reason,
                    user=request.user,
                    request=request,
                )
                messages.success(
                    request,
                    f"Exchange {ex.exchange_number} processed! Replacement invoice: {ex.replacement_invoice.invoice_number}.",
                )
                return redirect("invoicing:invoice_detail", pk=ex.replacement_invoice.pk)
            except (InsufficientStockError, ValidationError) as exc:
                messages.error(request, str(exc))
        return render(
            request,
            self.template_name,
            {
                "invoice": invoice,
                "ret_formset": ret_formset,
                "rep_formset": rep_formset,
                "conditions": SalesReturn.Condition.choices,
            },
        )
