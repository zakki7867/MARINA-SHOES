"""Pilot hardening verification suite: PostgreSQL safety, IGST GST summary, API parity, formsets & login hardening."""

from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.apps import check_production_secret_key
from apps.core.models import StoreLocation
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.finance.services import get_gst_summary
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import record_stock_movement
from apps.invoicing.models import Invoice
from apps.invoicing.services import cancel_invoice, create_invoice
from apps.payments.models import CustomerPayment
from apps.payments.services import record_customer_payment
from apps.purchases.models import Supplier
from apps.purchases.services import create_purchase_return, receive_goods
from apps.sales.models import SalesOrder
from apps.sales.services import process_sales_return


@pytest.fixture
def pilot_world(db, owner_user):
    cat = Category.objects.create(name="Pilot Sandals")
    brand = Brand.objects.create(name="Marina Pilot")
    prod = Product.objects.create(name="Pilot Comfort", category=cat, brand=brand, hsn_code="6402", tax_rate=Decimal("12.00"))
    var = ProductVariant.objects.create(
        product=prod, size="8", color="Tan", sku="PILOT-8-TAN",
        cost_price=Decimal("400.00"), wholesale_price=Decimal("600.00"), retail_price=Decimal("900.00"),
    )
    record_stock_movement(variant=var, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=40, user=owner_user)
    c_dl = Customer.objects.create(name="External GCC Wholesale", customer_type=Customer.CustomerType.WHOLESALE, phone="+96595551111", state_code="SA")
    c_mh = Customer.objects.create(name="Salmiya Wholesale", customer_type=Customer.CustomerType.WHOLESALE, phone="+96595552222", state_code="KW")
    sup = Supplier.objects.create(name="Pilot Supplier", phone="9555533333")
    return {"var": var, "c_dl": c_dl, "c_mh": c_mh, "sup": sup}


def test_batch1_igst_gst_summary_credit_note_and_invoice_lock_safety(db, owner_user, pilot_world):
    var = pilot_world["var"]
    c_dl = pilot_world["c_dl"]

    # Inter-state IGST sale: 2 * 600 = 1200 + 12% IGST (144) = 1344
    inv = create_invoice(customer=c_dl, price_list="WHOLESALE", items_data=[{"variant": var, "quantity": 2}], user=owner_user, issue_now=True)
    # Return 1 unit -> CN taxable 600, IGST 72, total 672
    process_sales_return(invoice=inv, items_data=[{"variant": var, "quantity": 1}], user=owner_user)

    gst = get_gst_summary()
    assert gst["outward_igst"] == Decimal("144.00")
    assert gst["credit_note_igst"] == Decimal("72.00")
    assert gst["net_igst"] == Decimal("72.00")
    assert gst["net_cgst"] == Decimal("0.00")
    assert gst["net_sgst"] == Decimal("0.00")
    assert gst["net_total_gst"] == Decimal("72.00")

    # Cancel a fresh draft/issued invoice verifies select_for_update path cleanly
    inv2 = create_invoice(customer=c_dl, items_data=[{"variant": var, "quantity": 1}], user=owner_user, issue_now=True)
    cancel_invoice(invoice=inv2, user=owner_user, reason="Test cancel")
    inv2.refresh_from_db()
    assert inv2.status == Invoice.Status.CANCELLED


