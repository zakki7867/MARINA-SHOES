from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Custom user model with business role enforcement and optional assigned store/warehouse."""

    class Role(models.TextChoices):
        OWNER = "OWNER", _("Owner")
        MANAGER = "MANAGER", _("Manager")
        BILLING_STAFF = "BILLING_STAFF", _("Billing Staff")
        STOREKEEPER = "STOREKEEPER", _("Storekeeper")

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.BILLING_STAFF,
        db_index=True,
    )
    phone = models.CharField(max_length=30, blank=True)
    assigned_location = models.ForeignKey(
        "core.StoreLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_members",
    )

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_owner(self) -> bool:
        return self.is_superuser or self.role == self.Role.OWNER

    @property
    def is_manager(self) -> bool:
        return self.role == self.Role.MANAGER

    @property
    def is_billing_staff(self) -> bool:
        return self.role == self.Role.BILLING_STAFF

    @property
    def is_storekeeper(self) -> bool:
        return self.role == self.Role.STOREKEEPER

    def has_any_role(self, *roles: str) -> bool:
        if self.is_superuser:
            return True
        return self.role in roles
