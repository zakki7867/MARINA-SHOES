from django.urls import path
from .views import (
    CustomerCreateView,
    CustomerDetailView,
    CustomerLedgerView,
    CustomerListView,
    CustomerSoftDeleteView,
    CustomerUpdateView,
)

app_name = "customers"

urlpatterns = [
    path("", CustomerListView.as_view(), name="customer_list"),
    path("new/", CustomerCreateView.as_view(), name="customer_create"),
    path("<int:pk>/", CustomerDetailView.as_view(), name="customer_detail"),
    path("<int:pk>/ledger/", CustomerLedgerView.as_view(), name="customer_ledger"),
    path("<int:pk>/edit/", CustomerUpdateView.as_view(), name="customer_edit"),
    path("<int:pk>/delete/", CustomerSoftDeleteView.as_view(), name="customer_delete"),
]
