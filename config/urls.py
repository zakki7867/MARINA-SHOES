"""URL Configuration for Marina Shoes BMS."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.api import CurrentUserAPIView, UserViewSet
from apps.audit.views import AuditLogViewSet
from apps.core.views import HealthCheckView
from apps.inventory.api import (
    BrandViewSet,
    CategoryViewSet,
    ProductVariantViewSet,
    ProductViewSet,
    StockMovementViewSet,
)
from apps.purchases.api import (
    GoodsReceiptViewSet,
    PurchaseOrderViewSet,
    SupplierPaymentViewSet,
    SupplierViewSet,
)
from apps.customers.api import CustomerViewSet
from apps.sales.api import SalesOrderViewSet, SalesReturnViewSet
from apps.invoicing.api import CreditNoteViewSet, InvoiceViewSet
from apps.payments.api import CustomerPaymentViewSet
from apps.expenses.views import ExpenseViewSet
from apps.finance.views import FinanceSummaryAPIView

router = DefaultRouter()
router.register(r"accounts/users", UserViewSet, basename="api-users")
router.register(r"audit", AuditLogViewSet, basename="api-audit")
router.register(r"inventory/categories", CategoryViewSet, basename="api-categories")
router.register(r"inventory/brands", BrandViewSet, basename="api-brands")
router.register(r"inventory/products", ProductViewSet, basename="api-products")
router.register(r"inventory/variants", ProductVariantViewSet, basename="api-variants")
router.register(r"inventory/stock-movements", StockMovementViewSet, basename="api-stock-movements")
router.register(r"purchases/suppliers", SupplierViewSet, basename="api-suppliers")
router.register(r"purchases/orders", PurchaseOrderViewSet, basename="api-purchase-orders")
router.register(r"purchases/receipts", GoodsReceiptViewSet, basename="api-goods-receipts")
router.register(r"purchases/payments", SupplierPaymentViewSet, basename="api-supplier-payments")
router.register(r"customers", CustomerViewSet, basename="api-customers")
router.register(r"sales/orders", SalesOrderViewSet, basename="api-sales-orders")
router.register(r"sales/returns", SalesReturnViewSet, basename="api-sales-returns")
router.register(r"invoicing/invoices", InvoiceViewSet, basename="api-invoices")
router.register(r"invoicing/credit-notes", CreditNoteViewSet, basename="api-credit-notes")
router.register(r"payments", CustomerPaymentViewSet, basename="api-payments")
router.register(r"expenses", ExpenseViewSet, basename="api-expenses")

urlpatterns = [
    path(getattr(settings, "DJANGO_ADMIN_URL", "admin/"), admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("healthz/", HealthCheckView.as_view(), name="healthz"),
    path("", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("core/", include("apps.core.urls")),
    path("audit/", include("apps.audit.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("purchases/", include("apps.purchases.urls")),
    path("customers/", include("apps.customers.urls")),
    path("sales/", include("apps.sales.urls")),
    path("invoicing/", include("apps.invoicing.urls")),
    path("payments/", include("apps.payments.urls")),
    path("expenses/", include("apps.expenses.urls")),
    path("finance/", include("apps.finance.urls")),
    path("reports/", include("apps.reports.urls")),
    path("api/v1/finance/summary/", FinanceSummaryAPIView.as_view(), name="api-finance-summary"),
    path("api/v1/accounts/me/", CurrentUserAPIView.as_view(), name="api-current-user"),
    path("api/v1/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
