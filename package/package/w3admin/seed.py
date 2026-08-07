"""Demo dataset so the dashboards, reports and workflows have something to show.

Runs automatically the first time the app starts against an empty database
(disable with SEED_DEMO_DATA=0), or on demand via `flask seed-demo`.
"""

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from .extensions import db
from .models import (
    AuditLog,
    CommunicationLog,
    Contract,
    Customer,
    Invoice,
    InvoiceLine,
    Machine,
    MeterReading,
    Part,
    PartUsage,
    Payment,
    ROLE_ADMIN,
    ROLE_SALES,
    ROLE_TECH,
    ServiceTicket,
    Setting,
    User,
)

DEMO_PASSWORD = "Password123"

USERS = [
    ("Ama Boateng", "admin@copytrack.example", ROLE_ADMIN, "Managing Director"),
    ("Kwesi Mensah", "sales@copytrack.example", ROLE_SALES, "Sales Manager"),
    ("Yaw Owusu", "tech@copytrack.example", ROLE_TECH, "Field Technician"),
    ("Efua Dartey", "tech2@copytrack.example", ROLE_TECH, "Senior Technician"),
]

CUSTOMERS = [
    ("Sunrise Chambers", "Adjoa Nyarko", "adjoa@sunrisechambers.example", "+233 24 111 2201", "14 Independence Ave", "Accra"),
    ("Volta Rural Bank", "Kofi Agyeman", "kofi@voltabank.example", "+233 24 111 2202", "3 Market Street", "Ho"),
    ("Northern Polytechnic", "Sadia Alhassan", "sadia@npoly.example", "+233 24 111 2203", "University Road", "Tamale"),
    ("Gold Coast Logistics", "Nii Armah", "nii@gclogistics.example", "+233 24 111 2204", "Harbour Road", "Tema"),
    ("Ashanti Medical Centre", "Akua Frimpong", "akua@ashantimed.example", "+233 24 111 2205", "Hospital Road", "Kumasi"),
    ("Cape Coast Legal Aid", "Kojo Baidoo", "kojo@cclegal.example", "+233 24 111 2206", "Castle Road", "Cape Coast"),
    ("Takoradi Freight Ltd", "Esi Quayson", "esi@tkfreight.example", "+233 24 111 2207", "Port Access Road", "Takoradi"),
    ("Greenfield Academy", "Yaa Serwaa", "yaa@greenfield.example", "+233 24 111 2208", "School Lane", "Koforidua"),
]

MACHINES = [
    ("Canon", "imageRUNNER 2630i", True, Decimal("18500")),
    ("Ricoh", "IM C3000", True, Decimal("24500")),
    ("Kyocera", "TASKalfa 3554ci", True, Decimal("21000")),
    ("HP", "LaserJet MFP E72530", False, Decimal("15800")),
    ("Canon", "imageRUNNER 2425", False, Decimal("11200")),
    ("Sharp", "BP-70C31", True, Decimal("26400")),
    ("Ricoh", "MP 2555SP", False, Decimal("13900")),
    ("Kyocera", "ECOSYS M4132idn", False, Decimal("9800")),
    ("Canon", "imageRUNNER C3226i", True, Decimal("23100")),
    ("Xerox", "VersaLink C7025", True, Decimal("20400")),
    ("Ricoh", "IM 4000", False, Decimal("17600")),
    ("Kyocera", "TASKalfa 2554ci", True, Decimal("19900")),
]

PARTS = [
    ("TNR-CAN-045K", "Canon 045 Black Toner", "toner", 14, 6, Decimal("320"), True),
    ("TNR-CAN-045C", "Canon 045 Cyan Toner", "toner", 4, 4, Decimal("410"), True),
    ("TNR-RIC-C3000", "Ricoh IM C3000 Black Toner", "toner", 9, 5, Decimal("480"), True),
    ("TNR-KYO-TK5244", "Kyocera TK-5244 Black Toner", "toner", 2, 5, Decimal("360"), True),
    ("DRM-CAN-2630", "Canon iR 2630 Drum Unit", "drum", 3, 2, Decimal("1250"), False),
    ("DRM-RIC-4000", "Ricoh IM 4000 Drum Unit", "drum", 1, 2, Decimal("1420"), False),
    ("PRT-FUSER-A", "Fuser Assembly (A series)", "part", 5, 2, Decimal("2100"), False),
    ("PRT-ROLL-PU", "Paper Pickup Roller", "part", 22, 8, Decimal("95"), False),
    ("PRT-WASTE-01", "Waste Toner Bottle", "consumable", 11, 6, Decimal("140"), True),
    ("PRT-SEP-PAD", "Separation Pad", "consumable", 3, 6, Decimal("70"), False),
]

