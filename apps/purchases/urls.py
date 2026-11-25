from django.urls import path
from .views import (
    GoodsReceiptCreateView,
    GoodsReceiptDetailView,
    GoodsReceiptListView,
    PurchaseOrderCreateView,
    PurchaseOrderDetailView,
    PurchaseOrderListView,
    SupplierCreateView,
    SupplierDetailView,
    SupplierListView,
    SupplierPaymentListCreateView,
    SupplierUpdateView,
)

app_name = "purchases"

urlpatterns = [
    path("suppliers/", SupplierListView.as_view(), name="supplier_list"),
    path("suppliers/new/", SupplierCreateView.as_view(), name="supplier_create"),
    path("suppliers/<int:pk>/", SupplierDetailView.as_view(), name="supplier_detail"),
    path("suppliers/<int:pk>/edit/", SupplierUpdateView.as_view(), name="supplier_edit"),
    path("orders/", PurchaseOrderListView.as_view(), name="po_list"),
    path("orders/new/", PurchaseOrderCreateView.as_view(), name="po_create"),
    path("orders/<int:pk>/", PurchaseOrderDetailView.as_view(), name="po_detail"),
    path("orders/<int:po_pk>/receive/", GoodsReceiptCreateView.as_view(), name="po_receive"),
    path("receipts/", GoodsReceiptListView.as_view(), name="grn_list"),
    path("receipts/new/", GoodsReceiptCreateView.as_view(), name="grn_create"),
    path("receipts/<int:pk>/", GoodsReceiptDetailView.as_view(), name="grn_detail"),
    path("payments/", SupplierPaymentListCreateView.as_view(), name="supplier_payments"),
]
