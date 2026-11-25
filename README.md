# MARINA SHOES ERP — Wholesale & Retail Footwear Management System (Kuwait)

A production-quality web-based ERP for **MARINA SHOES (Kuwait)**, built with **Django 5**, **Django REST Framework**, **Tailwind CSS**, **HTMX**, **WeasyPrint**, and **PostgreSQL / SQLite**.

---

## Architecture & Capabilities
- **7-Group Role-Focused Navigation**: Collapsible sidebar (`Dashboard`, `Sales`, `Inventory`, `Purchases`, `Finance`, `Reports`, `Administration`) plus mobile 1-tap POS shortcut and keyboard-driven Fast POS (`/sales/pos/`).
- **Multi-Location Product & Variant Inventory**: `Product -> ProductVariant` (style code, size, color, unique SKU & barcode, gender, tariff/item code, product image, tax rate, 3-decimal `KWD` / `KD` cost/wholesale/retail prices, reorder level) + `StoreLocation` + synchronized `StockBalance` updated atomically with immutable `StockMovement` rows.
- **Stock Transfers, Physical Counts & Catalog Import/Export**: Inter-location transfers, variance-reconciling physical counts, and `.csv`/`.xlsx` bulk catalog import/export.
- **Suppliers & Purchasing**: Suppliers (`+965` Kuwait contacts), Purchase Orders (discounts + tax), partial Goods Receipts (including damaged/missing qty tracking), Purchase Returns, and Supplier Payments.
- **Customers, Fast HTMX POS & Returns/Exchanges**: Wholesale & retail customers, credit limits, chronological Customer Ledger, fast USB-barcode/style-code POS screen (`/sales/pos/`) with keyboard shortcuts (`/`, `Enter`, `+`/`-`, `Del`, `Ctrl+Enter`, `Alt+N`), Sales Orders, Sales Returns (`RESTOCK` vs `DAMAGED`), Product Exchanges, and Cash/Bank/KNET Refunds.
- **Kuwait Invoicing (`KWD` / `KD`, `0%` Default Tax)**: 3-decimal fils precision (`0.001`), optional tax-exclusive/inclusive modes, annual auto-numbering (`StoreSettings`-configurable format), immutable issued invoices (cancel or credit note only), Print View, WeasyPrint PDF download, and WhatsApp share links.
- **Payments, Credit Ledger & Finance**: Partial/full payments (`CASH`, `KNET`, `BANK`, `CHEQUE`, `OTHER`), unallocated advances, audited payment reversals, 0–30 / 31–60 / 61–90 / 90+ day Customer & Supplier Aging, 8 Expense categories, Cash Book, Bank Book, KNET tracking, and P&L.
- **Domain-Grouped Reports & Owner Dashboard**: 23 reports grouped across Sales, Inventory, Purchases, and Finance with numeric sorting, pagination, CSV & Excel (`.xlsx`) downloads.
- **4-Role RBAC & Audit Log**: `OWNER`, `MANAGER`, `BILLING_STAFF`, and `STOREKEEPER` enforced across views, templates, APIs, and service logic, plus immutable `AuditLog`.

---

## Setup Instructions

### 1. Virtual Environment & Dependencies
```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Configuration (`.env`)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
- **Local Development (SQLite)**: `DATABASE_URL=sqlite:///db.sqlite3`
- **Production (PostgreSQL)**: `DATABASE_URL=postgres://marina_user:strong_password@localhost:5432/marina_shoes`

### 3. Migrations & Demo Seed Data
```bash
python manage.py migrate
python manage.py seed_data
```

Demo accounts created by `seed_data` (clearly marked for demo use; do not mix with live client data):
| Role | Username | Password |
| :--- | :--- | :--- |
| Owner | `owner` | `Owner1234!` |
| Manager | `manager` | `Manager1234!` |
| Billing Staff | `billing` | `Billing1234!` |
| Storekeeper | `storekeeper` | `Store1234!` |

### 4. Development Server & Testing
```bash
# Run development server
python manage.py runserver

# Run full pytest suite
pytest
```

---

## Production Deployment & Operations Guidance

1. **Settings & Secrets**:
   - Set `DJANGO_SETTINGS_MODULE=config.settings.production` and `DEBUG=False`.
   - Set a strong 50+ character `SECRET_KEY`, explicit `ALLOWED_HOSTS`, and `CSRF_TRUSTED_ORIGINS`.
   - Keep `SECURE_SSL_REDIRECT=True` behind HTTPS/Nginx/reverse proxy (or `False` only if TLS terminates at your proxy and internal HTTP health checks hit `/healthz/`).
2. **Static Files, Container & WSGI**:
   - Run `python manage.py collectstatic --noinput` (served via WhiteNoise or Nginx).
   - Serve via `gunicorn config.wsgi:application --workers 4` or the included `Dockerfile` (`GET /healthz/` built-in health probe).
3. **Database Backup & Restore (SQLite & PostgreSQL)**:
   - Cross-platform CLI:
     - `python manage.py db_backup --output-dir ./backups`
     - `python manage.py db_restore <backup_file> --verify-only`
   - PostgreSQL bash scripts:
     - `DATABASE_URL=postgres://... ./scripts/backup_postgres.sh /var/backups/marina`
     - `DATABASE_URL=postgres://... ./scripts/restore_postgres.sh /var/backups/marina/<file>.dump`
4. **WeasyPrint System Dependencies**:
   - **Linux / Docker**: Included in `Dockerfile` (`libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf2.0-0`).
   - **Windows**: Install GTK3 Runtime (`https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows`). Without GTK3 on Windows, PDF download falls back gracefully to the Print View (`/invoicing/<id>/print/`).
5. **Tax Configuration**:
   - Kuwait defaults to `0.00%` product tax (`KWD` / `KD`, `Asia/Kuwait`); custom per-product or per-variant tax rates remain configurable in store/catalog settings if needed.
