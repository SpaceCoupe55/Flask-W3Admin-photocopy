"""Reporting: revenue, receivables, machine utilisation, technician workload."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, Response, render_template

from ..extensions import db
from ..models import (
    Contract,
    Customer,
    Invoice,
    Machine,
    MACHINE_STATUS_LABELS,
    Payment,
    ServiceTicket,
)
from ..security import management_required
from ..services import (
    money,
    monthly_revenue_series,
    outstanding_total,
    refresh_overdue_invoices,
    revenue_between,
    technician_workload,
)
from ..utils import arg_str

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _range_from_args():
    """Report window — defaults to the last 6 months."""
    end = date.today()
    start = end.replace(day=1) - timedelta(days=150)
    raw_start, raw_end = arg_str("from"), arg_str("to")
    for raw, name in ((raw_start, "start"), (raw_end, "end")):
        if raw:
            try:
                parsed = date.fromisoformat(raw)
                if name == "start":
                    start = parsed
                else:
                    end = parsed
            except ValueError:
                pass
    return start, end


@reports_bp.route("/")
@management_required
def index():
    refresh_overdue_invoices()
    start, end = _range_from_args()

    invoices = Invoice.query.filter(
        Invoice.issue_date >= start,
        Invoice.issue_date <= end,
        Invoice.status != "cancelled",
    ).all()
    billed = sum((i.total for i in invoices), Decimal("0"))
    collected = revenue_between(start, end)

    machines = Machine.query.all()
    leased = [m for m in machines if m.status == "leased"]
    by_status = {}
    for machine in machines:
        by_status[machine.status] = by_status.get(machine.status, 0) + 1

    return render_template(
        "leasing/reports/index.html",
        page_title="Reports",
        start=start,
        end=end,
        billed=money(billed),
        collected=collected,
        outstanding=outstanding_total(),
        series=monthly_revenue_series(6),
        invoice_count=len(invoices),
        by_status=by_status,
        status_labels=MACHINE_STATUS_LABELS,
        utilisation=round(100 * len(leased) / len(machines)) if machines else 0,
        workload=technician_workload(),
        top_customers=_top_customers(start, end),
        aging=_receivables_aging(),
        machine_rows=_machine_utilisation(),
        ticket_summary=_ticket_summary(start, end),
    )


def _top_customers(start, end, limit=8):
    rows = []
    for customer in Customer.query.all():
        invoiced = sum(
            (
                i.total
                for i in customer.invoices
                if start <= i.issue_date <= end and i.status != "cancelled"
            ),
            Decimal("0"),
        )
        if invoiced:
            rows.append(
                {
                    "customer": customer,
                    "invoiced": money(invoiced),
                    "outstanding": money(customer.outstanding_balance),
                    "machines": len(customer.active_contracts),
                }
            )
    rows.sort(key=lambda r: r["invoiced"], reverse=True)
    return rows[:limit]


def _receivables_aging():
    buckets = {"current": Decimal("0"), "1-30": Decimal("0"), "31-60": Decimal("0"), "60+": Decimal("0")}
    for invoice in Invoice.query.filter(
        Invoice.status.in_(("sent", "part_paid", "overdue"))
    ).all():
        days = (date.today() - invoice.due_date).days
        if days <= 0:
            buckets["current"] += invoice.balance
        elif days <= 30:
            buckets["1-30"] += invoice.balance
        elif days <= 60:
            buckets["31-60"] += invoice.balance
        else:
            buckets["60+"] += invoice.balance
    return {k: money(v) for k, v in buckets.items()}


def _machine_utilisation():
    rows = []
    for machine in Machine.query.order_by(Machine.asset_tag).all():
        contract = machine.active_contract
        reading = machine.latest_reading
        rows.append(
            {
                "machine": machine,
                "contract": contract,
                "customer": machine.customer,
                "last_reading": reading,
                "volume": reading.total_count if reading else 0,
                "tickets": len([t for t in machine.tickets if t.is_open]),
            }
        )
    rows.sort(key=lambda r: r["volume"], reverse=True)
    return rows


def _ticket_summary(start, end):
    tickets = ServiceTicket.query.filter(
        db.func.date(ServiceTicket.created_at) >= start,
        db.func.date(ServiceTicket.created_at) <= end,
    ).all()
    resolved = [t for t in tickets if t.status in ("resolved", "closed") and t.resolved_at]
    avg_hours = 0
    if resolved:
        total = sum(
            (t.resolved_at - t.created_at).total_seconds() / 3600 for t in resolved
        )
        avg_hours = round(total / len(resolved), 1)
    return {
        "logged": len(tickets),
        "resolved": len(resolved),
        "open": len([t for t in tickets if t.is_open]),
        "avg_resolution_hours": avg_hours,
    }


# --------------------------------------------------------------------------- #
# CSV exports
# --------------------------------------------------------------------------- #


def _csv_response(filename, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reports_bp.route("/revenue.csv")
@management_required
def revenue_csv():
    start, end = _range_from_args()
    rows = [
        [
            p.paid_on.isoformat(),
            p.invoice.number,
            p.invoice.customer.company_name,
            p.method_label,
            p.reference or "",
            f"{p.amount:.2f}",
        ]
        for p in Payment.query.filter(
            Payment.paid_on >= start, Payment.paid_on <= end
        ).order_by(Payment.paid_on).all()
    ]
    return _csv_response(
        f"revenue-{start}-to-{end}.csv",
        ["Date", "Invoice", "Customer", "Method", "Reference", "Amount"],
        rows,
    )


@reports_bp.route("/overdue.csv")
@management_required
def overdue_csv():
    refresh_overdue_invoices()
    rows = [
        [
            i.number,
            i.customer.company_name,
            i.customer.phone or "",
            i.due_date.isoformat(),
            i.days_overdue,
            f"{i.total:.2f}",
            f"{i.balance:.2f}",
        ]
        for i in Invoice.query.filter(Invoice.status == "overdue")
        .order_by(Invoice.due_date)
        .all()
    ]
    return _csv_response(
        f"overdue-{date.today()}.csv",
        ["Invoice", "Customer", "Phone", "Due", "Days Overdue", "Total", "Balance"],
        rows,
    )


@reports_bp.route("/utilisation.csv")
@management_required
def utilisation_csv():
    rows = [
        [
            r["machine"].asset_tag,
            f"{r['machine'].manufacturer} {r['machine'].model}",
            MACHINE_STATUS_LABELS.get(r["machine"].status, r["machine"].status),
            r["customer"].company_name if r["customer"] else "",
            r["contract"].reference if r["contract"] else "",
            r["last_reading"].reading_date.isoformat() if r["last_reading"] else "",
            r["volume"],
            r["tickets"],
        ]
        for r in _machine_utilisation()
    ]
    return _csv_response(
        f"machine-utilisation-{date.today()}.csv",
        [
            "Asset Tag",
            "Machine",
            "Status",
            "Customer",
            "Contract",
            "Last Reading",
            "Total Copies",
            "Open Tickets",
        ],
        rows,
    )


@reports_bp.route("/contracts.csv")
@management_required
def contracts_csv():
    rows = [
        [
            c.reference,
            c.customer.company_name,
            c.machine.asset_tag,
            c.billing_type_label,
            c.start_date.isoformat(),
            c.end_date.isoformat(),
            c.status,
            f"{c.flat_monthly_fee:.2f}",
            f"{c.mono_rate:.4f}",
            f"{c.colour_rate:.4f}",
        ]
        for c in Contract.query.order_by(Contract.end_date).all()
    ]
    return _csv_response(
        f"contracts-{date.today()}.csv",
        [
            "Reference",
            "Customer",
            "Machine",
            "Billing",
            "Start",
            "End",
            "Status",
            "Monthly Fee",
            "Mono Rate",
            "Colour Rate",
        ],
        rows,
    )
