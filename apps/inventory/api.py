from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import HasRolePermission
from .models import Brand, Category, Product, ProductVariant, StockMovement
from .serializers import (
    BrandSerializer,
    CategorySerializer,
    ProductSerializer,
    ProductVariantSerializer,
    StockMovementSerializer,
)
from .services import InsufficientStockError, record_stock_movement

READ_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER, User.Role.BILLING_STAFF)
WRITE_ROLES = (User.Role.OWNER, User.Role.MANAGER, User.Role.STOREKEEPER)


class InventoryPermissionMixin:
    permission_classes = [HasRolePermission]
    allowed_roles_by_method = {
        "GET": READ_ROLES,
        "HEAD": READ_ROLES,
        "OPTIONS": READ_ROLES,
        "POST": WRITE_ROLES,
        "PUT": WRITE_ROLES,
        "PATCH": WRITE_ROLES,
        "DELETE": WRITE_ROLES,
    }


class CategoryViewSet(InventoryPermissionMixin, viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class BrandViewSet(InventoryPermissionMixin, viewsets.ModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer


class ProductViewSet(InventoryPermissionMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("category", "brand").prefetch_related("variants").order_by("name")
    serializer_class = ProductSerializer

    def perform_destroy(self, instance):
        instance.soft_delete()


class ProductVariantViewSet(InventoryPermissionMixin, viewsets.ModelViewSet):
    queryset = (
        ProductVariant.objects.select_related("product")
        .prefetch_related("location_balances__location")
        .with_stock()
        .order_by("product__name", "size", "color")
    )
    serializer_class = ProductVariantSerializer

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        qs = ProductVariant.objects.select_related("product").low_stock()
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


class StockMovementViewSet(InventoryPermissionMixin, viewsets.ModelViewSet):
    queryset = StockMovement.objects.select_related("variant", "location", "user").all()
    serializer_class = StockMovementSerializer
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            movement = record_stock_movement(
                variant=data["variant"],
                location=data.get("location"),
                movement_type=data["movement_type"],
                quantity=data["quantity"],
                direction=data.get("direction"),
                reference=data.get("reference", ""),
                reason=data.get("reason", ""),
                notes=data.get("notes", ""),
                user=request.user,
                request=request,
            )
        except (InsufficientStockError, DjangoValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)
