from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import StoreLocation, StoreSettings, TimeStampedModel
from apps.customers.models import Customer
from apps.inventory.models import ProductVariant


class PriceList(models.TextChoices):
    WHOLESALE = "WHOLESALE", _("Wholesale")
    RETAIL = "RETAIL", _("Retail")


class SalesOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        INVOICED = "INVOICED", _("Invoiced")
        CANCELLED = "CANCELLED", _("Cancelled")

    order_number = models.CharField(max_length=40, unique=True, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="sales_orders")
    price_list = models.CharField(max_length=15, choices=PriceList.choices, default=PriceList.WHOLESALE)
    tax_mode = models.CharField(max_length=15, choices=StoreSettings.TaxMode.choices, default=StoreSettings.TaxMode.EXCLUSIVE)
    order_date = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    subtotal = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    discount_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    invoice_discount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    grand_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_orders")

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"{self.order_number} — {self.customer.name}"


class SalesOrderItem(models.Model):
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="sales_order_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.000"))])
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))

    def __str__(self):
        return f"{self.variant.sku} x {self.quantity}"


class SalesReturn(TimeStampedModel):
    class Condition(models.TextChoices):
        RESTOCK = "RESTOCK", _("Good — Restock to Inventory")
        DAMAGED = "DAMAGED", _("Damaged — Write off")

    return_number = models.CharField(max_length=40, unique=True, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_returns")
    invoice = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.PROTECT,
        related_name="sales_returns",
    )
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="sales_returns")
    return_date = models.DateField(default=timezone.localdate, db_index=True)
    condition = models.CharField(max_length=15, choices=Condition.choices, default=Condition.RESTOCK)
    reason = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    refund_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_returns")

    class Meta:
        ordering = ["-return_date", "-id"]

    def __str__(self):
        return f"{self.return_number} ({self.invoice.invoice_number})"


class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="sales_return_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=13, decimal_places=3)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))


class SalesExchange(TimeStampedModel):
    exchange_number = models.CharField(max_length=40, unique=True, db_index=True)
    original_invoice = models.ForeignKey("invoicing.Invoice", on_delete=models.PROTECT, related_name="exchanges")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="exchanges")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="exchanges")
    exchange_date = models.DateField(default=timezone.localdate, db_index=True)
    credit_note = models.OneToOneField(
        "invoicing.CreditNote",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="exchange",
    )
    replacement_invoice = models.OneToOneField(
        "invoicing.Invoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="exchange_replacement",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_exchanges")

    class Meta:
        ordering = ["-exchange_date", "-id"]

    def __str__(self):
        return f"{self.exchange_number} ({self.original_invoice.invoice_number})"
