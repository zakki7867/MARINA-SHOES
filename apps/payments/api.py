from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from .models import CustomerPayment
from .serializers import CustomerPaymentSerializer
from .services import get_overdue_invoices, record_customer_payment


class CustomerPaymentViewSet(viewsets.ModelViewSet):
    queryset = CustomerPayment.objects.select_related("customer").prefetch_related("allocations__invoice").all()
    serializer_class = CustomerPaymentSerializer
    permission_classes = [HasRolePermission]
    allowed_roles = (User.Role.OWNER, User.Role.MANAGER, User.Role.BILLING_STAFF)
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            payment = record_customer_payment(
                customer=d["customer"],
                invoice=d.get("invoice"),
                amount=d["amount"],
                payment_mode=d["payment_mode"],
                is_advance=d.get("is_advance", False),
                payment_date=d.get("payment_date"),
                reference_no=d.get("reference_no", ""),
                notes=d.get("notes", ""),
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CustomerPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        data = [
            {
                "id": inv.pk,
                "invoice_number": inv.invoice_number,
                "customer_name": inv.customer.name,
                "due_date": inv.due_date,
                "grand_total": str(inv.grand_total),
                "balance_due": str(inv.balance_due),
            }
            for inv in get_overdue_invoices()
        ]
        return Response(data)