def test_batch2_api_delete_parity_audit_purchase_cap_and_advance(db, api_client, owner_user, manager_user, billing_user, storekeeper_user, pilot_world):
    c_mh = pilot_world["c_mh"]
    sup = pilot_world["sup"]
    var = pilot_world["var"]

    # Billing staff cannot DELETE customers via API, Owner can (with AuditLog)
    api_client.force_authenticate(user=billing_user)
    assert api_client.delete(f"/api/v1/customers/{c_mh.pk}/").status_code == 403
    c_temp = Customer.objects.create(name="Del Temp", phone="9000012345")
    api_client.force_authenticate(user=owner_user)
    assert api_client.delete(f"/api/v1/customers/{c_temp.pk}/").status_code == 204
    assert AuditLog.objects.filter(model_name="Customer", action=AuditLog.Action.DELETE, object_id=str(c_temp.pk)).exists()

    # Storekeeper cannot DELETE suppliers via API; Manager cannot DELETE expenses via API
    api_client.force_authenticate(user=storekeeper_user)
    assert api_client.delete(f"/api/v1/purchases/suppliers/{sup.pk}/").status_code == 403

    exp = Expense.objects.create(category=Expense.Category.RENT, amount=Decimal("100.00"), payment_mode="CASH", created_by=owner_user)
    api_client.force_authenticate(user=manager_user)
    assert api_client.delete(f"/api/v1/expenses/{exp.pk}/").status_code == 403
    assert api_client.patch(f"/api/v1/expenses/{exp.pk}/", {"notes": "Adjusted note"}, format="json").status_code == 200
    assert AuditLog.objects.filter(model_name="Expense", action=AuditLog.Action.UPDATE, object_id=str(exp.pk)).exists()

    # CustomerPaymentViewSet supports is_advance=True and SupplierPaymentViewSet returns 400 on invalid supplier/amount
    api_client.force_authenticate(user=billing_user)
    resp_adv = api_client.post(
        "/api/v1/payments/",
        {"customer": c_mh.pk, "amount": "500.000", "payment_mode": "KNET", "is_advance": True},
        format="json",
    )
    assert resp_adv.status_code == 201
    assert CustomerPayment.objects.get(pk=resp_adv.json()["id"]).is_advance is True

    # Inactive customer payment rejected cleanly (400 via API, ValidationError via service)
    inactive_c = Customer.objects.create(name="Dormant Pay", phone="9000066666", is_active=False)
    with pytest.raises(ValidationError):
        record_customer_payment(customer=inactive_c, amount=Decimal("100.00"), payment_mode="CASH", user=owner_user)

    sup_inactive = Supplier.objects.create(name="Dormant Sup", phone="9000055555", is_active=False)
    api_client.force_authenticate(user=owner_user)
    resp_sp = api_client.post(
        "/api/v1/purchases/payments/",
        {"supplier": sup_inactive.pk, "amount": "100.00", "payment_mode": "CASH"},
        format="json",
    )
    assert resp_sp.status_code == 400

    # Purchase return linked to GRN cannot exceed received qty
    grn = receive_goods(supplier=sup, items_data=[{"variant": var, "quantity_received": 4, "unit_cost": Decimal("400.00")}], user=owner_user)
    with pytest.raises(ValidationError):
        create_purchase_return(supplier=sup, goods_receipt=grn, items_data=[{"variant": var, "quantity": 5, "unit_cost": Decimal("400.00")}], user=owner_user)


def test_batch2_invoice_and_sales_order_formsets_single_row_with_blank_extra_row(db, client, billing_user, pilot_world):
    var = pilot_world["var"]
    c_mh = pilot_world["c_mh"]
    loc = StoreLocation.get_default()
    client.force_login(billing_user)

    resp_inv = client.post(
        reverse("invoicing:invoice_create"),
        {
            "customer": c_mh.pk,
            "location": loc.pk,
            "price_list": "WHOLESALE",
            "tax_mode": "EXCLUSIVE",
            "invoice_date": timezone.localdate().isoformat(),
            "issue_now": "on",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-variant": var.pk,
            "items-0-quantity": "2",
            "items-0-unit_price": "",
            "items-0-discount_percent": "",
            "items-1-variant": "",
            "items-1-quantity": "",
            "items-1-unit_price": "",
            "items-1-discount_percent": "",
        },
    )
    assert resp_inv.status_code == 302
    assert Invoice.objects.filter(customer=c_mh).count() == 1

    resp_so = client.post(
        reverse("sales:order_create"),
        {
            "customer": c_mh.pk,
            "location": loc.pk,
            "price_list": "WHOLESALE",
            "tax_mode": "EXCLUSIVE",
            "order_date": timezone.localdate().isoformat(),
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-variant": var.pk,
            "items-0-quantity": "2",
            "items-0-unit_price": "",
            "items-0-discount_percent": "",
            "items-1-variant": "",
            "items-1-quantity": "",
            "items-1-unit_price": "",
            "items-1-discount_percent": "",
        },
    )
    assert resp_so.status_code == 302
    assert SalesOrder.objects.filter(customer=c_mh).count() == 1


