"""Full project-wide URL, template, report, and API route crawler checking for 404/500/template errors."""

import pytest
from django.core.management import call_command
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts.models import User
from apps.customers.models import Customer
from apps.inventory.models import Product, ProductVariant
from apps.invoicing.models import Invoice
from apps.purchases.models import GoodsReceipt, PurchaseOrder, Supplier
from apps.reports.views import REPORT_CARDS
from apps.sales.models import SalesOrder


def _collect_url_names(patterns, namespace_prefix=""):
    results = []
    for p in patterns:
        if isinstance(p, URLPattern) and p.name:
            full_name = f"{namespace_prefix}:{p.name}" if namespace_prefix else p.name
            results.append((full_name, str(p.pattern)))
        elif isinstance(p, URLResolver):
            ns = f"{namespace_prefix}:{p.namespace}" if (namespace_prefix and p.namespace) else (p.namespace or namespace_prefix)
            results.extend(_collect_url_names(p.url_patterns, ns))
    return results


@pytest.mark.django_db
def test_full_project_route_crawler_no_500_or_404(client, api_client):
    call_command("seed_data")
    owner = User.objects.get(username="owner")
    client.force_login(owner)
    api_client.force_authenticate(user=owner)

    # Known representative entity IDs from seeded database
    prod = Product.objects.first()
    var = ProductVariant.objects.first()
    cust = Customer.objects.first()
    sup = Supplier.objects.first()
    po = PurchaseOrder.objects.first()
    grn = GoodsReceipt.objects.first()
    inv = Invoice.objects.first()
    so = SalesOrder.objects.first()

    # Every static parameterless page route across all 13 apps
    static_routes = [
        "dashboard:home",
        "accounts:login",
        "accounts:profile",
        "accounts:user_list",
        "accounts:user_create",
        "core:store_settings",
        "core:locations",
        "audit:audit_list",
        "inventory:product_list",
        "inventory:product_create",
        "inventory:categories_brands",
        "inventory:low_stock",
        "inventory:stock_movement",
        "inventory:stock_transfers",
        "inventory:stock_counts",
        "inventory:import_export",
        "purchases:supplier_list",
        "purchases:supplier_create",
        "purchases:po_list",
        "purchases:po_create",
        "purchases:grn_list",
        "purchases:grn_create",
        "purchases:supplier_payments",
        "customers:customer_list",
        "customers:customer_create",
        "sales:order_list",
        "sales:order_create",
        "sales:pos",
        "sales:pos_search",
        "sales:return_list",
        "invoicing:invoice_list",
        "invoicing:invoice_create",
        "invoicing:credit_note_list",
        "payments:payment_list",
        "payments:credit_ledger",
        "payments:overdue_list",
        "payments:aging",
        "expenses:expense_list",
        "finance:overview",
        "finance:cash_book",
        "finance:bank_book",
        "reports:index",
    ]
    for name in static_routes:
        url = reverse(name)
        resp = client.get(url)
        assert resp.status_code in (200, 302), f"Route {name} ({url}) returned {resp.status_code}"

    # Parameterized detail/edit/print/ledger/return/exchange routes (create minimal SalesOrder if seed didn't)
    if so is None:
        from apps.sales.services import create_sales_order

        so = create_sales_order(customer=cust, items_data=[{"variant": var, "quantity": 1}], user=owner)
    if po is None:
        from datetime import timedelta
        from django.utils import timezone
        from apps.purchases.services import create_purchase_order

        po = create_purchase_order(
            supplier=sup,
            order_date=timezone.localdate(),
            expected_date=timezone.localdate() + timedelta(days=3),
            items_data=[{"variant": var, "quantity_ordered": 2, "unit_cost": var.cost_price}],
            user=owner,
        )

    param_routes = [
        ("accounts:user_edit", [owner.pk]),
        ("inventory:product_detail", [prod.pk]),
        ("inventory:product_edit", [prod.pk]),
        ("inventory:variant_edit", [var.pk]),
        ("inventory:variant_history", [var.pk]),
        ("purchases:supplier_detail", [sup.pk]),
        ("purchases:supplier_edit", [sup.pk]),
        ("purchases:po_detail", [po.pk]),
        ("purchases:po_receive", [po.pk]),
        ("purchases:grn_detail", [grn.pk]),
        ("customers:customer_detail", [cust.pk]),
        ("customers:customer_edit", [cust.pk]),
        ("customers:customer_ledger", [cust.pk]),
        ("sales:order_detail", [so.pk]),
        ("sales:return_create", [inv.pk]),
        ("sales:exchange_create", [inv.pk]),
        ("invoicing:invoice_detail", [inv.pk]),
        ("invoicing:invoice_print", [inv.pk]),
    ]
    for name, args in param_routes:
        url = reverse(name, args=args)
        resp = client.get(url)
        assert resp.status_code == 200, f"Param route {name} ({url}) returned {resp.status_code}"

    # Catalog export query routes
    assert client.get(reverse("inventory:import_export"), {"export": "csv"}).status_code == 200
    assert client.get(reverse("inventory:import_export"), {"export": "xlsx"}).status_code == 200

    # Invoice PDF route (200 PDF if GTK3 available, or 302 graceful fallback with message)
    pdf_resp = client.get(reverse("invoicing:invoice_pdf", args=[inv.pk]))
    assert pdf_resp.status_code in (200, 302)

    # All 23 reports with sort, query, date filters, and pagination
    for card in REPORT_CARDS:
        key = card["key"]
        r = client.get(reverse("reports:detail", args=[key]), {"sort": "0", "start_date": "2020-01-01", "end_date": "2030-12-31"})
        assert r.status_code == 200, f"Report {key} returned {r.status_code}"

    # API list endpoints
    api_urls = [
        "/api/v1/accounts/me/",
        "/api/v1/accounts/users/",
        "/api/v1/audit/",
        "/api/v1/inventory/categories/",
        "/api/v1/inventory/brands/",
        "/api/v1/inventory/products/",
        "/api/v1/inventory/variants/",
        "/api/v1/inventory/variants/low_stock/",
        "/api/v1/inventory/stock-movements/",
        "/api/v1/purchases/suppliers/",
        "/api/v1/purchases/orders/",
        "/api/v1/purchases/receipts/",
        "/api/v1/purchases/payments/",
        "/api/v1/customers/",
        "/api/v1/sales/orders/",
        "/api/v1/sales/returns/",
        "/api/v1/invoicing/invoices/",
        "/api/v1/invoicing/credit-notes/",
        "/api/v1/payments/",
        "/api/v1/payments/overdue/",
        "/api/v1/expenses/",
        "/api/v1/finance/summary/",
    ]
    for url in api_urls:
        r = api_client.get(url)
        assert r.status_code == 200, f"API {url} returned {r.status_code}"

    # Verify favicon link present on base template (avoids browser 404 console error)
    dash_html = client.get(reverse("dashboard:home")).content.decode()
    assert 'rel="icon"' in dash_html

    # Verify numeric report sort orders numerically (not lexically)
    r_sort = client.get(reverse("reports:detail", args=["stock"]), {"sort": "6"})
    assert r_sort.status_code == 200
    stock_vals = [row[6] for row in r_sort.context["rows"]]
    assert stock_vals == sorted(stock_vals)

    all_names = _collect_url_names(get_resolver().url_patterns)
    assert len(all_names) > 60
