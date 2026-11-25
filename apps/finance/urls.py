from django.urls import path
from .views import BankBookView, CashBookView, FinanceOverviewView

app_name = "finance"

urlpatterns = [
    path("", FinanceOverviewView.as_view(), name="overview"),
    path("cash-book/", CashBookView.as_view(), name="cash_book"),
    path("bank-book/", BankBookView.as_view(), name="bank_book"),
]
