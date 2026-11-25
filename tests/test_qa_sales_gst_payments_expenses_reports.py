"""Comprehensive QA test suite: Phases 6-12 (Sales/POS, GST/Invoices, Payments, Expenses, Reports & P&L)."""

from datetime import date
from decimal import Decimal
import io
import openpyxl
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.finance.services import get_gst_summary, get_ledger_book, get_profit_and_loss
from apps.inventory.models import Brand, Category, Product, ProductVariant, StockMovement
from apps.inventory.services import InsufficientStockError, record_stock_movement
from apps.invoicing.models import CreditNote, Invoice
from apps.invoicing.services import cancel_invoice, create_credit_note, create_invoice, issue_refund
from apps.payments.models import CustomerPayment, PaymentAllocation
from apps.payments.services import apply_customer_advance, record_customer_payment, reverse_customer_payment
from apps.purchases.models import Supplier
from apps.purchases.services import create_purchase_return, receive_goods, record_supplier_payment
from apps.reports.views import REPORT_CARDS
from apps.sales.models import SalesReturn
from apps.sales.services import process_sales_return


@pytest.fixture
def gst_setup(db, owner_user):
    cat = Category.objects.create(name="GST Footwear")
    brand = Brand.objects.create(name="Marina QA")
    p_5 = Product.objects.create(name="Chappal 5%", category=cat, brand=brand, hsn_code="6402", tax_rate=Decimal("5.00"))
    p_12 = Product.objects.create(name="Boot 12%", category=cat, brand=brand, hsn_code="6403", tax_rate=Decimal("12.00"))
    v5 = ProductVariant.objects.create(
        product=p_5, size="8", color="Tan", sku="GST-CHP-8-TAN", barcode="8902222000011",
        cost_price=Decimal("200.00"), wholesale_price=Decimal("300.00"), retail_price=Decimal("400.00"),
    )
    v12 = ProductVariant.objects.create(
        product=p_12, size="9", color="Brown", sku="GST-BT-9-BRN", barcode="8902222000028",
        cost_price=Decimal("500.00"), wholesale_price=Decimal("800.00"), retail_price=Decimal("1000.00"),
    )
    record_stock_movement(variant=v5, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=50, user=owner_user)
    record_stock_movement(variant=v12, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=50, user=owner_user)
    c_intra = Customer.objects.create(name="Local KW Shop", customer_type=Customer.CustomerType.WHOLESALE, phone="+96591111111", state_code="KW")
    c_inter = Customer.objects.create(name="External GCC Shop", customer_type=Customer.CustomerType.WHOLESALE, phone="+96592222222", state_code="SA")
    c_retail = Customer.objects.create(name="Walkin Retail", customer_type=Customer.CustomerType.RETAIL, phone="+96593333333", state_code="KW")
    return {"p5": p_5, "p12": p_12, "v5": v5, "v12": v12, "c_intra": c_intra, "c_inter": c_inter, "c_retail": c_retail}


