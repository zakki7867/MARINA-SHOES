from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from apps.audit.models import AuditLog
from apps.audit.services import log_audit
from .models import GoodsReceipt, PurchaseOrder, Supplier, SupplierPayment
from .serializers import (
    GoodsReceiptSerializer,
    PurchaseOrderSerializer,
    SupplierPaymentSerializer,
    SupplierSerializer,
)
from .services import record_supplier_payment

OPS_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER)
FINANCE_ROLES = (User.Role.OWNER, User.Role.MANAGER)


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all().order_by("name")
    serializer_class = SupplierSerializer
    permission_classes = [HasRolePermission]
    allowed_roles_by_method = {
        "GET": OPS_ROLES,
        "HEAD": OPS_ROLES,
        "OPTIONS": OPS_ROLES,
        "POST": OPS_ROLES,
        "PUT": OPS_ROLES,
        "PATCH": OPS_ROLES,
        "DELETE": FINANCE_ROLES,
    }

    def perform_create(self, serializer):
        obj = serializer.save()
        log_audit(user=self.request.user, action=AuditLog.Action.CREATE, instance=obj, changes={"name": obj.name}, request=self.request, notes="Created supplier via API")

    def perform_update(self, serializer):
        obj = serializer.save()
        log_audit(user=self.request.user, action=AuditLog.Action.UPDATE, instance=obj, changes={"name": obj.name}, request=self.request, notes="Updated supplier via API")

    def perform_destroy(self, instance):
        instance.soft_delete()
        log_audit(user=self.request.user, action=AuditLog.Action.DELETE, instance=instance, request=self.request, notes="Soft-deleted supplier via API")


class PurchaseOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PurchaseOrder.objects.select_related("supplier").prefetch_related("items__variant").all()
    serializer_class = PurchaseOrderSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = OPS_ROLES


class GoodsReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = GoodsReceipt.objects.select_related("supplier", "purchase_order").prefetch_related("items__variant").all()
    serializer_class = GoodsReceiptSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = OPS_ROLES


class SupplierPaymentViewSet(viewsets.ModelViewSet):
    queryset = SupplierPayment.objects.select_related("supplier").all()
    serializer_class = SupplierPaymentSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = FINANCE_ROLES
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        try:
            payment = record_supplier_payment(
                supplier=d["supplier"],
                amount=d["amount"],
                payment_mode=d["payment_mode"],
                payment_date=d.get("payment_date"),
                reference_no=d.get("reference_no", ""),
                notes=d.get("notes", ""),
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SupplierPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
