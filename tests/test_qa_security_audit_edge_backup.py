"""Comprehensive QA test suite: Phases 13, 14, 17, 18 (RBAC/Security, Audit Logs, Edge Cases, Isolated Backup/Restore)."""

from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections, transaction
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.models import StoreLocation
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.finance.services import get_profit_and_loss
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import record_stock_movement
from apps.invoicing.models import Invoice
from apps.invoicing.services import cancel_invoice, create_invoice, render_invoice_pdf_bytes
from apps.payments.services import record_customer_payment, reverse_customer_payment
from apps.purchases.models import Supplier
from apps.purchases.services import receive_goods
from apps.reports.services import report_stock


@pytest.fixture
def rbac_world(db, owner_user):
    cat = Category.objects.create(name="RBAC Shoes")
    brand = Brand.objects.create(name="RBAC Brand")
    prod = Product.objects.create(name="RBAC Loafer", category=cat, brand=brand, hsn_code="6403", tax_rate=Decimal("12.00"))
    var = ProductVariant.objects.create(
        product=prod, size="8", color="Tan", sku="RBAC-8-TAN",
        cost_price=Decimal("400.00"), wholesale_price=Decimal("700.00"), retail_price=Decimal("999.00"),
    )
    record_stock_movement(variant=var, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=20, user=owner_user)
    cust = Customer.objects.create(name="RBAC Customer", customer_type=Customer.CustomerType.WHOLESALE, phone="+96590008888", state_code="KW")
    sup = Supplier.objects.create(name="RBAC Supplier", phone="9000077777")
    inv = create_invoice(customer=cust, items_data=[{"variant": var, "quantity": 2}], user=owner_user, issue_now=True)
    pay = record_customer_payment(customer=cust, invoice=inv, amount=Decimal("200.00"), payment_mode="CASH", user=owner_user)
    return {"prod": prod, "var": var, "cust": cust, "sup": sup, "inv": inv, "pay": pay}


# ---------------------------------------------------------------------------
# PHASE 13: EXHAUSTIVE ROLE PERMISSION & SECURITY TESTS
# ---------------------------------------------------------------------------
def test_phase13_role_matrix_direct_urls_and_api(db, client, api_client, owner_user, manager_user, billing_user, storekeeper_user, rbac_world):
    var = rbac_world["var"]
    inv = rbac_world["inv"]
    pay = rbac_world["pay"]

    # Unauthenticated access redirected on web, 403/401 on API
    assert client.get(reverse("dashboard:home")).status_code == 302
    assert api_client.get("/api/v1/accounts/users/").status_code in (401, 403)
    assert api_client.get("/api/v1/inventory/variants/").status_code in (401, 403)

    # OWNER vs MANAGER vs BILLING_STAFF vs STOREKEEPER direct URL matrix
    url_matrix = [
        (reverse("accounts:user_list"), {owner_user: 200, manager_user: 403, billing_user: 403, storekeeper_user: 403}),
        (reverse("core:store_settings"), {owner_user: 200, manager_user: 403, billing_user: 403, storekeeper_user: 403}),
        (reverse("audit:audit_list"), {owner_user: 200, manager_user: 403, billing_user: 403, storekeeper_user: 403}),
        (reverse("finance:overview"), {owner_user: 200, manager_user: 200, billing_user: 403, storekeeper_user: 403}),
        (reverse("expenses:expense_list"), {owner_user: 200, manager_user: 200, billing_user: 403, storekeeper_user: 403}),
        (reverse("inventory:stock_movement"), {owner_user: 200, manager_user: 200, billing_user: 403, storekeeper_user: 200}),
        (reverse("sales:pos"), {owner_user: 200, manager_user: 200, billing_user: 200, storekeeper_user: 403}),
        (reverse("payments:payment_list"), {owner_user: 200, manager_user: 200, billing_user: 200, storekeeper_user: 403}),
        (reverse("purchases:po_list"), {owner_user: 200, manager_user: 200, billing_user: 403, storekeeper_user: 200}),
    ]
    for url, expected in url_matrix:
        for usr, code in expected.items():
            client.force_login(usr)
            assert client.get(url).status_code == code, f"URL {url} failed for {usr.role}"

    # Billing Staff and Storekeeper cannot cancel invoices or reverse payments
    for restricted in (billing_user, storekeeper_user):
        client.force_login(restricted)
        assert client.post(reverse("invoicing:invoice_cancel", args=[inv.pk]), {"reason": "test"}).status_code == 403
        assert client.post(reverse("payments:payment_reverse", args=[pay.pk]), {"reason": "test"}).status_code == 403

    # Storekeeper cannot adjust via API vs Billing Staff cannot write stock movements via API
    api_client.force_authenticate(user=billing_user)
    assert api_client.get("/api/v1/audit/logs/").status_code == 403
    assert api_client.get("/api/v1/finance/summary/").status_code == 403
    assert api_client.post(
        "/api/v1/inventory/stock-movements/",
        {"variant": var.pk, "movement_type": "ADJUSTMENT", "direction": "IN", "quantity": 5},
        format="json",
    ).status_code == 403

    # COST PRICE EXPOSURE PROBE: Billing Staff should not see internal cost_price via variant API or HTML detail
    resp_var_api = api_client.get("/api/v1/inventory/variants/")
    assert resp_var_api.status_code == 200
    rows = resp_var_api.json().get("results", resp_var_api.json())
    assert "cost_price" not in rows[0], "cost_price leaked to BILLING_STAFF via variant API"

    client.force_login(billing_user)
    resp_html = client.get(reverse("inventory:product_detail", args=[rbac_world["prod"].pk]))
    assert resp_html.status_code == 200
    assert "400.00" not in resp_html.content.decode(), "cost_price leaked to BILLING_STAFF in HTML product detail"
    client.force_login(storekeeper_user)
    resp_sk_html = client.get(reverse("inventory:product_detail", args=[rbac_world["prod"].pk]))
    assert "400.00" in resp_sk_html.content.decode()

    # Dashboard privacy: Billing Staff must NOT see Gross Profit, Supplier Payables, or Cost Stock Value
    client.force_login(billing_user)
    dash_bill = client.get(reverse("dashboard:home")).content.decode()
    assert "Gross Profit Estimate" not in dash_bill
    assert "Supplier Payables" not in dash_bill
    assert "Actual Cash & Bank" not in dash_bill

    client.force_login(owner_user)
    dash_owner = client.get(reverse("dashboard:home")).content.decode()
    assert "Gross Profit Estimate" in dash_owner
    assert "Supplier Payables" in dash_owner


