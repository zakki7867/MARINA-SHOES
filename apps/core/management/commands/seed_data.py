from datetime import timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import StoreLocation, StoreSettings
from apps.customers.models import Customer
from apps.expenses.models import Expense
from apps.inventory.models import Brand, Category, Product, ProductVariant
from apps.inventory.services import transfer_stock
from apps.invoicing.services import create_invoice
from apps.payments.services import record_customer_payment
from apps.purchases.models import Supplier
from apps.purchases.services import receive_goods, record_supplier_payment


class Command(BaseCommand):
    help = "Seed Marina Shoes Kuwait demo data (locations, users, products, variants, stock, customers, invoices, payments, expenses)."

    @transaction.atomic
    def handle(self, *args, **options):
        store = StoreSettings.get_settings()
        store.store_name = "MARINA SHOES"
        store.tagline = "Wholesale & Retail Footwear — Kuwait"
        store.gstin = "KW-CR-204891"
        store.state_code = "KW"
        store.currency_code = "KWD"
        store.currency_symbol = "KD"
        store.address = "Salem Al Mubarak St, Block 4, Salmiya, Kuwait"
        store.phone = "+965 2571 8400"
        store.save()

        main_loc = StoreLocation.get_default()
        main_loc.name = "Salmiya Main Showroom"
        main_loc.state_code = "KW"
        main_loc.address = "Salem Al Mubarak St, Salmiya, Kuwait"
        main_loc.phone = "+965 2571 8400"
        main_loc.save()

        warehouse, _ = StoreLocation.objects.get_or_create(
            code="WH1",
            defaults={
                "name": "Shuwaikh Industrial Godown",
                "location_type": StoreLocation.LocationType.WAREHOUSE,
                "state_code": "KW",
                "address": "Block 2, Shuwaikh Industrial Area, Kuwait",
                "phone": "+965 2483 1900",
                "is_default": False,
                "is_active": True,
            },
        )

        users = {}
        role_specs = [
            ("owner", User.Role.OWNER, "Owner1234!", "Marina", "Owner"),
            ("manager", User.Role.MANAGER, "Manager1234!", "Tariq", "Manager"),
            ("billing", User.Role.BILLING_STAFF, "Billing1234!", "Sara", "Billing"),
            ("storekeeper", User.Role.STOREKEEPER, "Store1234!", "Hassan", "Storekeeper"),
        ]
        for uname, role, pwd, first, last in role_specs:
            user, created = User.objects.get_or_create(
                username=uname,
                defaults={
                    "role": role,
                    "first_name": first,
                    "last_name": last,
                    "assigned_location": main_loc,
                    "is_staff": role == User.Role.OWNER,
                    "is_superuser": role == User.Role.OWNER,
                },
            )
            if created:
                user.set_password(pwd)
                user.save()
            users[uname] = user

        if ProductVariant.objects.exists():
            self.stdout.write(self.style.WARNING("Products/variants already exist; skipping duplicate demo seed."))
            return

        owner = users["owner"]
        manager = users["manager"]
        billing = users["billing"]
        storekeeper = users["storekeeper"]

        cats = {name: Category.objects.get_or_create(name=name)[0] for name in ["Sneakers", "Loafers", "Sandals", "Formal", "Sports"]}
        brands = {name: Brand.objects.get_or_create(name=name)[0] for name in ["Marina Classic", "Marina Sport", "GulfWalk", "DesertStride"]}

        product_specs = [
            ("Marina Runner 2026", "MR26", "Sneakers", "Marina Sport", "MEN", "6404", "0.00", [
                ("7", "White", "MR26-7-WHT", "6.500", "9.500", "13.900", 5, 30),
                ("8", "White", "MR26-8-WHT", "6.500", "9.500", "13.900", 5, 25),
                ("9", "Black", "MR26-9-BLK", "6.500", "9.500", "13.900", 5, 4),
            ]),
            ("Suede Penny Loafer", "SPL01", "Loafers", "Marina Classic", "MEN", "6403", "0.00", [
                ("7", "Tan", "SPL01-7-TAN", "8.500", "12.000", "17.900", 4, 18),
                ("8", "Tan", "SPL01-8-TAN", "8.500", "12.000", "17.900", 4, 20),
                ("9", "Navy", "SPL01-9-NVY", "8.500", "12.000", "17.900", 4, 3),
            ]),
            ("Gulf Comfort Sandal", "GCS09", "Sandals", "GulfWalk", "WOMEN", "6403", "0.00", [
                ("6", "Brown", "GCS09-6-BRN", "3.500", "5.200", "7.900", 6, 40),
                ("7", "Brown", "GCS09-7-BRN", "3.500", "5.200", "7.900", 6, 35),
                ("8", "Black", "GCS09-8-BLK", "3.500", "5.200", "7.900", 6, 15),
            ]),
            ("Executive Oxford Brogue", "EOB04", "Formal", "Marina Classic", "MEN", "6403", "0.00", [
                ("8", "Black", "EOB04-8-BLK", "11.000", "15.500", "22.900", 4, 16),
                ("9", "Black", "EOB04-9-BLK", "11.000", "15.500", "22.900", 4, 22),
            ]),
            ("Pro Court Trainer", "PCT07", "Sports", "DesertStride", "UNISEX", "6404", "0.00", [
                ("8", "Grey", "PCT07-8-GRY", "7.200", "10.500", "14.900", 5, 24),
                ("9", "Blue", "PCT07-9-BLU", "7.200", "10.500", "14.900", 5, 5),
            ]),
        ]

        variants_map = {}
        grn_items = []
        for pname, scode, cname, bname, gender, hsn, tax, vlist in product_specs:
            prod = Product.objects.create(
                name=pname,
                style_code=scode,
                category=cats[cname],
                brand=brands[bname],
                gender=gender,
                hsn_code=hsn,
                tax_rate=Decimal(tax),
            )
            for size, color, sku, cost, wprice, rprice, reorder, init_qty in vlist:
                var = ProductVariant.objects.create(
                    product=prod,
                    size=size,
                    color=color,
                    sku=sku,
                    barcode=f"629{len(variants_map) + 100001}",
                    cost_price=Decimal(cost),
                    wholesale_price=Decimal(wprice),
                    retail_price=Decimal(rprice),
                    reorder_level=reorder,
                )
                variants_map[sku] = var
                grn_items.append({"variant": var, "quantity_received": init_qty, "unit_cost": Decimal(cost), "tax_rate": Decimal(tax)})

        sup1 = Supplier.objects.create(name="Gulf Footwear Import Co.", contact_person="Faisal", phone="+96524810011", state_code="KW", gstin="KW-SUP-1042")
        Supplier.objects.create(name="Al-Rai Leather Distributors", contact_person="Khalid", phone="+96524720022", state_code="KW", gstin="KW-SUP-2088")

        receive_goods(
            supplier=sup1,
            location=main_loc,
            items_data=grn_items,
            supplier_invoice_no="GFI-2026-101",
            user=storekeeper,
        )
        transfer_stock(
            from_location=main_loc,
            to_location=warehouse,
            items_data=[{"variant": variants_map["MR26-7-WHT"], "quantity": 5}],
            notes="Initial Shuwaikh warehouse allocation",
            user=storekeeper,
        )
        record_supplier_payment(supplier=sup1, amount=Decimal("500.000"), payment_mode="BANK", user=manager, reference_no="KFAST-8821")

        c1 = Customer.objects.create(
            name="Hawalli Footwear Gallery",
            customer_type=Customer.CustomerType.WHOLESALE,
            phone="+96599112233",
            whatsapp_number="+96599112233",
            city="Hawalli",
            state_code="HAWALLI",
            gstin="KW-CR-88121",
            credit_limit=Decimal("1500.000"),
            payment_terms_days=30,
        )
        c2 = Customer.objects.create(
            name="Farwaniya Shoe Mart",
            customer_type=Customer.CustomerType.WHOLESALE,
            phone="+96599334455",
            whatsapp_number="+96599334455",
            city="Farwaniya",
            state_code="FARWANIYA",
            credit_limit=Decimal("1200.000"),
            payment_terms_days=15,
        )
        c3 = Customer.objects.create(
            name="Fahaheel Footwear Palace",
            customer_type=Customer.CustomerType.WHOLESALE,
            phone="+96599556677",
            city="Fahaheel",
            state_code="AHMADI",
            gstin="KW-CR-99410",
            credit_limit=Decimal("2000.000"),
            payment_terms_days=30,
        )
        c4 = Customer.objects.create(
            name="Walk-in Retail Customer",
            customer_type=Customer.CustomerType.RETAIL,
            phone="+96590000001",
            city="Salmiya",
            state_code="KW",
            credit_limit=Decimal("0.000"),
            payment_terms_days=0,
        )

        today = timezone.localdate()
        inv1 = create_invoice(
            customer=c1,
            location=main_loc,
            items_data=[
                {"variant": variants_map["MR26-7-WHT"], "quantity": 10},
                {"variant": variants_map["SPL01-7-TAN"], "quantity": 5},
            ],
            invoice_date=today - timedelta(days=10),
            user=billing,
        )
        record_customer_payment(customer=c1, invoice=inv1, amount=Decimal("100.000"), payment_mode="KNET", reference_no="KNET-4412", user=billing)

        create_invoice(
            customer=c2,
            location=main_loc,
            items_data=[
                {"variant": variants_map["GCS09-6-BRN"], "quantity": 12},
                {"variant": variants_map["EOB04-9-BLK"], "quantity": 4},
            ],
            invoice_date=today - timedelta(days=35),
            due_date=today - timedelta(days=20),
            user=billing,
        )

        create_invoice(
            customer=c3,
            location=main_loc,
            items_data=[{"variant": variants_map["PCT07-8-GRY"], "quantity": 8}],
            invoice_date=today,
            user=billing,
        )

        inv4 = create_invoice(
            customer=c4,
            location=main_loc,
            items_data=[{"variant": variants_map["MR26-8-WHT"], "quantity": 2}],
            invoice_date=today,
            user=billing,
        )
        record_customer_payment(customer=c4, invoice=inv4, amount=inv4.grand_total, payment_mode="CASH", user=billing)

        Expense.objects.create(expense_date=today - timedelta(days=5), location=main_loc, category=Expense.Category.RENT, amount=Decimal("450.000"), payment_mode="BANK", notes="Salmiya showroom rent", created_by=owner)
        Expense.objects.create(expense_date=today, location=main_loc, category=Expense.Category.PACKAGING, amount=Decimal("18.500"), payment_mode="CASH", notes="Shoe boxes & bags", created_by=manager)

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded Marina Shoes Kuwait demo data! Users: owner/Owner1234!, manager/Manager1234!, billing/Billing1234!, storekeeper/Store1234!"
            )
        )