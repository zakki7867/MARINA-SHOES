from django.urls import path
from .views import (
    ConvertOrderToInvoiceView,
    POSVariantSearchView,
    POSView,
    SalesExchangeCreateView,
    SalesOrderCreateView,
    SalesOrderDetailView,
    SalesOrderListView,
    SalesReturnCreateView,
    SalesReturnListView,
)

app_name = "sales"

urlpatterns = [
    path("pos/", POSView.as_view(), name="pos"),
    path("pos/search/", POSVariantSearchView.as_view(), name="pos_search"),
    path("orders/", SalesOrderListView.as_view(), name="order_list"),
    path("orders/new/", SalesOrderCreateView.as_view(), name="order_create"),
    path("orders/<int:pk>/", SalesOrderDetailView.as_view(), name="order_detail"),
    path("orders/<int:pk>/invoice/", ConvertOrderToInvoiceView.as_view(), name="order_to_invoice"),
    path("returns/", SalesReturnListView.as_view(), name="return_list"),
    path("returns/invoice/<int:invoice_pk>/", SalesReturnCreateView.as_view(), name="return_create"),
    path("exchanges/invoice/<int:invoice_pk>/", SalesExchangeCreateView.as_view(), name="exchange_create"),
]