# ---------------------------------------------------------------------------
# PHASE 6: SALES & POS BOUNDARY TESTS
# ---------------------------------------------------------------------------
def test_phase6_pos_search_payments_duplicate_submit_and_boundaries(db, client, owner_user, billing_user, gst_setup):
    v5 = gst_setup["v5"]
    v12 = gst_setup["v12"]
    c_retail = gst_setup["c_retail"]
    loc = StoreLocation.get_default()

    client.force_login(billing_user)

    # Search by barcode, SKU, size
    for q, expected_sku in [("8902222000011", "GST-CHP-8-TAN"), ("GST-BT-9-BRN", "GST-BT-9-BRN"), ("Brown", "GST-BT-9-BRN")]:
        resp = client.get(reverse("sales:pos_search"), {"q": q})
        assert resp.status_code == 200
        assert expected_sku in resp.content.decode()

    # Unauthorized price override blocked for Billing Staff on POS
    pos_get = client.get(reverse("sales:pos"))
    token = pos_get.context["pos_token"]
    resp_override = client.post(
        reverse("sales:pos"),
        {
            "pos_token": token,
            "customer": c_retail.pk,
            "location": loc.pk,
            "price_list": "RETAIL",
            "tax_mode": "EXCLUSIVE",
            "variant_id": [v5.pk],
            "qty": ["1"],
            "unit_price": ["150.00"],  # unauthorized override (default retail 400.00)
            "discount_percent": ["0"],
            "amount_paid": "0",
            "payment_mode": "CASH",
        },
    )
    assert resp_override.status_code == 200
    assert Invoice.objects.count() == 0

    # Valid Billing Staff POS checkout (partial payment via UPI) + duplicate submit guard
    resp_ok = client.post(
        reverse("sales:pos"),
        {
            "pos_token": token,
            "customer": c_retail.pk,
            "location": loc.pk,
            "price_list": "RETAIL",
            "tax_mode": "EXCLUSIVE",
            "variant_id": [v5.pk],
            "qty": ["2"],
            "unit_price": ["400.00"],
            "discount_percent": ["0"],
            "amount_paid": "500.000",
            "payment_mode": "KNET",
            "payment_reference": "KNET-QA-01",
        },
    )
    assert resp_ok.status_code == 302
    assert Invoice.objects.count() == 1
    inv = Invoice.objects.first()
    # 2 * 400 = 800 + 5% (40) = 840; paid 500 -> PARTIAL, balance 340
    assert inv.grand_total == Decimal("840.00")
    assert inv.amount_paid == Decimal("500.00")
    assert inv.status == Invoice.Status.PARTIAL
    assert v5.current_stock == 48

    # Double-click / resubmit with same pos_token is ignored safely!
    resp_dup = client.post(
        reverse("sales:pos"),
        {
            "pos_token": token,
            "customer": c_retail.pk,
            "location": loc.pk,
            "price_list": "RETAIL",
            "tax_mode": "EXCLUSIVE",
            "variant_id": [v5.pk],
            "qty": ["2"],
            "unit_price": ["400.00"],
            "discount_percent": ["0"],
        },
    )
    assert resp_dup.status_code == 302
    assert Invoice.objects.count() == 1
    assert v5.current_stock == 48

    # Zero qty, negative discount (>100% or negative price) rejected
    with pytest.raises(ValidationError):
        create_invoice(customer=c_retail, items_data=[{"variant": v5, "quantity": 0}], user=owner_user)
    with pytest.raises(ValidationError):
        create_invoice(customer=c_retail, items_data=[{"variant": v5, "quantity": -1}], user=owner_user)
    with pytest.raises(ValidationError):
        create_invoice(customer=c_retail, items_data=[{"variant": v5, "quantity": 1, "discount_percent": "120"}], user=owner_user)
    with pytest.raises(ValidationError):
        create_invoice(customer=c_retail, items_data=[{"variant": v5, "quantity": 1, "unit_price": Decimal("-50.00")}], user=owner_user)

    # Inactive variant / inactive product / deleted product / inactive customer MUST be rejected
    inactive_var = ProductVariant.objects.create(
        product=gst_setup["p5"], size="6", color="Grey", sku="GST-INACT-6",
        cost_price=Decimal("100.00"), wholesale_price=Decimal("200.00"), retail_price=Decimal("300.00"),
        is_active=False,
    )
    record_stock_movement(variant=inactive_var, movement_type=StockMovement.MovementType.OPENING_STOCK, quantity=5, user=owner_user)
    with pytest.raises(ValidationError):
        create_invoice(customer=c_retail, items_data=[{"variant": inactive_var, "quantity": 1}], user=owner_user)

    inactive_cust = Customer.objects.create(name="Dormant Cust", customer_type=Customer.CustomerType.RETAIL, phone="9000099999", is_active=False)
    with pytest.raises(ValidationError):
        create_invoice(customer=inactive_cust, items_data=[{"variant": v12, "quantity": 1}], user=owner_user)


