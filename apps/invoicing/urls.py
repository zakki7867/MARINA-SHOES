from django.urls import path
from .views import (
    CreditNoteListView,
    InvoiceApplyAdvanceView,
    InvoiceCancelView,
    InvoiceCreateView,
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceListView,
    InvoicePDFView,
    InvoicePrintView,
)

app_name = "invoicing"

urlpatterns = [
    path("", InvoiceListView.as_view(), name="invoice_list"),
    path("new/", InvoiceCreateView.as_view(), name="invoice_create"),
    path("<int:pk>/", InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<int:pk>/issue/", InvoiceIssueView.as_view(), name="invoice_issue"),
    path("<int:pk>/apply-advance/", InvoiceApplyAdvanceView.as_view(), name="invoice_apply_advance"),
    path("<int:pk>/cancel/", InvoiceCancelView.as_view(), name="invoice_cancel"),
    path("<int:pk>/print/", InvoicePrintView.as_view(), name="invoice_print"),
    path("<int:pk>/pdf/", InvoicePDFView.as_view(), name="invoice_pdf"),
    path("credit-notes/", CreditNoteListView.as_view(), name="credit_note_list"),
]
