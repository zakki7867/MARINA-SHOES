from django.urls import path
from .views import (
    CategoryBrandManageView,
    LowStockListView,
    ProductCreateView,
    ProductDetailView,
    ProductImportExportView,
    ProductListView,
    ProductSoftDeleteView,
    ProductUpdateView,
    StockCountListCreateView,
    StockMovementCreateView,
    StockTransferListCreateView,
    VariantCreateView,
    VariantSoftDeleteView,
    VariantStockHistoryView,
    VariantUpdateView,
)

app_name = "inventory"

urlpatterns = [
    path("", ProductListView.as_view(), name="product_list"),
    path("products/new/", ProductCreateView.as_view(), name="product_create"),
    path("products/<int:pk>/", ProductDetailView.as_view(), name="product_detail"),
    path("products/<int:pk>/edit/", ProductUpdateView.as_view(), name="product_edit"),
    path("products/<int:pk>/delete/", ProductSoftDeleteView.as_view(), name="product_delete"),
    path("products/<int:product_pk>/variants/new/", VariantCreateView.as_view(), name="variant_create"),
    path("variants/<int:pk>/edit/", VariantUpdateView.as_view(), name="variant_edit"),
    path("variants/<int:pk>/delete/", VariantSoftDeleteView.as_view(), name="variant_delete"),
    path("variants/<int:pk>/history/", VariantStockHistoryView.as_view(), name="variant_history"),
    path("stock-movements/", StockMovementCreateView.as_view(), name="stock_movement"),
    path("transfers/", StockTransferListCreateView.as_view(), name="stock_transfers"),
    path("counts/", StockCountListCreateView.as_view(), name="stock_counts"),
    path("import-export/", ProductImportExportView.as_view(), name="import_export"),
    path("low-stock/", LowStockListView.as_view(), name="low_stock"),
    path("categories-brands/", CategoryBrandManageView.as_view(), name="categories_brands"),
]
