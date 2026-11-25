from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from apps.core.models import StoreLocation
from apps.reports.services import export_csv_response, export_excel_response, report_stock
from .forms import (
    BrandForm,
    CategoryForm,
    CountLineFormSet,
    ProductForm,
    ProductImportForm,
    ProductVariantForm,
    StockCountForm,
    StockMovementForm,
    StockTransferForm,
    TransferLineFormSet,
)
from .models import Brand, Category, Product, ProductVariant, StockCount, StockMovement, StockTransfer
from .services import (
    InsufficientStockError,
    get_low_stock_variants,
    import_products_from_file,
    record_stock_count,
    record_stock_movement,
    transfer_stock,
)

INVENTORY_READ_ROLES = (
    User.Role.OWNER,
    User.Role.MANAGER,
    User.Role.STOREKEEPER,
    User.Role.BILLING_STAFF,
)
INVENTORY_WRITE_ROLES = (
    User.Role.OWNER,
    User.Role.MANAGER,
    User.Role.STOREKEEPER,
)


class ProductListView(RoleRequiredMixin, ListView):
    allowed_roles = INVENTORY_READ_ROLES
    model = Product
    template_name = "inventory/product_list.html"
    context_object_name = "products"
    paginate_by = 25

    def get_queryset(self):
        qs = Product.objects.select_related("category", "brand").annotate(
            variant_count=Count("variants", filter=Q(variants__is_deleted=False), distinct=True)
        )
        q = self.request.GET.get("q", "").strip()
        category_id = self.request.GET.get("category", "").strip()
        brand_id = self.request.GET.get("brand", "").strip()
        gender = self.request.GET.get("gender", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(style_code__icontains=q)
                | Q(hsn_code__icontains=q)
                | Q(variants__sku__icontains=q)
                | Q(variants__barcode__icontains=q)
            ).distinct()
        if category_id:
            qs = qs.filter(category_id=category_id)
        if brand_id:
            qs = qs.filter(brand_id=brand_id)
        if gender:
            qs = qs.filter(gender=gender)
        return qs.order_by("name")

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["inventory/partials/product_table.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = Category.objects.all()
        ctx["brands"] = Brand.objects.all()
        ctx["genders"] = Product.Gender.choices
        ctx["low_stock_count"] = get_low_stock_variants().count()
        return ctx


class ProductCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = INVENTORY_WRITE_ROLES
    model = Product
    form_class = ProductForm
    template_name = "inventory/product_form.html"

    def get_success_url(self):
        return reverse("inventory:product_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=self.object,
            changes={"name": self.object.name, "hsn_code": self.object.hsn_code},
            request=self.request,
            notes="Created product",
        )
        messages.success(self.request, f"Product '{self.object.name}' created. Add variants below.")
        return resp


class ProductUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = INVENTORY_WRITE_ROLES
    model = Product
    form_class = ProductForm
    template_name = "inventory/product_form.html"

    def get_success_url(self):
        return reverse("inventory:product_detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            changes={"name": self.object.name, "status": self.object.status},
            request=self.request,
            notes="Updated product",
        )
        messages.success(self.request, f"Product '{self.object.name}' updated.")
        return resp


class ProductSoftDeleteView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        product.soft_delete()
        for variant in product.variants.all():
            variant.soft_delete()
        log_audit(
            user=request.user,
            action=AuditLog.Action.DELETE,
            instance=product,
            request=request,
            notes="Soft-deleted product and variants",
        )
        messages.success(request, f"Product '{product.name}' archived.")
        return redirect("inventory:product_list")


class ProductDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = INVENTORY_READ_ROLES
    model = Product
    template_name = "inventory/product_detail.html"
    context_object_name = "product"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["variants"] = self.object.variants.with_stock().prefetch_related("location_balances__location")
        ctx["variant_form"] = ProductVariantForm()
        return ctx


class VariantCreateView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES

    def post(self, request, product_pk):
        product = get_object_or_404(Product, pk=product_pk)
        form = ProductVariantForm(request.POST)
        if form.is_valid():
            variant = form.save(commit=False)
            variant.product = product
            try:
                variant.full_clean()
                variant.save()
                log_audit(
                    user=request.user,
                    action=AuditLog.Action.CREATE,
                    instance=variant,
                    changes={"sku": variant.sku, "size": variant.size, "color": variant.color},
                    request=request,
                    notes="Added variant",
                )
                messages.success(request, f"Variant '{variant.sku}' added.")
                return redirect("inventory:product_detail", pk=product.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
        variants = product.variants.with_stock()
        return render(
            request,
            "inventory/product_detail.html",
            {"product": product, "variants": variants, "variant_form": form},
        )


class VariantUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = INVENTORY_WRITE_ROLES
    model = ProductVariant
    form_class = ProductVariantForm
    template_name = "inventory/variant_form.html"

    def get_success_url(self):
        return reverse("inventory:product_detail", kwargs={"pk": self.object.product_id})

    def form_valid(self, form):
        resp = super().form_valid(form)
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            changes={"sku": self.object.sku, "wholesale": self.object.wholesale_price, "retail": self.object.retail_price},
            request=self.request,
            notes="Updated variant",
        )
        messages.success(self.request, f"Variant '{self.object.sku}' updated.")
        return resp


class VariantSoftDeleteView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES

    def post(self, request, pk):
        variant = get_object_or_404(ProductVariant, pk=pk)
        product_pk = variant.product_id
        variant.soft_delete()
        log_audit(
            user=request.user,
            action=AuditLog.Action.DELETE,
            instance=variant,
            request=request,
            notes="Soft-deleted product variant",
        )
        messages.success(request, f"Variant '{variant.sku}' archived.")
        return redirect("inventory:product_detail", pk=product_pk)


class VariantStockHistoryView(RoleRequiredMixin, DetailView):
    allowed_roles = INVENTORY_READ_ROLES
    model = ProductVariant
    template_name = "inventory/variant_history.html"
    context_object_name = "variant"

    def get_queryset(self):
        return ProductVariant.objects.select_related("product", "product__brand").with_stock()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["movements"] = self.object.stock_movements.select_related("user", "location").all()[:100]
        ctx["location_balances"] = self.object.location_balances.select_related("location").all()
        return ctx


class StockMovementCreateView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES
    template_name = "inventory/stock_movement_form.html"

    def get(self, request):
        initial = {"location": StoreLocation.get_default().pk}
        variant_id = request.GET.get("variant")
        if variant_id:
            initial["variant"] = variant_id
        form = StockMovementForm(initial=initial)
        recent = StockMovement.objects.select_related("variant", "variant__product", "user", "location")[:25]
        return render(request, self.template_name, {"form": form, "recent_movements": recent})

    def post(self, request):
        form = StockMovementForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                movement = record_stock_movement(
                    variant=cd["variant"],
                    location=cd["location"],
                    movement_type=cd["movement_type"],
                    quantity=cd["quantity"],
                    direction=cd.get("direction") or None,
                    reference=cd.get("reference", ""),
                    reason=cd.get("reason", ""),
                    notes=cd.get("notes", ""),
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Recorded stock movement: {movement}")
                return redirect("inventory:stock_movement")
            except (InsufficientStockError, ValidationError) as exc:
                form.add_error(None, str(exc))
        recent = StockMovement.objects.select_related("variant", "variant__product", "user", "location")[:25]
        return render(request, self.template_name, {"form": form, "recent_movements": recent})


class StockTransferListCreateView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES
    template_name = "inventory/stock_transfers.html"

    def get(self, request):
        form = StockTransferForm(initial={"transfer_date": timezone.localdate(), "from_location": StoreLocation.get_default().pk})
        formset = TransferLineFormSet(prefix="items")
        transfers = StockTransfer.objects.select_related("from_location", "to_location", "created_by").prefetch_related("items__variant")[:25]
        return render(request, self.template_name, {"form": form, "formset": formset, "transfers": transfers})

    def post(self, request):
        form = StockTransferForm(request.POST)
        formset = TransferLineFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = [{"variant": f.cleaned_data["variant"], "quantity": f.cleaned_data["quantity"]} for f in formset if f.cleaned_data]
            try:
                trf = transfer_stock(
                    from_location=form.cleaned_data["from_location"],
                    to_location=form.cleaned_data["to_location"],
                    transfer_date=form.cleaned_data["transfer_date"],
                    notes=form.cleaned_data.get("notes", ""),
                    items_data=items,
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Stock transfer {trf.transfer_number} completed.")
                return redirect("inventory:stock_transfers")
            except (InsufficientStockError, ValidationError) as exc:
                form.add_error(None, str(exc))
        transfers = StockTransfer.objects.select_related("from_location", "to_location", "created_by").prefetch_related("items__variant")[:25]
        return render(request, self.template_name, {"form": form, "formset": formset, "transfers": transfers})


class StockCountListCreateView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES
    template_name = "inventory/stock_counts.html"

    def get(self, request):
        form = StockCountForm(initial={"count_date": timezone.localdate(), "location": StoreLocation.get_default().pk})
        formset = CountLineFormSet(prefix="items")
        counts = StockCount.objects.select_related("location", "created_by").prefetch_related("items__variant")[:25]
        return render(request, self.template_name, {"form": form, "formset": formset, "counts": counts})

    def post(self, request):
        form = StockCountForm(request.POST)
        formset = CountLineFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = [{"variant": f.cleaned_data["variant"], "counted_quantity": f.cleaned_data["counted_quantity"]} for f in formset if f.cleaned_data]
            try:
                sc = record_stock_count(
                    location=form.cleaned_data["location"],
                    count_date=form.cleaned_data["count_date"],
                    notes=form.cleaned_data.get("notes", ""),
                    items_data=items,
                    user=request.user,
                    request=request,
                )
                messages.success(request, f"Stock count {sc.count_number} posted and variances reconciled.")
                return redirect("inventory:stock_counts")
            except ValidationError as exc:
                form.add_error(None, str(exc))
        counts = StockCount.objects.select_related("location", "created_by").prefetch_related("items__variant")[:25]
        return render(request, self.template_name, {"form": form, "formset": formset, "counts": counts})


class ProductImportExportView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES
    template_name = "inventory/import_export.html"

    def get(self, request):
        fmt = request.GET.get("export", "").lower()
        if fmt in ("csv", "xlsx"):
            title, headers, rows = report_stock()
            if fmt == "csv":
                return export_csv_response("marina_catalog_stock", headers, rows)
            return export_excel_response("marina_catalog_stock", title, headers, rows)
        return render(request, self.template_name, {"form": ProductImportForm()})

    def post(self, request):
        form = ProductImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                res = import_products_from_file(form.cleaned_data["file"], user=request.user, request=request)
                messages.success(request, f"Import complete: {res['created']} variants created, {res['updated']} updated.")
                return redirect("inventory:product_list")
            except Exception as exc:
                form.add_error("file", str(exc))
        return render(request, self.template_name, {"form": form})


class LowStockListView(RoleRequiredMixin, ListView):
    allowed_roles = INVENTORY_READ_ROLES
    template_name = "inventory/low_stock_list.html"
    context_object_name = "variants"

    def get_queryset(self):
        return get_low_stock_variants()


class CategoryBrandManageView(RoleRequiredMixin, View):
    allowed_roles = INVENTORY_WRITE_ROLES
    template_name = "inventory/categories_brands.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "categories": Category.objects.all(),
                "brands": Brand.objects.all(),
                "category_form": CategoryForm(),
                "brand_form": BrandForm(),
            },
        )

    def post(self, request):
        action = request.POST.get("form_type")
        cat_form = CategoryForm(request.POST if action == "category" else None)
        brand_form = BrandForm(request.POST if action == "brand" else None)
        if action == "category" and cat_form.is_valid():
            cat_form.save()
            messages.success(request, "Category added.")
            return redirect("inventory:categories_brands")
        if action == "brand" and brand_form.is_valid():
            brand_form.save()
            messages.success(request, "Brand added.")
            return redirect("inventory:categories_brands")
        return render(
            request,
            self.template_name,
            {
                "categories": Category.objects.all(),
                "brands": Brand.objects.all(),
                "category_form": cat_form,
                "brand_form": brand_form,
            },
        )
