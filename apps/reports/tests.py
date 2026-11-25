from django.core.management import call_command
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
import pytest

from apps.inventory.models import StockBalance, StockMovement


@pytest.mark.django_db
def test_seed_data_all_23_reports_exports_dashboard_and_stock_balance_consistency(client):
    call_command("seed_data")

    # Verify StockBalance matches StockMovement SUM across every variant & location
    for bal in StockBalance.objects.select_related("variant", "location").all():
        ledger_sum = StockMovement.objects.filter(variant=bal.variant, location=bal.location).aggregate(
            t=Coalesce(Sum("signed_quantity"), Value(0))
        )["t"]
        assert bal.quantity == int(ledger_sum)

    from django.contrib.auth import get_user_model
    User = get_user_model()
    owner = User.objects.get(username="owner")
    storekeeper = User.objects.get(username="storekeeper")
    billing = User.objects.get(username="billing")

    client.force_login(owner)
    dash = client.get(reverse("dashboard:home"))
    assert dash.status_code == 200
    assert "Today's Sales" in dash.content.decode()
    assert "Gross Profit Estimate" in dash.content.decode()

    all_keys = [
        "stock", "stock_by_size", "stock_by_color", "stock_by_location",
        "low_stock", "stock_movements", "damaged_stock", "slow_moving",
        "daily_sales", "monthly_sales", "sales_by_product", "sales_by_category",
        "sales_by_brand", "wholesale_vs_retail", "sales_by_customer", "top_customers",
        "pending_payments", "customer_aging", "supplier_outstanding", "supplier_aging",
        "expenses", "profit", "gst_summary",
    ]
    for key in all_keys:
        assert client.get(reverse("reports:detail", kwargs={"report_key": key})).status_code == 200
        csv_resp = client.get(reverse("reports:detail", kwargs={"report_key": key}) + "?export=csv")
        assert csv_resp.status_code == 200
        assert csv_resp["Content-Type"].startswith("text/csv")

        xlsx_resp = client.get(reverse("reports:detail", kwargs={"report_key": key}) + "?export=xlsx")
        assert xlsx_resp.status_code == 200
        assert "spreadsheetml" in xlsx_resp["Content-Type"]

    # Storekeeper can view inventory reports, blocked from financial/sales reports
    client.force_login(storekeeper)
    assert client.get(reverse("reports:detail", kwargs={"report_key": "stock_by_location"})).status_code == 200
    assert client.get(reverse("reports:detail", kwargs={"report_key": "profit"})).status_code == 403
    assert client.get(reverse("reports:detail", kwargs={"report_key": "customer_aging"})).status_code == 403

    # Billing staff blocked from reports
    client.force_login(billing)
    assert client.get(reverse("reports:index")).status_code == 403
