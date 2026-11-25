from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import (
    AllObjectsManager,
    SoftDeleteModel,
    SoftDeleteQuerySet,
    StoreLocation,
    TimeStampedModel,
)
from apps.core.validators import validate_image_upload


class Category(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Brand(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(SoftDeleteModel):
    """Master footwear style (e.g., 'Marina Classic Loafer') with HSN, gender, image & tax rate."""

    class Gender(models.TextChoices):
        MEN = "MEN", _("Men")
        WOMEN = "WOMEN", _("Women")
        KIDS = "KIDS", _("Kids")
        UNISEX = "UNISEX", _("Unisex")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        INACTIVE = "INACTIVE", _("Inactive")

    name = models.CharField(max_length=160, db_index=True)
    style_code = models.CharField(max_length=50, blank=True, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    gender = models.CharField(max_length=10, choices=Gender.choices, default=Gender.UNISEX, db_index=True)
    hsn_code = models.CharField(max_length=20, blank=True, db_index=True, verbose_name="Item / Tariff Code", help_text="Optional customs/tariff code (e.g., 6403)")
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Default tax percentage (0.00% in Kuwait by default).",
    )
    unit = models.CharField(max_length=20, default="PAIR")
    image = models.ImageField(upload_to="products/", blank=True, null=True, validators=[validate_image_upload])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class ProductVariantQuerySet(SoftDeleteQuerySet):
    def with_stock(self):
        return self.annotate(
            calculated_stock=Coalesce(Sum("stock_movements__signed_quantity"), Value(0))
        )

    def low_stock(self):
        return self.with_stock().filter(calculated_stock__lte=F("reorder_level"))


class ActiveVariantManager(models.Manager):
    def get_queryset(self):
        return ProductVariantQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def with_stock(self):
        return self.get_queryset().with_stock()

    def low_stock(self):
        return self.get_queryset().low_stock()


class ProductVariant(SoftDeleteModel):
    """Specific size + color SKU of a Product. Stock is tracked via StockMovement & StockBalance."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    size = models.CharField(max_length=20, db_index=True)
    color = models.CharField(max_length=50, db_index=True)
    sku = models.CharField(max_length=64, unique=True, blank=True, db_index=True)
    barcode = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)
    cost_price = models.DecimalField(
        max_digits=13,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    wholesale_price = models.DecimalField(
        max_digits=13,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    retail_price = models.DecimalField(
        max_digits=13,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Tax Rate Override (%)",
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Optional variant tax override; blank uses product tax rate.",
    )
    reorder_level = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True, db_index=True)

    objects = ActiveVariantManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["product__name", "size", "color"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "size", "color"],
                condition=Q(is_deleted=False),
                name="uniq_active_variant_size_color",
            )
        ]
        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["barcode"]),
        ]

    def clean(self):
        if self.barcode == "":
            self.barcode = None

    def save(self, *args, **kwargs):
        if self.barcode == "":
            self.barcode = None
        if not self.sku:
            prefix = (self.product.style_code or slugify(self.product.name)[:6]).upper().replace("-", "")
            col = slugify(self.color)[:3].upper() or "CLR"
            sz = slugify(self.size).upper() or "SZ"
            base_sku = f"{prefix}-{sz}-{col}"
            candidate = base_sku
            suffix = 1
            while ProductVariant.all_objects.filter(sku=candidate).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_sku}-{suffix}"
            self.sku = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} — {self.color} / Size {self.size} ({self.sku})"

    @property
    def effective_gst_rate(self) -> Decimal:
        return Decimal(self.gst_rate if self.gst_rate is not None else self.product.tax_rate)

    @property
    def current_stock(self) -> int:
        if hasattr(self, "calculated_stock") and self.calculated_stock is not None:
            return int(self.calculated_stock)
        total = self.stock_movements.aggregate(total=Coalesce(Sum("signed_quantity"), Value(0)))["total"]
        return int(total or 0)

    def current_stock_at(self, location: StoreLocation | None = None) -> int:
        if location is None:
            return self.current_stock
        bal = self.location_balances.filter(location=location).first()
        if bal is not None:
            return int(bal.quantity)
        total = self.stock_movements.filter(location=location).aggregate(
            total=Coalesce(Sum("signed_quantity"), Value(0))
        )["total"]
        return int(total or 0)

    @property
    def is_low_stock(self) -> bool:
        return self.current_stock <= self.reorder_level


class StockBalance(TimeStampedModel):
    """Synchronized per-variant per-location stock balance locked during ledger mutations."""

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="location_balances")
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, related_name="stock_balances")
    quantity = models.IntegerField(default=0)

    class Meta:
        unique_together = [("variant", "location")]
        indexes = [
            models.Index(fields=["location", "variant"]),
        ]

    def __str__(self):
        return f"{self.variant.sku} @ {self.location.code}: {self.quantity}"


class StockMovement(models.Model):
    """Append-only stock ledger. Stock is never edited directly."""

    class MovementType(models.TextChoices):
        OPENING_STOCK = "OPENING_STOCK", _("Opening Stock (+)")
        PURCHASE = "PURCHASE", _("Purchase (+)")
        SALE = "SALE", _("Sale (-)")
        SALE_RETURN = "SALE_RETURN", _("Sale Return (+)")
        PURCHASE_RETURN = "PURCHASE_RETURN", _("Purchase Return (-)")
        DAMAGE = "DAMAGE", _("Damage (-)")
        ADJUSTMENT = "ADJUSTMENT", _("Adjustment (+/-)")
        TRANSFER_IN = "TRANSFER_IN", _("Transfer In (+)")
        TRANSFER_OUT = "TRANSFER_OUT", _("Transfer Out (-)")

    class Direction(models.TextChoices):
        IN = "IN", _("Stock In (+)")
        OUT = "OUT", _("Stock Out (-)")

    INBOUND_TYPES = {
        MovementType.OPENING_STOCK,
        MovementType.PURCHASE,
        MovementType.SALE_RETURN,
        MovementType.TRANSFER_IN,
    }
    OUTBOUND_TYPES = {
        MovementType.SALE,
        MovementType.PURCHASE_RETURN,
        MovementType.DAMAGE,
        MovementType.TRANSFER_OUT,
    }

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="stock_movements")
    location = models.ForeignKey(
        StoreLocation,
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, db_index=True)
    direction = models.CharField(max_length=5, choices=Direction.choices, default=Direction.IN)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    signed_quantity = models.IntegerField(editable=False)
    reference = models.CharField(max_length=120, blank=True, db_index=True)
    reason = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["variant", "created_at"]),
            models.Index(fields=["location", "variant"]),
            models.Index(fields=["movement_type", "created_at"]),
        ]

    def __str__(self):
        sign = "+" if self.signed_quantity >= 0 else ""
        return f"{self.variant.sku} {self.movement_type} {sign}{self.signed_quantity}"

    def clean(self):
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        if self.location_id is None:
            self.location = StoreLocation.get_default()
        if self.movement_type in self.INBOUND_TYPES:
            self.direction = self.Direction.IN
        elif self.movement_type in self.OUTBOUND_TYPES:
            self.direction = self.Direction.OUT
        elif self.movement_type == self.MovementType.ADJUSTMENT:
            if self.direction not in (self.Direction.IN, self.Direction.OUT):
                raise ValidationError("Adjustment must specify IN or OUT direction.")
        self.signed_quantity = self.quantity if self.direction == self.Direction.IN else -self.quantity

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("StockMovement rows are immutable and cannot be edited.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("StockMovement rows cannot be deleted.")


class StockTransfer(TimeStampedModel):
    transfer_number = models.CharField(max_length=40, unique=True, db_index=True)
    from_location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, related_name="transfers_out")
    to_location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, related_name="transfers_in")
    transfer_date = models.DateField(default=timezone.localdate, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_transfers")

    class Meta:
        ordering = ["-transfer_date", "-id"]

    def __str__(self):
        return f"{self.transfer_number}: {self.from_location.code} -> {self.to_location.code}"


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="transfer_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])


class StockCount(TimeStampedModel):
    count_number = models.CharField(max_length=40, unique=True, db_index=True)
    location = models.ForeignKey(StoreLocation, on_delete=models.PROTECT, related_name="stock_counts")
    count_date = models.DateField(default=timezone.localdate, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_counts")

    class Meta:
        ordering = ["-count_date", "-id"]

    def __str__(self):
        return f"{self.count_number} ({self.location.code})"


class StockCountItem(models.Model):
    stock_count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="count_items")
    system_quantity = models.IntegerField()
    counted_quantity = models.PositiveIntegerField()
    variance_quantity = models.IntegerField()
