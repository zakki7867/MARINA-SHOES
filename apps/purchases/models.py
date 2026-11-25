from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AllObjectsManager, SoftDeleteModel, SoftDeleteQuerySet, StoreLocation, TimeStampedModel
from apps.core.utils import money_round
from apps.inventory.models import ProductVariant


class SupplierQuerySet(SoftDeleteQuerySet):
    def with_balances(self):
        dec = DecimalField(max_digits=15, decimal_places=3)
        zero = Value(Decimal("0.000"), output_field=dec)
        purch_sq = (
            GoodsReceipt.objects.filter(supplier=OuterRef("pk"))
            .values("supplier")
            .annotate(t=Sum("total_amount"))
            .values("t")[:1]
        )
        ret_sq = (
            PurchaseReturn.objects.filter(supplier=OuterRef("pk"))
            .values("supplier")
            .annotate(t=Sum("total_amount"))
            .values("t")[:1]
        )
        paid_sq = (
            SupplierPayment.objects.filter(supplier=OuterRef("pk"))
            .values("supplier")
            .annotate(t=Sum("amount"))
            .values("t")[:1]
        )
        return self.annotate(
            ann_purchased=Coalesce(Subquery(purch_sq, output_field=dec), zero),
            ann_returned=Coalesce(Subquery(ret_sq, output_field=dec), zero),
            ann_paid=Coalesce(Subquery(paid_sq, output_field=dec), zero),
        )


class ActiveSupplierManager(models.Manager):
    def get_queryset(self):
        return SupplierQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def with_balances(self):
        return self.get_queryset().with_balances()


class Supplier(SoftDeleteModel):
    name = models.CharField(max_length=160, db_index=True)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, db_index=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField(max_length=20, blank=True, verbose_name="Tax ID / CR No.")
    address = models.TextField(blank=True)
    state_code = models.CharField(max_length=10, default=settings.DEFAULT_STATE_CODE, verbose_name="Governorate / Region")
    opening_balance = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    payment_terms_days = models.PositiveIntegerField(default=30)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    objects = ActiveSupplierManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_purchased(self) -> Decimal:
        if hasattr(self, "ann_purchased"):
            return money_round(self.ann_purchased)
        val = self.goods_receipts.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def total_returned(self) -> Decimal:
        if hasattr(self, "ann_returned"):
            return money_round(self.ann_returned)
        val = self.purchase_returns.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def total_paid(self) -> Decimal:
        if hasattr(self, "ann_paid"):
            return money_round(self.ann_paid)
        val = self.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def outstanding_balance(self) -> Decimal:
        return money_round(self.opening_balance + self.total_purchased - self.total_returned - self.total_paid)


class PurchaseOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        ORDERED = "ORDERED", _("Ordered")
        PARTIAL = "PARTIAL", _("Partially Received")
        RECEIVED = "RECEIVED", _("Received")
        CANCELLED = "CANCELLED", _("Cancelled")

    po_number = models.CharField(max_length=40, unique=True, db_index=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_orders")
    order_date = models.DateField(default=timezone.localdate, db_index=True)
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ORDERED, db_index=True)
    discount_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    tax_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    total_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_orders")

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"{self.po_number} ({self.supplier.name})"


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="po_items")
    quantity_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.000"))])
    discount_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))

    def __str__(self):
        return f"{self.variant.sku} x {self.quantity_ordered}"


class GoodsReceipt(TimeStampedModel):
    grn_number = models.CharField(max_length=40, unique=True, db_index=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="goods_receipts",
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="goods_receipts")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="goods_receipts")
    received_date = models.DateField(default=timezone.localdate, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    supplier_invoice_no = models.CharField(max_length=60, blank=True)
    discount_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    tax_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    total_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    amount_paid = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="goods_receipts")

    class Meta:
        ordering = ["-received_date", "-id"]

    def __str__(self):
        return f"{self.grn_number} — {self.supplier.name}"

    @property
    def balance_due(self) -> Decimal:
        return max(money_round(self.total_amount - self.amount_paid), Decimal("0.00"))


class GoodsReceiptItem(models.Model):
    goods_receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="grn_items")
    quantity_received = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    damaged_quantity = models.PositiveIntegerField(default=0)
    missing_quantity = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.000"))])
    discount_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))

    def __str__(self):
        return f"{self.variant.sku} +{self.quantity_received}"


class PurchaseReturn(TimeStampedModel):
    return_number = models.CharField(max_length=40, unique=True, db_index=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_returns")
    goods_receipt = models.ForeignKey(
        GoodsReceipt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_returns",
    )
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_returns")
    return_date = models.DateField(default=timezone.localdate, db_index=True)
    total_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_returns")

    class Meta:
        ordering = ["-return_date", "-id"]

    def __str__(self):
        return f"{self.return_number} — {self.supplier.name}"


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="purchase_return_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.000"))])
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))


class SupplierPayment(TimeStampedModel):
    class PaymentMode(models.TextChoices):
        CASH = "CASH", _("Cash")
        KNET = "KNET", _("KNET")
        BANK = "BANK", _("Bank Transfer")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    payment_number = models.CharField(max_length=40, unique=True, db_index=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    payment_mode = models.CharField(max_length=15, choices=PaymentMode.choices, default=PaymentMode.BANK, db_index=True)
    reference_no = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="supplier_payments")

    class Meta:
        ordering = ["-payment_date", "-id"]

    def __str__(self):
        return f"{self.payment_number} — {self.supplier.name} ({self.amount})"

    @property
    def allocated_amount(self) -> Decimal:
        val = self.allocations.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def unallocated_amount(self) -> Decimal:
        return max(money_round(self.amount - self.allocated_amount), Decimal("0.00"))


class SupplierPaymentAllocation(models.Model):
    payment = models.ForeignKey(SupplierPayment, on_delete=models.CASCADE, related_name="allocations")
    goods_receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])

    class Meta:
        unique_together = [("payment", "goods_receipt")]