def test_batch2_spoofed_xff_login_lockout_and_production_secret_check(db, client, owner_user):
    from django.core.cache import cache

    cache.clear()
    for i in range(10):
        resp = client.post(
            reverse("accounts:login"),
            {"username": owner_user.username, "password": "WrongPassword!"},
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i + 1}",
        )
        assert resp.status_code == 200
    resp_locked = client.post(
        reverse("accounts:login"),
        {"username": owner_user.username, "password": "Password123!"},
        HTTP_X_FORWARDED_FOR="10.0.0.250",
    )
    assert resp_locked.status_code == 429

    with override_settings(DEBUG=False, SECRET_KEY="change-me-to-a-long-random-secret-key-in-production"):
        warnings = check_production_secret_key(None)
        assert any(w.id == "marina_shoes.W001" for w in warnings)


def test_kuwait_kwd_3_decimal_fils_precision_and_zero_tax_default(db, owner_user):
    from django.conf import settings
    from apps.core.models import StoreSettings
    from apps.core.utils import money_round

    assert settings.DEFAULT_CURRENCY_CODE == "KWD"
    assert settings.DEFAULT_CURRENCY_SYMBOL == "KD"
    assert settings.TIME_ZONE == "Asia/Kuwait"
    assert money_round("12.3456") == Decimal("12.346")

    cat = Category.objects.create(name="Kuwait Slippers")
    brand = Brand.objects.create(name="Marina KW")
    prod = Product.objects.create(name="Salmiya Sandal", category=cat, brand=brand)
    assert prod.tax_rate == Decimal("0.00")

    var = ProductVariant.objects.create(
        product=prod,
        size="42",
        color="Beige",
        sku="KW-SLIP-42",
        cost_price=Decimal("4.250"),
        wholesale_price=Decimal("6.750"),
        retail_price=Decimal("9.950"),
    )
    record_stock_movement(variant=var, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=10, user=owner_user)
    cust = Customer.objects.create(name="Hawalli Shop", customer_type=Customer.CustomerType.WHOLESALE, phone="+96599001122")
    inv = create_invoice(customer=cust, items_data=[{"variant": var, "quantity": 3}], invoice_discount=Decimal("0.125"), user=owner_user)
    # 3 * 6.750 = 20.250 - 0.125 = 20.125 KD, 0% tax
    assert inv.grand_total == Decimal("20.125")
    assert str(inv.grand_total) == "20.125"
    assert StoreSettings.get_settings().currency_symbol == "KD"


