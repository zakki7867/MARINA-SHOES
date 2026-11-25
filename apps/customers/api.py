from rest_framework import serializers, viewsets
from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    outstanding_balance = serializers.DecimalField(max_digits=13, decimal_places=3, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "customer_type",
            "phone",
            "whatsapp_number",
            "email",
            "gstin",
            "address",
            "city",
            "state_code",
            "credit_limit",
            "payment_terms_days",
            "opening_balance",
            "outstanding_balance",
        ]


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by("name")
    serializer_class = CustomerSerializer
    permission_classes = [HasRolePermission]
    allowed_roles_by_method = {
        "GET": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "HEAD": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "OPTIONS": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "POST": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "PUT": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "PATCH": (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF),
        "DELETE": (User.Role.OWNER, User.Role.MANAGER),
    }

    def perform_create(self, serializer):
        obj = serializer.save()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=obj,
            changes={"name": obj.name, "type": obj.customer_type},
            request=self.request,
            notes="Created customer via API",
        )

    def perform_update(self, serializer):
        obj = serializer.save()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=obj,
            changes={"name": obj.name, "credit_limit": obj.credit_limit},
            request=self.request,
            notes="Updated customer via API",
        )

    def perform_destroy(self, instance):
        instance.soft_delete()
        log_audit(
            user=self.request.user,
            action=AuditLog.Action.DELETE,
            instance=instance,
            request=self.request,
            notes="Soft-deleted customer via API",
        )
