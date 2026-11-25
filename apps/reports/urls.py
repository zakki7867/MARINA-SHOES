from django.urls import path
from .views import ReportDetailView, ReportIndexView

app_name = "reports"

urlpatterns = [
    path("", ReportIndexView.as_view(), name="index"),
    path("<str:report_key>/", ReportDetailView.as_view(), name="detail"),
]
