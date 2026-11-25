from django.urls import path
from .views import AgingReportView, CreditLedgerView, OverdueListView, PaymentListCreateView, PaymentReverseView

app_name = "payments"

urlpatterns = [
    path("", PaymentListCreateView.as_view(), name="payment_list"),
    path("<int:pk>/reverse/", PaymentReverseView.as_view(), name="payment_reverse"),
    path("ledger/", CreditLedgerView.as_view(), name="credit_ledger"),
    path("overdue/", OverdueListView.as_view(), name="overdue_list"),
    path("aging/", AgingReportView.as_view(), name="aging"),
]
