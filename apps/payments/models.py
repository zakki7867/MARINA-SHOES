from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import money_round
from apps.customers.models import Customer
from apps.invoicing.models import Invoice


class CustomerPayment(TimeStampedModel):
    class PaymentMode(models.TextChoices):
        CASH = "CASH", _("Cash")
        KNET = "KNET", _("KNET")
        BANK = "BANK", _("Bank Transfer")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    receipt_number = models.CharField(max_length=40, unique=True, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="customer_payments")
    payment_date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    payment_mode = models.CharField(max_length=15, choices=PaymentMode.choices, default=PaymentMode.CASH, db_index=True)
    reference_no = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    is_advance = models.BooleanField(default=False)
    is_reversed = models.BooleanField(default=False, db_index=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_payments_reversed",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_payments_recorded")

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [
            models.Index(fields=["customer", "payment_date"]),
        ]

    def __str__(self):
        return f"{self.receipt_number} — {self.customer.name} ({self.amount})"

    @property
    def allocated_amount(self) -> Decimal:
        if self.is_reversed:
            return Decimal("0.00")
        val = self.allocations.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def unallocated_amount(self) -> Decimal:
        if self.is_reversed:
            return Decimal("0.00")
        return max(money_round(self.amount - self.allocated_amount), Decimal("0.00"))


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])

    class Meta:
        unique_together = [("payment", "invoice")]

    def __str__(self):
        return f"{self.payment.receipt_number} -> {self.invoice.invoice_number}: {self.amount}"

    def clean(self):
        if self.amount is None or self.amount <= Decimal("0.00"):
            raise ValidationError("Allocation amount must be positive.")
        if self.invoice_id:
            other_allocated = (
                PaymentAllocation.objects.filter(invoice_id=self.invoice_id, payment__is_reversed=False)
                .exclude(pk=self.pk)
                .aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
            )
            max_allowed = money_round(
                self.invoice.grand_total - self.invoice.credited_total + self.invoice.refunded_total
            )
            if money_round(other_allocated + self.amount) > max_allowed:
                raise ValidationError("Total payment allocations cannot exceed the invoice collectible balance.")
