from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _

from apps.core.models import AllObjectsManager, SoftDeleteModel, SoftDeleteQuerySet
from apps.core.utils import money_round


class CustomerQuerySet(SoftDeleteQuerySet):
    def with_balances(self):
        from apps.invoicing.models import CreditNote, Invoice, Refund
        from apps.payments.models import CustomerPayment

        dec = DecimalField(max_digits=15, decimal_places=3)
        zero = Value(Decimal("0.000"), output_field=dec)

        invoiced_sq = (
            Invoice.objects.filter(customer=OuterRef("pk"), status__in=["ISSUED", "PARTIAL", "PAID"])
            .values("customer")
            .annotate(t=Sum("grand_total"))
            .values("t")[:1]
        )
        credited_sq = (
            CreditNote.objects.filter(customer=OuterRef("pk"))
            .values("customer")
            .annotate(t=Sum("total_amount"))
            .values("t")[:1]
        )
        refunded_sq = (
            Refund.objects.filter(customer=OuterRef("pk"))
            .values("customer")
            .annotate(t=Sum("amount"))
            .values("t")[:1]
        )
        paid_sq = (
            CustomerPayment.objects.filter(customer=OuterRef("pk"), is_reversed=False)
            .values("customer")
            .annotate(t=Sum("amount"))
            .values("t")[:1]
        )
        return self.annotate(
            ann_invoiced=Coalesce(Subquery(invoiced_sq, output_field=dec), zero),
            ann_credited=Coalesce(Subquery(credited_sq, output_field=dec), zero),
            ann_refunded=Coalesce(Subquery(refunded_sq, output_field=dec), zero),
            ann_paid=Coalesce(Subquery(paid_sq, output_field=dec), zero),
        )


class ActiveCustomerManager(models.Manager):
    def get_queryset(self):
        return CustomerQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def with_balances(self):
        return self.get_queryset().with_balances()


class Customer(SoftDeleteModel):
    """Wholesale shop or direct retail customer with credit limit and GST details."""

    class CustomerType(models.TextChoices):
        WHOLESALE = "WHOLESALE", _("Wholesale Shop")
        RETAIL = "RETAIL", _("Retail Walk-in")

    name = models.CharField(max_length=160, db_index=True)
    customer_type = models.CharField(
        max_length=15,
        choices=CustomerType.choices,
        default=CustomerType.WHOLESALE,
        db_index=True,
    )
    phone = models.CharField(max_length=40, db_index=True)
    whatsapp_number = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField(max_length=20, blank=True, verbose_name="Tax ID / CR No.")
    address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True, verbose_name="Area / City")
    state_code = models.CharField(max_length=10, default=settings.DEFAULT_STATE_CODE, verbose_name="Governorate / Region")
    credit_limit = models.DecimalField(
        max_digits=13,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="0.000 means no preset credit limit.",
    )
    payment_terms_days = models.PositiveIntegerField(default=30)
    opening_balance = models.DecimalField(max_digits=13, decimal_places=3, default=Decimal("0.000"))
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    objects = ActiveCustomerManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name", "customer_type"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_customer_type_display()})"

    @property
    def total_invoiced(self) -> Decimal:
        if hasattr(self, "ann_invoiced"):
            return money_round(self.ann_invoiced)
        if not hasattr(self, "invoices"):
            return Decimal("0.00")
        val = self.invoices.filter(status__in=["ISSUED", "PARTIAL", "PAID"]).aggregate(
            total=Coalesce(Sum("grand_total"), Decimal("0.00"))
        )["total"]
        return money_round(val)

    @property
    def total_credit_notes(self) -> Decimal:
        if hasattr(self, "ann_credited"):
            return money_round(self.ann_credited)
        if not hasattr(self, "credit_notes"):
            return Decimal("0.00")
        val = self.credit_notes.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def total_paid(self) -> Decimal:
        if hasattr(self, "ann_paid"):
            return money_round(self.ann_paid)
        if not hasattr(self, "customer_payments"):
            return Decimal("0.00")
        val = self.customer_payments.filter(is_reversed=False).aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def total_refunded(self) -> Decimal:
        if hasattr(self, "ann_refunded"):
            return money_round(self.ann_refunded)
        if not hasattr(self, "refunds"):
            return Decimal("0.00")
        val = self.refunds.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        return money_round(val)

    @property
    def outstanding_balance(self) -> Decimal:
        return money_round(
            self.opening_balance + self.total_invoiced - self.total_credit_notes + self.total_refunded - self.total_paid
        )

    @property
    def whatsapp_digits(self) -> str:
        raw = self.whatsapp_number or self.phone or ""
        return "".join(ch for ch in raw if ch.isdigit())
