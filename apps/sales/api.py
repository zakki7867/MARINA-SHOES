from rest_framework import serializers, viewsets
from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from .models import SalesOrder, SalesOrderItem, SalesReturn


class SalesOrderItemSerializer(serializers.ModelSerializer):
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = ["id", "variant", "variant_sku", "quantity", "unit_price", "discount_percent", "line_total"]


class SalesOrderSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = SalesOrder
        fields = ["id", "order_number", "customer", "customer_name", "price_list", "order_date", "status", "subtotal", "discount_total", "grand_total", "notes", "items"]


class SalesReturnSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)

    class Meta:
        model = SalesReturn
        fields = ["id", "return_number", "customer", "customer_name", "invoice", "invoice_number", "return_date", "reason", "total_amount"]


class SalesOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SalesOrder.objects.select_related("customer").prefetch_related("items__variant").all()
    serializer_class = SalesOrderSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)


class SalesReturnViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SalesReturn.objects.select_related("customer", "invoice").all()
    serializer_class = SalesReturnSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)
