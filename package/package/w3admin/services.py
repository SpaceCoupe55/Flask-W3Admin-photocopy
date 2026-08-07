"""Business logic for the leasing workflows described in the requirements.

Everything that is more than a straight database write lives here so the
blueprints stay thin: invoice building from meter readings, payment
application, stock deduction, contract renewal/termination and the alert feed
that drives the dashboards.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from .extensions import db
from .models import (
    Contract,
    Invoice,
    InvoiceLine,
    Machine,
    MeterReading,
    Part,
    PartUsage,
    Payment,
    ServiceTicket,
    Setting,
    Customer,
    User,
    ROLE_TECH,
    default_due_date,
    next_contract_reference,
    next_invoice_number,
)

MISSED_READING_DAYS = 40


# --------------------------------------------------------------------------- #
# Money helpers
# --------------------------------------------------------------------------- #


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def currency(value):
    return f"{Setting.get('currency_symbol', 'GHS ')}{money(value):,.2f}"


# --------------------------------------------------------------------------- #
# Meter readings
# --------------------------------------------------------------------------- #


def billing_baseline(contract):
    """Reading the next per-copy invoice should be measured from.

    Preference order: the last reading already billed on this lease; failing
    that the last reading taken before the lease started (the opening meter);
    failing that the first reading of the lease.
    """
    readings = (
        MeterReading.query.filter_by(machine_id=contract.machine_id)
        .order_by(MeterReading.reading_date.asc(), MeterReading.id.asc())
        .all()
    )
    if not readings:
        return None

    in_term = [r for r in readings if r.reading_date >= contract.start_date]
    billed = [r for r in in_term if r.billed]
    if billed:
        return billed[-1]

    before = [r for r in readings if r.reading_date < contract.start_date]
    if in_term and before:
        return before[-1]
    if in_term:
        return in_term[0]
    return before[-1]


def latest_reading(contract):
    return (
        MeterReading.query.filter_by(machine_id=contract.machine_id)
        .order_by(MeterReading.reading_date.desc(), MeterReading.id.desc())
        .first()
    )


def unbilled_volume(contract):
    """(mono, colour, baseline, current) copies waiting to be invoiced."""
    baseline = billing_baseline(contract)
    current = latest_reading(contract)
    if not baseline or not current or current.id == baseline.id:
        return 0, 0, baseline, current
    mono = max((current.mono_count or 0) - (baseline.mono_count or 0), 0)
    colour = max((current.colour_count or 0) - (baseline.colour_count or 0), 0)
    return mono, colour, baseline, current


def contracts_missing_readings(days=MISSED_READING_DAYS):
    """Per-copy contracts whose meter has not been read recently (s.4)."""
    cutoff = date.today() - timedelta(days=days)
    stale = []
    for contract in Contract.query.filter(
        Contract.status == "active",
        Contract.billing_type.in_(("per_copy", "hybrid")),
    ).all():
        last = latest_reading(contract)
        if last is None or last.reading_date < cutoff:
            stale.append((contract, last))
    return stale


# --------------------------------------------------------------------------- #
# Invoicing
# --------------------------------------------------------------------------- #


def build_invoice_for_contract(contract, period_start, period_end, created_by=None):
    """Create a draft invoice for one billing period of a lease.

    Flat contracts get a rental line; per-copy contracts get mono/colour lines
    priced from the difference between the last billed meter reading and the
    most recent one; hybrid contracts get both.
    """
    invoice = Invoice(
        number=next_invoice_number(),
        customer_id=contract.customer_id,
        contract_id=contract.id,
        issue_date=date.today(),
        due_date=default_due_date(),
        period_start=period_start,
        period_end=period_end,
        status="draft",
        tax_rate=Setting.get_decimal("default_tax_rate"),
        created_by_id=created_by.id if created_by else None,
    )

    machine = contract.machine
    label = f"{machine.manufacturer} {machine.model} ({machine.asset_tag})"

    if contract.billing_type in ("flat", "hybrid"):
        invoice.lines.append(
            InvoiceLine(
                description=(
                    f"Monthly lease rental — {label} · "
                    f"{period_start:%d %b %Y} to {period_end:%d %b %Y}"
                ),
                quantity=Decimal("1"),
                unit_price=Decimal(contract.flat_monthly_fee or 0),
                kind="rental",
            )
        )

    consumed_readings = []
    if contract.charges_per_copy:
        mono, colour, baseline, current = unbilled_volume(contract)
        billable_mono = max(mono - (contract.included_mono or 0), 0)
        billable_colour = max(colour - (contract.included_colour or 0), 0)

        if billable_mono:
            invoice.lines.append(
                InvoiceLine(
                    description=(
                        f"Black & white copies — {billable_mono:,} "
                        f"(meter {baseline.mono_count:,} → {current.mono_count:,}"
                        + (
                            f", {contract.included_mono:,} included)"
                            if contract.included_mono
                            else ")"
                        )
                    ),
                    quantity=Decimal(billable_mono),
                    unit_price=Decimal(contract.mono_rate or 0),
                    kind="mono",
                )
            )
        if billable_colour:
            invoice.lines.append(
                InvoiceLine(
                    description=(
                        f"Colour copies — {billable_colour:,} "
                        f"(meter {baseline.colour_count:,} → {current.colour_count:,}"
                        + (
                            f", {contract.included_colour:,} included)"
                            if contract.included_colour
                            else ")"
                        )
                    ),
                    quantity=Decimal(billable_colour),
                    unit_price=Decimal(contract.colour_rate or 0),
                    kind="colour",
                )
            )
        if current is not None and (baseline is None or current.id != baseline.id):
            consumed_readings.append(current)

    db.session.add(invoice)
    for reading in consumed_readings:
        reading.billed = True
    return invoice


def chargeable_parts(contract, since=None):
    """Parts used on resolved tickets for this contract's machine, rechargeable."""
    q = (
        PartUsage.query.join(ServiceTicket)
        .join(Part)
        .filter(
            ServiceTicket.machine_id == contract.machine_id,
            Part.charge_to_customer.is_(True),
        )
    )
    if since:
        q = q.filter(PartUsage.used_at >= datetime.combine(since, datetime.min.time()))
    return q.all()