def test_ux_refinements_sidebar_dashboard_pos_and_grouped_reports(
    db, client, owner_user, billing_user, storekeeper_user
):
    from apps.purchases.models import Supplier

    cat = Category.objects.create(name="Dress Leather")
    brand = Brand.objects.create(name="Marina Classic")
    active_prod = Product.objects.create(
        name="Derby Executive",
        style_code="MRN-DRB-901",
        category=cat,
        brand=brand,
        status=Product.Status.ACTIVE,
    )
    inactive_prod = Product.objects.create(
        name="Archived Loafers",
        style_code="MRN-OLD-999",
        category=cat,
        brand=brand,
        status=Product.Status.INACTIVE,
    )
    active_var = ProductVariant.objects.create(
        product=active_prod,
        size="43",
        color="Black",
        sku="MRN-901-43-BLK",
        barcode="6990001112223",
        cost_price=Decimal("10.000"),
        wholesale_price=Decimal("15.000"),
        retail_price=Decimal("22.500"),
        reorder_level=3,
    )
    inactive_var = ProductVariant.objects.create(
        product=inactive_prod,
        size="42",
        color="Brown",
        sku="MRN-OLD-42-BRN",
        cost_price=Decimal("8.000"),
        wholesale_price=Decimal("12.000"),
        retail_price=Decimal("18.000"),
    )
    record_stock_movement(
        variant=active_var,
        movement_type=StockMovement.MovementType.OPENING_STOCK,
        quantity=10,
        user=owner_user,
    )
    Supplier.objects.create(name="Kuwait Tannery", phone="+96522334455", opening_balance=Decimal("45.000"))
    cust = Customer.objects.create(
        name="Salmiya Footwear Co", customer_type=Customer.CustomerType.WHOLESALE, phone="+96555667788"
    )
    create_invoice(customer=cust, items_data=[{"variant": active_var, "quantity": 2}], user=owner_user)

    # 1. Owner Dashboard & Collapsible 7-Group Sidebar
    client.force_login(owner_user)
    dash_owner = client.get(reverse("dashboard:home"))
    assert dash_owner.status_code == 200
    owner_html = dash_owner.content.decode()
    assert "Welcome, Marina Shoes" in owner_html
    assert "Welcome, Marina Owner" not in owner_html
    for expected in (
        "Today's Profit:",
        "Yesterday:",
        "out-of-stock",
        "Low-Stock & Out-of-Stock Alerts",
        "Pending Customer Payments",
        "Stock Value by Category",
        "Upcoming Supplier Payments",
        "Administration",
        "POS Billing",
    ):
        assert expected in owner_html
    assert owner_html.count("<details") == 6  # Sales, Inventory, Purchases, Finance, Reports, Administration (+ Dashboard link = 7 groups)

    # 2. POS Search matches style_code and excludes INACTIVE product variants
    pos_page = client.get(reverse("sales:pos"))
    assert pos_page.status_code == 200
    assert "Quick-Add Match" in pos_page.content.decode()

    pos_search = client.get(reverse("sales:pos_search"), {"q": "MRN-DRB-901"})
    assert pos_search.status_code == 200
    assert "MRN-901-43-BLK" in pos_search.content.decode()
    assert 'data-barcode="6990001112223"' in pos_search.content.decode()

    pos_inactive = client.get(reverse("sales:pos_search"), {"q": "MRN-OLD-999"})
    assert pos_inactive.status_code == 200
    assert "MRN-OLD-42-BRN" not in pos_inactive.content.decode()

    # 3. Reports Index Grouped into 4 Sections
    rep_owner = client.get(reverse("reports:index"))
    assert rep_owner.status_code == 200
    rep_html = rep_owner.content.decode()
    for heading in ("Sales Reports", "Inventory Reports", "Purchase Reports", "Financial Reports"):
        assert heading in rep_html

    # 4. Billing Staff privacy guard on new Dashboard widgets
    client.force_login(billing_user)
    dash_billing = client.get(reverse("dashboard:home"))
    assert dash_billing.status_code == 200
    billing_html = dash_billing.content.decode()
    assert "Pending Customer Payments" in billing_html
    for restricted in (
        "Today's Profit:",
        "Gross Profit Estimate",
        "Upcoming Supplier Payments",
        "Stock Value by Category",
        "Supplier Payables",
        "Actual Cash & Bank",
    ):
        assert restricted not in billing_html

    # 5. Storekeeper sees Inventory KPIs & Inventory Reports only
    client.force_login(storekeeper_user)
    dash_store = client.get(reverse("dashboard:home"))
    assert dash_store.status_code == 200
    store_html = dash_store.content.decode()
    assert "Stock Value by Category" in store_html
    assert "Low-Stock & Out-of-Stock Alerts" in store_html
    assert "Today's Profit:" not in store_html
    assert "Upcoming Supplier Payments" not in store_html

    rep_store = client.get(reverse("reports:index"))
    assert rep_store.status_code == 200
    rep_store_html = rep_store.content.decode()
    assert "Inventory Reports" in rep_store_html
    assert "Financial Reports" not in rep_store_html


