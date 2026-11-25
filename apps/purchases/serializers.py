from rest_framework import serializers
from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierPayment,
)


class SupplierSerializer(serializers.ModelSerializer):
    outstanding_balance = serializers.DecimalField(max_digits=13, decimal_places=3, read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "contact_person",
            "phone",
            "email",
            "gstin",
            "state_code",
            "address",
            "opening_balance",
            "outstanding_balance",
        ]


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ["id", "variant", "variant_sku", "quantity_ordered", "unit_cost", "line_total"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "po_number", "supplier", "supplier_name", "order_date", "expected_date", "status", "total_amount", "notes", "items"]


class GoodsReceiptItemSerializer(serializers.ModelSerializer):
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = GoodsReceiptItem
        fields = ["id", "variant", "variant_sku", "quantity_received", "unit_cost", "line_total"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    items = GoodsReceiptItemSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = ["id", "grn_number", "purchase_order", "supplier", "supplier_name", "received_date", "supplier_invoice_no", "total_amount", "notes", "items"]


class SupplierPaymentSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = SupplierPayment
        fields = ["id", "payment_number", "supplier", "supplier_name", "payment_date", "amount", "payment_mode", "reference_no", "notes"]
        read_only_fields = ["id", "payment_number"]