def mark_invoice_sent(invoice):
    if invoice.status == "draft":
        invoice.status = "sent"
        invoice.sent_at = datetime.utcnow()
    invoice.recalculate_status()


def apply_payment(invoice, amount, paid_on, method, reference, user, notes=None):
    payment = Payment(
        invoice_id=invoice.id,
        amount=money(amount),
        paid_on=paid_on,
        method=method,
        reference=reference,
        notes=notes,
        recorded_by_id=user.id if user else None,
    )
    db.session.add(payment)
    invoice.payments.append(payment)
    if invoice.status == "draft":
        invoice.status = "sent"
    invoice.recalculate_status()
    return payment


def refresh_overdue_invoices():
    """Flip 'sent'/'part_paid' invoices to overdue once their due date passes."""
    changed = 0
    for invoice in Invoice.query.filter(
        Invoice.status.in_(("sent", "part_paid")), Invoice.due_date < date.today()
    ).all():
        invoice.recalculate_status()
        changed += 1
    if changed:
        db.session.commit()
    return changed


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


def activate_contract(contract):
    """Flag the machine as leased and tie it to the customer (workflow 5.1)."""
    # A freshly added contract has not been flushed, so `contract.machine` may
    # still be empty — resolve the machine by id in that case.
    machine = contract.machine or db.session.get(Machine, contract.machine_id)
    if machine is None:
        return
    machine.status = "leased"
    machine.customer_id = contract.customer_id
    if contract.status == "draft":
        contract.status = "active"


def terminate_contract(contract, reason, return_machine=True):
    """End a lease and hand the machine back to stock (workflow 5.4)."""
    contract.status = "terminated"
    contract.terminated_on = date.today()
    contract.termination_reason = reason
    if return_machine and contract.machine:
        contract.machine.status = "in_stock"
        contract.machine.customer_id = None