def test_enterprise_healthz_api_rbac_and_db_backup_restore(
    db, tmp_path, client, api_client, owner_user, billing_user, storekeeper_user
):
    import sqlite3
    from django.core.management import call_command

    # 1. Health endpoint works unauthenticated and checks DB connectivity without leaking secrets
    health_resp = client.get(reverse("healthz"))
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok", "database": "ok"}

    # 2. API RBAC across roles & anonymous users
    restricted_endpoints = [
        reverse("api-users-list"),
        reverse("api-audit-list"),
        reverse("api-finance-summary"),
        reverse("api-supplier-payments-list"),
        reverse("api-expenses-list"),
    ]
    for url in restricted_endpoints:
        anon_r = api_client.get(url)
        assert anon_r.status_code in (401, 403), f"Expected 401/403 for anon on {url}, got {anon_r.status_code}"

    api_client.force_authenticate(user=billing_user)
    for url in restricted_endpoints:
        bil_r = api_client.get(url)
        assert bil_r.status_code == 403, f"Expected 403 for BILLING_STAFF on {url}, got {bil_r.status_code}"

    api_client.force_authenticate(user=storekeeper_user)
    for url in restricted_endpoints:
        st_r = api_client.get(url)
        assert st_r.status_code == 403, f"Expected 403 for STOREKEEPER on {url}, got {st_r.status_code}"

    api_client.force_authenticate(user=owner_user)
    for url in restricted_endpoints:
        own_r = api_client.get(url)
        assert own_r.status_code == 200, f"Expected 200 for OWNER on {url}, got {own_r.status_code}"

    # 3. Real SQLite online backup + integrity check + restore recovery test on temporary database
    src_db = tmp_path / "pilot_src.sqlite3"
    backup_file = tmp_path / "pilot_backup.sqlite3"
    restored_db = tmp_path / "pilot_restored.sqlite3"

    with sqlite3.connect(src_db) as conn:
        conn.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE core_storesettings (id INTEGER PRIMARY KEY, store_name TEXT)")
        conn.execute("INSERT INTO core_storesettings (store_name) VALUES ('MARINA SHOES KW')")
        conn.commit()

    with override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(src_db)}}):
        call_command("db_backup", output=str(backup_file))
        assert backup_file.exists()
        call_command("db_restore", str(backup_file), verify_only=True)
        call_command("db_restore", str(backup_file), target=str(restored_db))

    with sqlite3.connect(restored_db) as rconn:
        check = rconn.execute("PRAGMA integrity_check").fetchone()
        row = rconn.execute("SELECT store_name FROM core_storesettings WHERE id = 1").fetchone()
        assert check == ("ok",)
        assert row == ("MARINA SHOES KW",)


def test_bilingual_english_arabic_switching_and_rtl(db, client, owner_user):
    # 1. Login page supports language switching pre-login
    login_en = client.get(reverse("accounts:login"))
    assert login_en.status_code == 200
    assert 'dir="ltr"' in login_en.content.decode()
    assert "Sign In" in login_en.content.decode()

    resp_ar = client.post(reverse("set_language"), {"language": "ar", "next": reverse("accounts:login")}, follow=True)
    assert resp_ar.status_code == 200
    ar_login_html = resp_ar.content.decode()
    assert 'lang="ar" dir="rtl"' in ar_login_html
    assert "تسجيل الدخول" in ar_login_html
    assert "اسم المستخدم" in ar_login_html

    # 2. Authenticated Owner views translate cleanly in Arabic (Dashboard, POS, Reports)
    client.force_login(owner_user)
    dash_ar = client.get(reverse("dashboard:home"))
    assert dash_ar.status_code == 200
    dash_ar_html = dash_ar.content.decode()
    assert 'lang="ar" dir="rtl"' in dash_ar_html
    assert "مرحباً،" in dash_ar_html
    assert "Marina Shoes" in dash_ar_html
    assert "لوحة التحكم" in dash_ar_html
    assert "نقطة البيع (الكاشير)" in dash_ar_html

    pos_ar = client.get(reverse("sales:pos"))
    assert pos_ar.status_code == 200
    assert "نقطة البيع السريعة" in pos_ar.content.decode()

    rep_ar = client.get(reverse("reports:index"))
    assert rep_ar.status_code == 200
    rep_ar_html = rep_ar.content.decode()
    for heading in ("تقارير المبيعات", "تقارير المخزون", "تقارير المشتريات", "التقارير المالية"):
        assert heading in rep_ar_html

    # 3. Switching back to English restores LTR and Marina Shoes dashboard welcome
    resp_en = client.post(reverse("set_language"), {"language": "en", "next": reverse("dashboard:home")}, follow=True)
    assert resp_en.status_code == 200
    dash_en_html = resp_en.content.decode()
    assert 'lang="en" dir="ltr"' in dash_en_html
    assert "Welcome," in dash_en_html
    assert "Marina Shoes" in dash_en_html
    assert "Marina Owner" not in dash_en_html.split("Welcome,")[1].split("</h1>")[0]
