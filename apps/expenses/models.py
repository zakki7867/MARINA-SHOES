from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import StoreLocation, TimeStampedModel
from apps.core.validators import validate_receipt_upload


class Expense(TimeStampedModel):
    class Category(models.TextChoices):
        RENT = "RENT", _("Rent")
        SALARY = "SALARY", _("Salary")
        ELECTRICITY = "ELECTRICITY", _("Electricity")
        TRANSPORT = "TRANSPORT", _("Transportation")
        MAINTENANCE = "MAINTENANCE", _("Maintenance")
        PACKAGING = "PACKAGING", _("Packaging")
        OFFICE = "OFFICE", _("Office Expense")
        OTHER = "OTHER", _("Other")

    class PaymentMode(models.TextChoices):
        CASH = "CASH", _("Cash")
        KNET = "KNET", _("KNET")
        BANK = "BANK", _("Bank Transfer")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    expense_date = models.DateField(default=timezone.localdate, db_index=True)
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER, db_index=True)
    amount = models.DecimalField(max_digits=13, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    payment_mode = models.CharField(max_length=15, choices=PaymentMode.choices, default=PaymentMode.CASH, db_index=True)
    reference_no = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    receipt_attachment = models.FileField(upload_to="expenses/", blank=True, null=True, validators=[validate_receipt_upload])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses_recorded")

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [
            models.Index(fields=["expense_date", "category"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} — {self.amount} ({self.expense_date})"