TICKET_TEMPLATES = [
    ("Paper jam in tray 2", "Jams every few pages when using recycled A4 stock.", "high"),
    ("Streaks on colour prints", "Vertical cyan streaks across the full page.", "normal"),
    ("Toner low warning stuck", "Warning persists after replacing the cartridge.", "low"),
    ("Machine will not power on", "No response from the panel since the power cut.", "critical"),
    ("Scan to email failing", "Scans queue but never arrive at the mailbox.", "normal"),
    ("Noisy fuser unit", "Loud grinding during warm-up.", "high"),
    ("Duplex unit misfeeds", "Double-sided printing skews every second sheet.", "normal"),
    ("Routine preventive service", "Scheduled quarterly service and clean.", "low"),
]


def seed_if_empty(force=False):
    """Populate the demo dataset into an empty database.

    Returns True if anything was created. `force` is accepted so the CLI can
    call it explicitly, but an already-populated database is never re-seeded —
    that would duplicate every reference number.
    """
    if User.query.first() is not None:
        return False

    rng = random.Random(20260807)  # deterministic demo data
    today = date.today()

    for key, value in Setting.DEFAULTS.items():
        Setting.set(key, value)

    # -- users ------------------------------------------------------------- #
    users = {}
    for name, email, role, title in USERS:
        user = User(name=name, email=email, role=role, job_title=title, active=True)
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
        users[email] = user
    db.session.flush()

    admin = users["admin@copytrack.example"]
    sales = users["sales@copytrack.example"]
    technicians = [users["tech@copytrack.example"], users["tech2@copytrack.example"]]

    # -- customers --------------------------------------------------------- #
    customers = []
    for index, (company, contact, email, phone, address, city) in enumerate(CUSTOMERS, start=1):
        customer = Customer(
            code=f"CUS-{index:04d}",
            company_name=company,
            contact_name=contact,
            email=email,
            phone=phone,
            address=address,
            city=city,
            status="active" if index <= 6 else "prospect",
            created_by_id=sales.id,
            created_at=datetime.utcnow() - timedelta(days=320 - index * 18),
        )
        db.session.add(customer)
        customers.append(customer)
    db.session.flush()

    for customer in customers[:5]:
        db.session.add(
            CommunicationLog(
                customer_id=customer.id,
                channel=rng.choice(["call", "email", "visit"]),
                summary=rng.choice(
                    [
                        "Confirmed monthly meter reading arrangement with the office manager.",
                        "Discussed upgrading to a colour unit at renewal.",
                        "Chased outstanding invoice — payment promised end of week.",
                        "Site visit to review paper stock and machine placement.",
                    ]
                ),
                happened_at=datetime.utcnow() - timedelta(days=rng.randint(2, 60)),
                logged_by_id=sales.id,
            )
        )

    # -- machines ---------------------------------------------------------- #
    machines = []
    for index, (make, model, colour, cost) in enumerate(MACHINES, start=1):
        machine = Machine(
            asset_tag=f"MC-{index:04d}",
            manufacturer=make,
            model=model,
            serial_number=f"{make[:3].upper()}{2024000 + index * 37}",
            condition=rng.choice(["new", "new", "refurbished"]),
            status="in_stock",
            supports_colour=colour,
            purchase_date=today - timedelta(days=rng.randint(200, 900)),
            purchase_cost=cost,
            location="Main warehouse",
        )
        db.session.add(machine)
        machines.append(machine)
    db.session.flush()

    # -- contracts, readings, invoices ------------------------------------- #
    parts = []
    for sku, name, category, qty, reorder, cost, rechargeable in PARTS:
        part = Part(
            sku=sku,
            name=name,
            category=category,
            unit="unit",
            quantity_in_stock=qty,
            reorder_level=reorder,
            unit_cost=cost,
            charge_to_customer=rechargeable,
            supplier="Accra Office Supplies",
        )
        db.session.add(part)
        parts.append(part)
    db.session.flush()

    contracts = []
    invoice_seq = 0
    # Five customers take two machines each — the last two units stay in stock
    # so the "assign a machine" step of the onboarding flow is demonstrable.
    for index, customer in enumerate(customers[:5]):
        for machine in (machines[index * 2], machines[index * 2 + 1]):
            billing_type = ["flat", "per_copy", "hybrid"][index % 3]
            start = today - timedelta(days=rng.randint(120, 420))
            end = start + timedelta(days=365)

            contract = Contract(
                reference=f"LSE-{len(contracts) + 1:04d}",
                customer_id=customer.id,
                machine_id=machine.id,
                start_date=start,
                end_date=end,
                billing_type=billing_type,
                billing_day=1,
                flat_monthly_fee=Decimal(rng.choice([450, 600, 750, 900]))
                if billing_type in ("flat", "hybrid")
                else Decimal("0"),
                mono_rate=Decimal("0.0850") if billing_type in ("per_copy", "hybrid") else Decimal("0"),
                colour_rate=Decimal("0.4500")
                if billing_type in ("per_copy", "hybrid") and machine.supports_colour
                else Decimal("0"),
                included_mono=1000 if billing_type == "hybrid" else 0,
                status="active",
                created_by_id=sales.id,
                created_at=datetime.combine(start, datetime.min.time()),
            )
            machine.status = "leased"
            machine.customer_id = customer.id
            machine.location = f"{customer.company_name}, {customer.city}"
            db.session.add(contract)
            contracts.append(contract)
    db.session.flush()

    # Make two leases expire soon so renewal alerts have something to show.
    contracts[0].end_date = today + timedelta(days=12)
    contracts[3].end_date = today + timedelta(days=26)

    for contract in contracts:
        mono = rng.randint(4000, 12000)
        colour = rng.randint(500, 3000) if contract.machine.supports_colour else 0

        # Six months of monthly readings, oldest first.
        for months_ago in range(5, -1, -1):
            reading_date = today - timedelta(days=30 * months_ago)
            if reading_date < contract.start_date:
                continue
            mono += rng.randint(1500, 5200)
            colour += rng.randint(150, 900) if contract.machine.supports_colour else 0
            db.session.add(
                MeterReading(
                    machine_id=contract.machine_id,
                    contract_id=contract.id,
                    reading_date=reading_date,
                    mono_count=mono,
                    colour_count=colour,
                    source=rng.choice(["technician", "customer", "sales"]),
                    billed=months_ago > 0,
                    recorded_by_id=rng.choice(technicians).id,
                )
            )
    db.session.flush()

    # -- invoices & payments ----------------------------------------------- #
    for contract in contracts:
        for months_ago in (4, 3, 2, 1):
            period_start = (today.replace(day=1) - timedelta(days=30 * months_ago)).replace(day=1)
            period_end = period_start + timedelta(days=29)
            invoice_seq += 1

            invoice = Invoice(
                number=f"INV-{period_start.year}-{invoice_seq:04d}",
                customer_id=contract.customer_id,
                contract_id=contract.id,
                issue_date=period_end,
                due_date=period_end + timedelta(days=14),
                period_start=period_start,
                period_end=period_end,
                status="sent",
                tax_rate=Decimal("0"),
                created_by_id=sales.id,
                sent_at=datetime.combine(period_end, datetime.min.time()),
                created_at=datetime.combine(period_end, datetime.min.time()),
            )
            label = f"{contract.machine.manufacturer} {contract.machine.model} ({contract.machine.asset_tag})"

            if contract.billing_type in ("flat", "hybrid"):
                invoice.lines.append(
                    InvoiceLine(
                        description=f"Monthly lease rental — {label} · "
                        f"{period_start:%d %b %Y} to {period_end:%d %b %Y}",
                        quantity=Decimal("1"),
                        unit_price=Decimal(contract.flat_monthly_fee or 0),
                        kind="rental",
                    )
                )
            if contract.charges_per_copy:
                mono_copies = rng.randint(1800, 4800)
                invoice.lines.append(
                    InvoiceLine(
                        description=f"Black & white copies — {mono_copies:,}",
                        quantity=Decimal(mono_copies),
                        unit_price=contract.mono_rate,
                        kind="mono",
                    )
                )
                if contract.colour_rate:
                    colour_copies = rng.randint(200, 900)
                    invoice.lines.append(
                        InvoiceLine(
                            description=f"Colour copies — {colour_copies:,}",
                            quantity=Decimal(colour_copies),
                            unit_price=contract.colour_rate,
                            kind="colour",
                        )
                    )

            db.session.add(invoice)
            db.session.flush()

            # Older invoices are settled; the newest ones drive the receivables view.
            roll = rng.random()
            if months_ago >= 3 or roll < 0.55:
                db.session.add(
                    Payment(
                        invoice_id=invoice.id,
                        amount=invoice.total,
                        paid_on=min(invoice.due_date, today),
                        method=rng.choice(["mobile_money", "bank_transfer", "cheque"]),
                        reference=f"REF{rng.randint(100000, 999999)}",
                        recorded_by_id=sales.id,
                    )
                )
                invoice.status = "paid"
            elif roll < 0.75:
                part_amount = (invoice.total * Decimal("0.4")).quantize(Decimal("0.01"))
                db.session.add(
                    Payment(
                        invoice_id=invoice.id,
                        amount=part_amount,
                        paid_on=min(invoice.due_date, today),
                        method="mobile_money",
                        reference=f"REF{rng.randint(100000, 999999)}",
                        recorded_by_id=sales.id,
                    )
                )
                invoice.status = "part_paid" if invoice.due_date >= today else "overdue"
            else:
                invoice.status = "overdue" if invoice.due_date < today else "sent"
    db.session.flush()

    # -- service tickets & parts usage -------------------------------------- #
    for index, contract in enumerate(contracts):
        if index % 2 and index > 4:
            continue
        title, description, priority = TICKET_TEMPLATES[index % len(TICKET_TEMPLATES)]
        created = datetime.utcnow() - timedelta(days=rng.randint(1, 55))
        technician = rng.choice(technicians)

        if index % 4 == 0:
            status = "resolved"
        elif index % 4 == 1:
            status = "in_progress"
        elif index % 4 == 2:
            status = "assigned"
        else:
            status = "open"

        ticket = ServiceTicket(
            reference=f"TKT-{index + 1:04d}",
            customer_id=contract.customer_id,
            machine_id=contract.machine_id,
            title=title,
            description=description,
            priority=priority,
            status=status,
            assigned_to_id=None if status == "open" else technician.id,
            logged_by_id=sales.id,
            created_at=created,
            scheduled_for=None
            if status == "open"
            else created + timedelta(days=rng.randint(1, 5), hours=rng.randint(8, 15)),
            resolved_at=created + timedelta(days=rng.randint(1, 4)) if status == "resolved" else None,
            resolution="Cleaned feed path, replaced worn roller and test printed 50 pages."
            if status == "resolved"
            else None,
        )
        db.session.add(ticket)
        db.session.flush()

        if status in ("resolved", "in_progress"):
            part = rng.choice(parts)
            quantity = rng.randint(1, 2)
            part.quantity_in_stock = max(part.quantity_in_stock - quantity, 0)
            db.session.add(
                PartUsage(
                    ticket_id=ticket.id,
                    part_id=part.id,
                    quantity=quantity,
                    unit_cost=part.unit_cost,
                    used_at=created + timedelta(days=1),
                    recorded_by_id=technician.id,
                )
            )

    db.session.add(
        AuditLog(
            user_id=admin.id,
            action="seeded",
            entity="System",
            detail="Demo dataset loaded",
        )
    )
    db.session.commit()
    return True