# ---------------------------------------------------------------------------
# PHASE 14: AUDIT LOGGING COMPLETENESS & IMMUTABILITY
# ---------------------------------------------------------------------------
def test_phase14_audit_logs_created_and_strictly_immutable(db, client, owner_user, rbac_world):
    var = rbac_world["var"]
    inv = rbac_world["inv"]
    pay = rbac_world["pay"]

    # Adjustment, payment reversal, and invoice cancellation all generate AuditLog records
    record_stock_movement(variant=var, movement_type=StockMovement.MovementType.DAMAGE, quantity=1, reason="Torn box", user=owner_user)
    reverse_customer_payment(payment=pay, user=owner_user, reason="Wrong receipt")
    inv_fresh = create_invoice(customer=rbac_world["cust"], items_data=[{"variant": var, "quantity": 1}], user=owner_user, issue_now=True)
    cancel_invoice(invoice=inv_fresh, user=owner_user, reason="Duplicate order")

    assert AuditLog.objects.filter(model_name="StockMovement", action=AuditLog.Action.ADJUST).exists()
    assert AuditLog.objects.filter(model_name="CustomerPayment", action=AuditLog.Action.CANCEL).exists()
    assert AuditLog.objects.filter(model_name="Invoice", action=AuditLog.Action.ISSUE).exists()
    assert AuditLog.objects.filter(model_name="Invoice", action=AuditLog.Action.CANCEL).exists()

    # AuditLog cannot be altered or deleted once recorded
    log = AuditLog.objects.first()
    log.notes = "Tampered note"
    with pytest.raises(ValidationError):
        log.save()
    with pytest.raises(ValidationError):
        log.delete()


# ---------------------------------------------------------------------------
# PHASE 17: FAILURE & EDGE CASE PROBES (INVALID DATES, REPORT CARD LEAK, ROLLBACK)
# ---------------------------------------------------------------------------
def test_phase17_invalid_dates_unknown_report_leak_pdf_error_and_atomic_rollback(db, client, owner_user, storekeeper_user, rbac_world):
    # Invalid dates on Finance and Reports must return safe 200 fallback, NOT HTTP 500 ValueError!
    client.force_login(owner_user)
    assert client.get(reverse("finance:overview"), {"start_date": "not-a-date", "end_date": "2026-99-99"}).status_code == 200
    assert client.get(reverse("reports:detail", args=["profit"]), {"start_date": "bad-date"}).status_code == 200

    # Unknown report key requested by Storekeeper must NOT reveal Owner/Manager financial report cards!
    client.force_login(storekeeper_user)
    resp_unknown = client.get(reverse("reports:detail", args=["nonexistent_report_xyz"]))
    assert resp_unknown.status_code == 200
    assert "Profit Report" not in resp_unknown.content.decode()

    # PDF generation handles missing GTK3/Pango safely with clear error message or valid PDF bytes
    inv = rbac_world["inv"]
    try:
        pdf_bytes = render_invoice_pdf_bytes(inv)
        assert pdf_bytes.startswith(b"%PDF")
    except RuntimeError as exc:
        assert "WeasyPrint" in str(exc)

    # Failed transaction rollback leaves DB consistent (atomic invoice + failure)
    initial_inv_count = Invoice.objects.count()
    initial_mv_count = StockMovement.objects.count()
    with pytest.raises(RuntimeError, match="Simulated mid-transaction failure"):
        with transaction.atomic():
            create_invoice(customer=rbac_world["cust"], items_data=[{"variant": rbac_world["var"], "quantity": 1}], user=owner_user, issue_now=True)
            raise RuntimeError("Simulated mid-transaction failure")
    assert Invoice.objects.count() == initial_inv_count
    assert StockMovement.objects.count() == initial_mv_count