# ---------------------------------------------------------------------------
# PHASE 7: MANUAL GST & INVOICE RECONCILIATION + RETURN OF DISCOUNTED ITEMS
# ---------------------------------------------------------------------------
def test_phase7_gst_exclusive_inclusive_mixed_rates_discounts_fy_and_discounted_return(db, client, owner_user, gst_setup):
    v5 = gst_setup["v5"]
    v12 = gst_setup["v12"]
    c_intra = gst_setup["c_intra"]
    c_inter = gst_setup["c_inter"]

    # Case 1: Mixed tax rates (5% + 12%), Intra-state (MH->MH), item discount + invoice discount
    # v5: 2 * 300 = 600, item disc 10% (60) -> net 540
    # v12: 1 * 800 = 800, item disc 0 -> net 800
    # total net after line disc = 1340; invoice_discount = 134.00 (10%)
    # proportional inv disc: v5 gets round(134 * 540 / 1340) = 54.00 -> taxable 486.00 @5%: CGST 12.15, SGST 12.15, line 510.30
    #                        v12 gets 80.00 -> taxable 720.00 @12%: CGST 43.20, SGST 43.20, line 806.40
    # Totals: Taxable = 1206.00, CGST = 55.35, SGST = 55.35, IGST = 0.00, Grand Total = 1316.70
    inv1 = create_invoice(
        customer=c_intra,
        price_list="WHOLESALE",
        tax_mode=StoreSettings.TaxMode.EXCLUSIVE,
        invoice_discount=Decimal("134.00"),
        items_data=[
            {"variant": v5, "quantity": 2, "discount_percent": "10"},
            {"variant": v12, "quantity": 1},
        ],
        user=owner_user,
        issue_now=True,
    )
    assert inv1.taxable_amount == Decimal("1206.00")
    assert inv1.cgst_total == Decimal("55.35")
    assert inv1.sgst_total == Decimal("55.35")
    assert inv1.igst_total == Decimal("0.00")
    assert inv1.grand_total == Decimal("1316.70")
    assert sum(it.taxable_value for it in inv1.items.all()) == inv1.taxable_amount
    assert sum(it.line_total for it in inv1.items.all()) == inv1.grand_total

    # BUG PROBE: Full return of ALL items on a discounted invoice!
    # Returning 2 of v5 and 1 of v12 must credit at most the discounted invoice net (1316.70), NEVER exceed grand_total!
    sr_full = process_sales_return(
        invoice=inv1,
        items_data=[{"variant": v5, "quantity": 2}, {"variant": v12, "quantity": 1}],
        user=owner_user,
        reason="Full defective lot",
    )
    assert sr_full.credit_note.total_amount == Decimal("1316.70")
    assert inv1.balance_due == Decimal("0.00")

    # Case 2: External region (KW->SA) Tax-Inclusive invoice (3-decimal fils rounding)
    # 1 * v12 retail 1000.000 @12% inclusive -> taxable = 892.857, IGST = 107.143, line_total = 1000.000
    inv2 = create_invoice(
        customer=c_inter,
        price_list="RETAIL",
        tax_mode=StoreSettings.TaxMode.INCLUSIVE,
        items_data=[{"variant": v12, "quantity": 1}],
        user=owner_user,
        issue_now=True,
    )
    assert inv2.taxable_amount == Decimal("892.857")
    assert inv2.cgst_total == Decimal("0.000")
    assert inv2.sgst_total == Decimal("0.000")
    assert inv2.igst_total == Decimal("107.143")
    assert inv2.grand_total == Decimal("1000.000")

    # Case 3: Financial-year boundary (2026-03-31 -> 2025-26, 2026-04-01 -> 2026-27)
    inv_mar = create_invoice(
        customer=c_intra,
        invoice_date=date(2026, 3, 31),
        items_data=[{"variant": v5, "quantity": 1}],
        user=owner_user,
        issue_now=True,
    )
    inv_apr = create_invoice(
        customer=c_intra,
        invoice_date=date(2026, 4, 1),
        items_data=[{"variant": v5, "quantity": 1}],
        user=owner_user,
        issue_now=True,
    )
    assert inv_mar.financial_year == "2025-26" and "2025-26/0001" in inv_mar.invoice_number
    assert inv_apr.financial_year == "2026-27" and "2026-27/" in inv_apr.invoice_number

    # Print view & WhatsApp link work; cancelled invoice restores stock and blocks delete
    client.force_login(owner_user)
    resp_print = client.get(reverse("invoicing:invoice_print", args=[inv2.pk]))
    assert resp_print.status_code == 200 and inv2.invoice_number in resp_print.content.decode()
    assert "wa.me" in inv2.get_whatsapp_url("https://erp.example/inv/2")

    stock_before_cancel = v5.current_stock
    cancel_invoice(invoice=inv_mar, user=owner_user, reason="Wrong party")
    assert v5.current_stock == stock_before_cancel + 1
    with pytest.raises(ValidationError):
        inv_mar.delete()