def renew_contract(contract, start_date, end_date, overrides=None, created_by=None):
    """Create a follow-on contract on the same machine (workflow 5.4)."""
    overrides = overrides or {}
    new = Contract(
        reference=next_contract_reference(),
        customer_id=contract.customer_id,
        machine_id=contract.machine_id,
        start_date=start_date,
        end_date=end_date,
        billing_type=overrides.get("billing_type", contract.billing_type),
        billing_day=overrides.get("billing_day", contract.billing_day),
        flat_monthly_fee=overrides.get("flat_monthly_fee", contract.flat_monthly_fee),
        mono_rate=overrides.get("mono_rate", contract.mono_rate),
        colour_rate=overrides.get("colour_rate", contract.colour_rate),
        included_mono=overrides.get("included_mono", contract.included_mono),
        included_colour=overrides.get("included_colour", contract.included_colour),
        status="active",
        renewed_from_id=contract.id,
        notes=overrides.get("notes"),
        created_by_id=created_by.id if created_by else None,
    )
    contract.status = "expired"
    db.session.add(new)
    activate_contract(new)
    return new


def expiring_contracts(days=None):
    days = days if days is not None else Setting.get_int("contract_alert_days", 30)
    horizon = date.today() + timedelta(days=days)
    return (
        Contract.query.filter(
            Contract.status == "active", Contract.end_date <= horizon
        )
        .order_by(Contract.end_date.asc())
        .all()
    )


# --------------------------------------------------------------------------- #
# Service & stock
# --------------------------------------------------------------------------- #


def log_part_usage(ticket, part, quantity, user):
    """Record consumables against a job and deduct them from stock (5.3)."""
    quantity = max(int(quantity or 0), 1)
    usage = PartUsage(
        ticket_id=ticket.id,
        part_id=part.id,
        quantity=quantity,
        unit_cost=part.unit_cost or 0,
        recorded_by_id=user.id if user else None,
    )
    part.quantity_in_stock = max((part.quantity_in_stock or 0) - quantity, 0)
    db.session.add(usage)
    return usage


def low_stock_parts():
    return (
        Part.query.filter(Part.quantity_in_stock <= Part.reorder_level)
        .order_by(Part.quantity_in_stock.asc())
        .all()
    )


def resolve_ticket(ticket, resolution):
    ticket.status = "resolved"
    ticket.resolution = resolution
    ticket.resolved_at = datetime.utcnow()
    if ticket.machine and ticket.machine.status == "maintenance":
        ticket.machine.status = "leased" if ticket.machine.customer_id else "in_stock"


# --------------------------------------------------------------------------- #
# Alerts & dashboard metrics
# --------------------------------------------------------------------------- #


def alert_feed(user, limit=8):
    """Notifications shown in the header, filtered by what the role can act on."""
    alerts = []

    if user.role == ROLE_TECH:
        jobs = (
            ServiceTicket.query.filter(
                ServiceTicket.assigned_to_id == user.id,
                ServiceTicket.status.in_(("assigned", "in_progress")),
            )
            .order_by(ServiceTicket.priority.desc(), ServiceTicket.created_at.asc())
            .limit(limit)
            .all()
        )
        for job in jobs:
            alerts.append(
                {
                    "tone": job.priority_class,
                    "title": f"{job.reference} · {job.title}",
                    "meta": f"{job.customer.company_name} — {job.status_label}",
                    "url": f"/service/tickets/{job.id}",
                }
            )
        for part in low_stock_parts()[:3]:
            alerts.append(
                {
                    "tone": "danger",
                    "title": f"Low stock: {part.name}",
                    "meta": f"{part.quantity_in_stock} left (reorder at {part.reorder_level})",
                    "url": "/inventory/parts",
                }
            )
        return alerts[:limit]

    overdue = (
        Invoice.query.filter(Invoice.status == "overdue")
        .order_by(Invoice.due_date.asc())
        .limit(4)
        .all()
    )
    for inv in overdue:
        alerts.append(
            {
                "tone": "danger",
                "title": f"Invoice {inv.number} overdue",
                "meta": f"{inv.customer.company_name} — {currency(inv.balance)} · {inv.days_overdue}d",
                "url": f"/billing/invoices/{inv.id}",
            }
        )

    for contract in expiring_contracts()[:4]:
        days = contract.days_to_expiry
        overdue = days < 0
        alerts.append(
            {
                "tone": "danger" if overdue else "warning",
                "title": f"Lease {contract.reference} {'expired' if overdue else 'expiring'}",
                "meta": (
                    f"{contract.customer.company_name} — "
                    + (f"ended {abs(days)} days ago" if overdue else f"{days} days left")
                ),
                "url": f"/contracts/{contract.id}",
            }
        )

    unassigned = (
        ServiceTicket.query.filter(ServiceTicket.status == "open")
        .order_by(ServiceTicket.created_at.asc())
        .limit(3)
        .all()
    )
    for ticket in unassigned:
        alerts.append(
            {
                "tone": "info",
                "title": f"{ticket.reference} awaiting assignment",
                "meta": f"{ticket.customer.company_name} — {ticket.title}",
                "url": f"/service/tickets/{ticket.id}",
            }
        )

    for part in low_stock_parts()[:3]:
        alerts.append(
            {
                "tone": "danger",
                "title": f"Low stock: {part.name}",
                "meta": f"{part.quantity_in_stock} left (reorder at {part.reorder_level})",
                "url": "/inventory/parts",
            }
        )

    return alerts[:limit]