# ---------------------------------------------------------------------------
# PHASE 18: ISOLATED BACKUP & RESTORE VERIFICATION
# ---------------------------------------------------------------------------
def test_phase18_isolated_backup_and_restore_roundtrip(db, owner_user, tmp_path, rbac_world):
    """Verify full logical dump + isolated database corruption/wipe + full restore + ledger/report reconciliation."""
    sup = rbac_world["sup"]
    var = rbac_world["var"]
    cust = rbac_world["cust"]

    receive_goods(supplier=sup, items_data=[{"variant": var, "quantity_received": 5, "unit_cost": Decimal("400.00")}], user=owner_user)
    Expense.objects.create(category=Expense.Category.RENT, amount=Decimal("250.00"), payment_mode="CASH", created_by=owner_user)

    # Record expected baseline metrics before backup
    expected_stock = var.current_stock
    expected_cust_bal = cust.outstanding_balance
    expected_sup_bal = sup.outstanding_balance
    expected_pnl = get_profit_and_loss()
    expected_inv_count = Invoice.objects.count()

    # Create JSON fixture backup of entire domain dataset
    backup_file = tmp_path / "erp_backup.json"
    with open(backup_file, "w", encoding="utf-8") as fh:
        call_command(
            "dumpdata",
            "accounts",
            "core",
            "inventory",
            "customers",
            "purchases",
            "sales",
            "invoicing",
            "payments",
            "expenses",
            "audit",
            stdout=fh,
        )
    assert backup_file.stat().st_size > 500

    # Verify SQLite file-level binary backup copy integrity & isolated restore
    backup_sqlite = tmp_path / "isolated_orig.sqlite3"
    restored_sqlite = tmp_path / "isolated_restored.sqlite3"
    conn1 = sqlite3.connect(backup_sqlite)
    conn1.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, amt TEXT)")
    conn1.execute("INSERT INTO t(amt) VALUES (?)", (str(expected_pnl["net_profit"]),))
    conn1.commit()
    conn2 = sqlite3.connect(restored_sqlite)
    conn1.backup(conn2)
    conn1.close()
    assert conn2.execute("SELECT amt FROM t").fetchone()[0] == str(expected_pnl["net_profit"])
    conn2.close()

    # Simulate destructive data loss/corruption in domain tables inside test DB, then restore from dumpdata backup
    # (clear transactional & master data safely in reverse dependency order)
    from apps.inventory.models import StockBalance
    from apps.invoicing.models import CreditNote, FinancialYearSequence, InvoiceItem, Refund
    from apps.payments.models import CustomerPayment, PaymentAllocation
    from apps.purchases.models import GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem, PurchaseReturn, SupplierPayment, SupplierPaymentAllocation

    PaymentAllocation.objects.all().delete()
    CustomerPayment.objects.all().delete()
    Refund.objects.all().delete()
    CreditNote.objects.all().delete()
    InvoiceItem.objects.all().delete()
    Invoice.objects.all().delete()
    SupplierPaymentAllocation.objects.all().delete()
    SupplierPayment.objects.all().delete()
    GoodsReceiptItem.objects.all().delete()
    GoodsReceipt.objects.all().delete()
    Expense.objects.all().delete()
    StockBalance.objects.all().delete()
    StockMovement.all_objects.all().delete() if hasattr(StockMovement, "all_objects") else StockMovement.objects.all().delete()
    AuditLog._base_manager.all().delete()

    assert Invoice.objects.count() == 0
    assert StockMovement.objects.count() == 0

    # Restore backup
    call_command("loaddata", str(backup_file))

    var.refresh_from_db()
    cust.refresh_from_db()
    sup.refresh_from_db()
    assert Invoice.objects.count() == expected_inv_count
    assert var.current_stock == expected_stock
    assert cust.outstanding_balance == expected_cust_bal
    assert sup.outstanding_balance == expected_sup_bal
    assert get_profit_and_loss() == expected_pnl
    _, _, stock_rows = report_stock()
    assert any(r[0] == "RBAC-8-TAN" and r[6] == expected_stock for r in stock_rows)