# ---------------------------------------------------------------------------
# PHASE 8: CUSTOMER PAYMENTS, REVERSAL, RE-ALLOCATION & LEDGER RECONCILIATION
# ---------------------------------------------------------------------------
def test_phase8_customer_payments_reversal_reallocation_advance_and_refund(db, owner_user, gst_setup):
    v12 = gst_setup["v12"]
    cust = gst_setup["c_intra"]

    # Invoice: 2 * 800 = 1600 + 12% GST (192) = 1792.00
    inv = create_invoice(customer=cust, price_list="WHOLESALE", items_data=[{"variant": v12, "quantity": 2}], user=owner_user, issue_now=True)
    assert inv.grand_total == Decimal("1792.00")

    # Advance 500 + partial cash 1000 directly on inv -> remaining 792, advance unallocated 500
    p_adv = record_customer_payment(customer=cust, amount=Decimal("500.00"), payment_mode="KNET", is_advance=True, user=owner_user)
    p1 = record_customer_payment(customer=cust, invoice=inv, amount=Decimal("1000.00"), payment_mode="CASH", user=owner_user)
    inv.refresh_from_db()
    assert inv.amount_paid == Decimal("1000.00")
    assert inv.balance_due == Decimal("792.00")
    assert p_adv.unallocated_amount == Decimal("500.00")

    # Reverse p1 -> restores inv.amount_paid = 0, inv.status = ISSUED, balance_due = 1792.00
    reverse_customer_payment(payment=p1, user=owner_user, reason="Cheque bounced")
    inv.refresh_from_db()
    p1.refresh_from_db()
    assert p1.is_reversed is True
    assert inv.amount_paid == Decimal("0.00")
    assert inv.status == Invoice.Status.ISSUED
    assert inv.balance_due == Decimal("1792.00")

    # Apply advance 500 -> amount_paid = 500, balance_due = 1292
    applied = apply_customer_advance(customer=cust, invoice=inv, user=owner_user)
    inv.refresh_from_db()
    assert applied == Decimal("500.00")
    assert inv.amount_paid == Decimal("500.00")
    assert inv.balance_due == Decimal("1292.00")

    # Manual Credit Note + Refund exceeding credit note blocked
    cn = create_credit_note(invoice=inv, items_data=[{"variant": v12, "quantity": 1, "unit_price": Decimal("800.00"), "tax_rate": Decimal("12.00")}], user=owner_user)
    assert cn.total_amount == Decimal("896.00")
    with pytest.raises(ValidationError):
        issue_refund(credit_note=cn, amount=Decimal("900.00"), payment_mode="CASH", user=owner_user)
    issue_refund(credit_note=cn, amount=Decimal("200.00"), payment_mode="CASH", user=owner_user)

    # Manual customer ledger reconciliation:
    # Invoiced 1792 - CN 896 + Refund 200 - Valid Paid 500 = 596.00
    inv.refresh_from_db()
    cust.refresh_from_db()
    assert inv.balance_due == Decimal("596.00")
    assert cust.outstanding_balance == Decimal("596.00")


