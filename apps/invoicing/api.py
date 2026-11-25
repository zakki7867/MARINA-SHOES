from decimal import Decimal
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.inventory.services import InsufficientStockError
from .models import CreditNote, Invoice
from .serializers import CreditNoteSerializer, InvoiceItemInputSerializer, InvoiceSerializer
from .services import CreditLimitExceededError, cancel_invoice, create_invoice


class InvoiceCreateAPISerializer(serializers.Serializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.all())
    location = serializers.PrimaryKeyRelatedField(queryset=StoreLocation.objects.filter(is_active=True), required=False)
    price_list = serializers.ChoiceField(choices=["WHOLESALE", "RETAIL"], required=False)
    tax_mode = serializers.ChoiceField(choices=StoreSettings.TaxMode.choices, required=False)
    invoice_discount = serializers.DecimalField(max_digits=13, decimal_places=3, required=False, default=Decimal("0.000"))
    place_of_supply_state = serializers.CharField(required=False, allow_blank=True)
    issue_now = serializers.BooleanField(default=True)
    override_credit_limit = serializers.BooleanField(default=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    items = InvoiceItemInputSerializer(many=True)


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.select_related("customer", "location").prefetch_related("items__variant").all()
    serializer_class = InvoiceSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        ser = InvoiceCreateAPISerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        override = d.get("override_credit_limit", False) and request.user.has_any_role(
            User.Role.OWNER, User.Role.MANAGER
        )
        try:
            inv = create_invoice(
                customer=d["customer"],
                location=d.get("location"),
                price_list=d.get("price_list"),
                tax_mode=d.get("tax_mode"),
                invoice_discount=d.get("invoice_discount") or Decimal("0.00"),
                place_of_supply_state=d.get("place_of_supply_state") or None,
                notes=d.get("notes", ""),
                items_data=d["items"],
                issue_now=d.get("issue_now", True),
                override_credit_limit=override,
                enforce_price_permission=True,
                user=request.user,
                request=request,
            )
        except DjangoPermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except (InsufficientStockError, CreditLimitExceededError, DjangoValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(inv).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        if not request.user.has_any_role(User.Role.OWNER, User.Role.MANAGER):
            return Response({"detail": "Only Owner or Manager can cancel issued invoices."}, status=status.HTTP_403_FORBIDDEN)
        invoice = self.get_object()
        try:
            cancel_invoice(invoice=invoice, user=request.user, reason=request.data.get("reason", ""), request=request)
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(invoice).data)


class CreditNoteViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CreditNote.objects.select_related("invoice", "customer").all()
    serializer_class = CreditNoteSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)
