from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Abstract base model with created and updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet supporting soft deletion filtering."""

    def active(self):
        return self.filter(is_deleted=False)

    def deleted(self):
        return self.filter(is_deleted=True)

    def delete(self):
        return super().update(is_deleted=True, deleted_at=timezone.now())


class ActiveManager(models.Manager):
    """Default manager returning only non-deleted records."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)


class AllObjectsManager(models.Manager):
    """Manager returning all records including soft-deleted ones."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteModel(TimeStampedModel):
    """Abstract base model implementing soft deletion."""

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def delete(self, using=None, keep_parents=False):
        self.soft_delete()


class StoreLocation(TimeStampedModel):
    """Physical store or warehouse location for multi-location stock tracking."""

    class LocationType(models.TextChoices):
        STORE = "STORE", _("Store / Showroom")
        WAREHOUSE = "WAREHOUSE", _("Warehouse / Godown")

    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    location_type = models.CharField(max_length=15, choices=LocationType.choices, default=LocationType.STORE)
    state_code = models.CharField(max_length=10, default=settings.DEFAULT_STATE_CODE, verbose_name="Governorate / Region")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_default = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        if self.is_default:
            StoreLocation.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls):
        loc = cls.objects.filter(is_active=True, is_default=True).first() or cls.objects.filter(is_active=True).first()
        if loc:
            return loc
        return cls.objects.create(
            code="MAIN",
            name="Main Store",
            location_type=cls.LocationType.STORE,
            state_code=settings.DEFAULT_STATE_CODE,
            is_default=True,
            is_active=True,
        )


class StoreSettings(TimeStampedModel):
    """Singleton configuration for store identity, tax region, tax mode, and stock rules."""

    class TaxMode(models.TextChoices):
        EXCLUSIVE = "EXCLUSIVE", _("Tax Exclusive (Tax added on top)")
        INCLUSIVE = "INCLUSIVE", _("Tax Inclusive (Tax included in rate)")

    store_name = models.CharField(max_length=150, default="MARINA SHOES")
    tagline = models.CharField(max_length=200, default="Wholesale & Retail Footwear — Kuwait", blank=True)
    gstin = models.CharField(max_length=20, blank=True, verbose_name="Tax ID / CR No.")
    state_code = models.CharField(max_length=10, default=settings.DEFAULT_STATE_CODE, verbose_name="Governorate / Region")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    currency_code = models.CharField(max_length=10, default=settings.DEFAULT_CURRENCY_CODE)
    currency_symbol = models.CharField(max_length=10, default=settings.DEFAULT_CURRENCY_SYMBOL)
    default_tax_mode = models.CharField(max_length=15, choices=TaxMode.choices, default=TaxMode.EXCLUSIVE)
    allow_negative_stock = models.BooleanField(
        default=False,
        help_text="Owner-controlled setting allowing stock quantities to go below zero.",
    )
    financial_year_start_month = models.PositiveSmallIntegerField(default=4)
    invoice_prefix = models.CharField(max_length=10, default="MS")
    invoice_number_format = models.CharField(
        max_length=50,
        default="{prefix}/{fy}/{seq:04d}",
        help_text="Available placeholders: {prefix}, {fy}, {seq:04d}",
    )
    invoice_terms = models.TextField(
        blank=True,
        default="Goods once sold may be exchanged within 7 days in unused condition with original invoice.",
    )
    default_credit_days = models.PositiveIntegerField(default=30)

    class Meta:
        verbose_name = "Store Settings"
        verbose_name_plural = "Store Settings"

    def __str__(self):
        return self.store_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class DocumentSequence(models.Model):
    """Atomic sequence counter for concurrency-safe document numbering."""

    prefix = models.CharField(max_length=20, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.prefix}: {self.last_number}"
