from rest_framework import serializers
from apps.invoicing.models import Invoice
from .models import CustomerPayment, PaymentAllocation


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "amount"]


class CustomerPaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    invoice = serializers.PrimaryKeyRelatedField(
        queryset=Invoice.objects.all(), required=False, allow_null=True, write_only=True
    )

    class Meta:
        model = CustomerPayment
        fields = [
            "id",
            "receipt_number",
            "customer",
            "customer_name",
            "payment_date",
            "amount",
            "payment_mode",
            "is_advance",
            "reference_no",
            "notes",
            "invoice",
            "allocations",
        ]
        read_only_fields = ["id", "receipt_number"]
