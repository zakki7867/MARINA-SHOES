from rest_framework import serializers
from apps.inventory.models import ProductVariant
from .models import CreditNote, Invoice, InvoiceItem


class InvoiceItemInputSerializer(serializers.Serializer):
    variant = serializers.PrimaryKeyRelatedField(queryset=ProductVariant.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=13, decimal_places=3, required=False)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=0)


class InvoiceItemSerializer(serializers.ModelSerializer):
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = InvoiceItem
        fields = [
            "id",
            "variant",
            "variant_sku",
            "description",
            "hsn_code",
            "quantity",
            "unit_price",
            "discount_amount",
            "taxable_value",
            "tax_rate",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "line_total",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    balance_due = serializers.DecimalField(max_digits=13, decimal_places=3, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "financial_year",
            "customer",
            "customer_name",
            "price_list",
            "invoice_date",
            "due_date",
            "place_of_supply_state",
            "status",
            "subtotal",
            "discount_total",
            "taxable_amount",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "grand_total",
            "amount_paid",
            "balance_due",
            "items",
        ]


class CreditNoteSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = CreditNote
        fields = [
            "id",
            "credit_note_number",
            "invoice",
            "invoice_number",
            "customer",
            "customer_name",
            "issue_date",
            "reason",
            "taxable_amount",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "total_amount",
        ]
