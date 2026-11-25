from decimal import Decimal
from urllib.parse import quote
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import StoreLocation, StoreSettings, TimeStampedModel
from apps.core.utils import money_round
from apps.customers.models import Customer
from apps.inventory.models import ProductVariant
from apps.sales.models import PriceList, SalesOrder, SalesReturn


class FinancialYearSequence(models.Model):
    """Atomic sequence counter per financial year for auto invoice numbering."""

    financial_year = models.CharField(max_length=15, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.financial_year}: {self.last_number}"


class Invoice(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        ISSUED = "ISSUED", _("Issued")
        PARTIAL = "PARTIAL", _("Partially Paid")
        PAID = "PAID", _("Paid")
        CANCELLED = "CANCELLED", _("Cancelled")

    invoice_number = models.CharField(max_length=40, unique=True, null=True, blank=True, db_index=True)
    financial_year = models.CharField(max_length=15, blank=True, db_index=True)
    sales_order = models.OneToOneField(
        SalesOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice",
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices")
    price_list = models.CharField(max_length=15, choices=PriceList.choices, default=PriceList.WHOLESALE)
    tax_mode = models.CharField(
        max_length=15,
        choices=StoreSettings.TaxMode.choices,
        default=StoreSettings.TaxMode.EXCLUSIVE,
    )
    invoice_date = models.DateField(default=timezone.localdate, db_index=True)
    due_date = models.DateField(db_index=True)
    place_of_supply_state = models.CharField(max_length=10, default=settings.DEFAULT_STATE_CODE, verbose_name="Region / Governorate")
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT, db_index=True)

    subtotal = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    discount_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    invoice_discount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    taxable_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    cgst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    sgst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    igst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    round_off = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0.000"))
    grand_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    amount_paid = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))

    notes = models.TextField(blank=True)
    terms_and_conditions = models.TextField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices_created")
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_cancelled",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        indexes = [
            models.Index(fields=["invoice_number"]),
            models.Index(fields=["customer", "invoice_date"]),
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self):
        return self.invoice_number or f"DRAFT-{self.pk}"

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Issued or finalized invoices cannot be deleted. Cancel or issue a credit note instead.")
        return super().delete(*args, **kwargs)

    @property
    def total_tax(self) -> Decimal:
        return money_round(self.cgst_total + self.sgst_total + self.igst_total)

    @property
    def credited_total(self) -> Decimal:
        val = self.credit_notes.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        return money_round(val)

    @property
    def refunded_total(self) -> Decimal:
        val = Refund.objects.filter(credit_note__invoice=self).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        return money_round(val)

    @property
    def balance_due(self) -> Decimal:
        if self.status in (self.Status.DRAFT, self.Status.CANCELLED):
            return Decimal("0.00")
        due = money_round(self.grand_total - self.credited_total + self.refunded_total - self.amount_paid)
        return max(due, Decimal("0.00"))

    @property
    def is_overdue(self) -> bool:
        return self.status in (self.Status.ISSUED, self.Status.PARTIAL) and self.due_date < timezone.localdate() and self.balance_due > 0

    def get_whatsapp_url(self, absolute_invoice_url: str = "") -> str:
        store = StoreSettings.get_settings()
        phone = self.customer.whatsapp_digits
        text = (
            f"Hello {self.customer.name}, invoice {self.invoice_number} from {store.store_name} "
            f"for {store.currency_symbol} {self.grand_total} (Due: {self.due_date}). "
            f"Balance due: {store.currency_symbol} {self.balance_due}."
        )
        if absolute_invoice_url:
            text += f" View: {absolute_invoice_url}"
        base = f"https://wa.me/{phone}" if phone else "https://wa.me/"
        return f"{base}?text={quote(text)}"


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="invoice_items")
    description = models.CharField(max_length=200)
    hsn_code = models.CharField(max_length=20, blank=True, verbose_name="Item / Tariff Code")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=13, decimal_places=3)
    discount_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    taxable_value = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    cgst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    sgst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    igst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))

    def __str__(self):
        return f"{self.variant.sku} x {self.quantity}"


class CreditNote(TimeStampedModel):
    credit_note_number = models.CharField(max_length=40, unique=True, db_index=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="credit_notes")
    sales_return = models.OneToOneField(
        SalesReturn,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_note",
    )
    issue_date = models.DateField(default=timezone.localdate, db_index=True)
    reason = models.TextField(blank=True)
    taxable_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    cgst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    sgst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    igst_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    total_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credit_notes")

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self):
        return f"{self.credit_note_number} ({self.invoice.invoice_number})"


class CreditNoteItem(models.Model):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="credit_note_items")
    hsn_code = models.CharField(max_length=20, blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=13, decimal_places=3)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    taxable_value = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    cgst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    sgst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    igst_amount = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    line_total = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))


class Refund(TimeStampedModel):
    class PaymentMode(models.TextChoices):
        CASH = "CASH", "Cash"
        KNET = "KNET", "KNET"
        BANK = "BANK", "Bank Transfer"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    refund_number = models.CharField(max_length=40, unique=True, db_index=True)
    credit_note = models.ForeignKey(CreditNote, on_delete=models.PROTECT, related_name="refunds")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="refunds")
    refund_date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    payment_mode = models.CharField(max_length=15, choices=PaymentMode.choices, default=PaymentMode.CASH, db_index=True)
    reference_no = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="refunds_issued")

    class Meta:
        ordering = ["-refund_date", "-id"]

    def __str__(self):
        return f"{self.refund_number} — {self.customer.name} ({self.amount})"