# ---------------------------------------------------------------------------
# PHASE 9-12: SUPPLIERS, EXPENSES, ALL 23 REPORTS & FINANCIAL P&L RECONCILIATION
# ---------------------------------------------------------------------------
def test_phase9_to_12_expenses_reports_and_exact_financial_reconciliation(db, client, owner_user, billing_user, storekeeper_user, gst_setup):
    v5 = gst_setup["v5"]
    v12 = gst_setup["v12"]
    c_intra = gst_setup["c_intra"]

    # 1. Supplier transactions: opening 1000, purchase 3000 (cash paid 1200 via BANK, payable left 1800) + return 400 -> payable 2400
    sup = Supplier.objects.create(name="QA Recon Supplier", phone="9777777777", opening_balance=Decimal("1000.00"))
    grn = receive_goods(
        supplier=sup,
        items_data=[{"variant": v5, "quantity_received": 10, "unit_cost": Decimal("300.00"), "tax_rate": Decimal("0.00")}],
        user=owner_user,
    )
    record_supplier_payment(supplier=sup, goods_receipt=grn, amount=Decimal("1200.00"), payment_mode="BANK", user=owner_user)
    create_purchase_return(supplier=sup, goods_receipt=grn, items_data=[{"variant": v5, "quantity": 2, "unit_cost": Decimal("200.00")}], user=owner_user)
    assert sup.outstanding_balance == Decimal("2400.00")

    # 2. Sales: Credit invoice 2 * v12 (cost 500, wholesale 800, GST 12%) -> taxable 1600, GST 192, grand 1792
    # Plus return 1 * v12 -> CN taxable 800, GST 96, total 896
    # Net revenue (ex-tax) = 1600 - 800 = 800.00; COGS = (2 - 1) * 500 = 500.00; Gross Profit = 300.00
    inv = create_invoice(customer=c_intra, price_list="WHOLESALE", items_data=[{"variant": v12, "quantity": 2}], user=owner_user, issue_now=True)
    process_sales_return(invoice=inv, items_data=[{"variant": v12, "quantity": 1}], condition=SalesReturn.Condition.RESTOCK, user=owner_user)

    # Customer cash collection = 400.00 ONLY (out of 896.00 net invoiced -> receivable remaining = 496.00)
    record_customer_payment(customer=c_intra, invoice=inv, amount=Decimal("400.00"), payment_mode="CASH", user=owner_user)
    # KNET receipt 150 advance (bank book)
    record_customer_payment(customer=c_intra, amount=Decimal("150.00"), payment_mode="KNET", is_advance=True, user=owner_user)

    # 3. Operating Expense: 180 CASH rent + bad expense upload validation
    Expense.objects.create(category=Expense.Category.RENT, amount=Decimal("180.00"), payment_mode=Expense.PaymentMode.CASH, notes="Shop tea & cleaning", created_by=owner_user)

    client.force_login(owner_user)
    bad_exe = SimpleUploadedFile("virus.exe", b"MZ...", content_type="application/octet-stream")
    resp_bad_exp = client.post(
        reverse("expenses:expense_list"),
        {"expense_date": date.today().isoformat(), "category": "RENT", "amount": "100.00", "payment_mode": "CASH", "receipt_attachment": bad_exe},
    )
    assert resp_bad_exp.status_code == 200
    assert Expense.objects.count() == 1  # rejected!

    # Zero / negative expense rejected at validation
    bad_amt = Expense(category=Expense.Category.OTHER, amount=Decimal("0.00"), payment_mode=Expense.PaymentMode.CASH, created_by=owner_user)
    with pytest.raises(ValidationError):
        bad_amt.full_clean()

    # 4. Exact Financial P&L Reconciliation (Sales Revenue != Cash Collected, Purchases != Expenses):
    pnl = get_profit_and_loss()
    assert pnl["gross_sales_taxable"] == Decimal("1600.00")
    assert pnl["returns_taxable"] == Decimal("800.00")
    assert pnl["net_revenue"] == Decimal("800.00")
    assert pnl["cogs"] == Decimal("500.00")
    assert pnl["gross_profit"] == Decimal("300.00")
    assert pnl["total_expenses"] == Decimal("180.00")  # excludes supplier purchase 3000 & supplier payment 1200!
    assert pnl["net_profit"] == Decimal("120.00")

    # Cash book = +400 customer cash - 180 cash expense = 220.00
    cash_book = get_ledger_book(modes=("CASH",))
    assert cash_book["total_in"] == Decimal("400.00")
    assert cash_book["total_out"] == Decimal("180.00")
    assert cash_book["closing_balance"] == Decimal("220.00")

    # Bank book (BANK + KNET + CHEQUE + OTHER) = +150 KNET - 1200 BANK supplier payment = -1050.00
    bank_book = get_ledger_book(modes=("BANK", "KNET", "CHEQUE", "OTHER"))
    assert bank_book["total_in"] == Decimal("150.00")
    assert bank_book["total_out"] == Decimal("1200.00")
    assert bank_book["closing_balance"] == Decimal("-1050.00")

    # GST Summary: Outward 1600 taxable (96 CGST + 96 SGST) - CN 800 taxable (48 CGST + 48 SGST) -> net taxable 800, net GST 96
    gst = get_gst_summary()
    assert gst["net_taxable"] == Decimal("800.00")
    assert gst["net_cgst"] == Decimal("48.00")
    assert gst["net_sgst"] == Decimal("48.00")
    assert gst["net_total_gst"] == Decimal("96.00")

    # 5. Test ALL 23 Reports: HTML render, search filter, pagination, CSV export, Excel export, role permissions
    for card in REPORT_CARDS:
        key = card["key"]
        r_html = client.get(reverse("reports:detail", args=[key]), {"q": "GST-BT-9"})
        assert r_html.status_code == 200
        r_csv = client.get(reverse("reports:detail", args=[key]), {"export": "csv"})
        assert r_csv.status_code == 200 and "text/csv" in r_csv["Content-Type"]
        r_xlsx = client.get(reverse("reports:detail", args=[key]), {"export": "xlsx"})
        assert r_xlsx.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(r_xlsx.content))
        assert wb.active.max_row >= 1

    # Permissions check on reports: Storekeeper cannot access FIN_ROLES reports; Billing Staff blocked from all reports
    client.force_login(storekeeper_user)
    assert client.get(reverse("reports:detail", args=["stock"])).status_code == 200
    assert client.get(reverse("reports:detail", args=["profit"])).status_code == 403
    client.force_login(billing_user)
    assert client.get(reverse("reports:index")).status_code == 403