def revenue_between(start, end):
    total = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.paid_on >= start, Payment.paid_on <= end)
        .scalar()
    )
    return money(total)


def monthly_revenue_series(months=6):
    """[(label, collected, invoiced)] for the last N months, oldest first."""
    today = date.today()
    series = []
    for offset in range(months - 1, -1, -1):
        year = today.year
        month = today.month - offset
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        collected = revenue_between(start, end)
        invoiced = sum(
            (
                inv.total
                for inv in Invoice.query.filter(
                    Invoice.issue_date >= start,
                    Invoice.issue_date <= end,
                    Invoice.status != "cancelled",
                ).all()
            ),
            Decimal("0"),
        )
        series.append((start.strftime("%b"), collected, money(invoiced)))
    return series


def outstanding_total():
    return money(
        sum(
            (inv.balance for inv in Invoice.query.filter(
                Invoice.status.in_(("sent", "part_paid", "overdue"))
            ).all()),
            Decimal("0"),
        )
    )


def admin_metrics():
    today = date.today()
    month_start = today.replace(day=1)
    machines = Machine.query.all()
    leased = [m for m in machines if m.status == "leased"]
    invoices = Invoice.query.all()
    overdue = [i for i in invoices if i.status == "overdue"]

    return {
        "customers": Customer.query.filter_by(status="active").count(),
        "active_contracts": Contract.query.filter_by(status="active").count(),
        "machines_total": len(machines),
        "machines_leased": len(leased),
        "machines_available": len([m for m in machines if m.status == "in_stock"]),
        "utilisation": round(100 * len(leased) / len(machines)) if machines else 0,
        "revenue_mtd": revenue_between(month_start, today),
        "outstanding": outstanding_total(),
        "overdue_count": len(overdue),
        "overdue_value": money(sum((i.balance for i in overdue), Decimal("0"))),
        "open_tickets": ServiceTicket.query.filter(
            ServiceTicket.status.in_(("open", "assigned", "in_progress"))
        ).count(),
        "low_stock": len(low_stock_parts()),
        "expiring": len(expiring_contracts()),
        "missed_readings": len(contracts_missing_readings()),
    }


def technician_metrics(user):
    mine = ServiceTicket.query.filter_by(assigned_to_id=user.id)
    return {
        "assigned": mine.filter(ServiceTicket.status == "assigned").count(),
        "in_progress": mine.filter(ServiceTicket.status == "in_progress").count(),
        "resolved_30d": mine.filter(
            ServiceTicket.status.in_(("resolved", "closed")),
            ServiceTicket.resolved_at >= datetime.utcnow() - timedelta(days=30),
        ).count(),
        "low_stock": len(low_stock_parts()),
    }


def technician_workload():
    rows = []
    for tech in User.query.filter_by(role=ROLE_TECH, active=True).all():
        tickets = ServiceTicket.query.filter_by(assigned_to_id=tech.id)
        rows.append(
            {
                "technician": tech,
                "open": tickets.filter(
                    ServiceTicket.status.in_(("assigned", "in_progress"))
                ).count(),
                "resolved": tickets.filter(
                    ServiceTicket.status.in_(("resolved", "closed"))
                ).count(),
                "parts_cost": money(
                    sum(
                        (
                            u.line_cost
                            for u in PartUsage.query.filter_by(recorded_by_id=tech.id).all()
                        ),
                        Decimal("0"),
                    )
                ),
            }
        )
    return rows
