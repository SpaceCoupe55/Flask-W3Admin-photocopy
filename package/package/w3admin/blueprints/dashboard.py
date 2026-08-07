"""Role-based dashboards (requirements s.4 — Reporting & Dashboard)."""

from datetime import date, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..models import (
    Contract,
    Customer,
    Invoice,
    Machine,
    MeterReading,
    ServiceTicket,
)
from ..services import (
    admin_metrics,
    contracts_missing_readings,
    expiring_contracts,
    low_stock_parts,
    monthly_revenue_series,
    refresh_overdue_invoices,
    technician_metrics,
    technician_workload,
)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
def home():
    refresh_overdue_invoices()

    if current_user.is_technician:
        return _technician_dashboard()
    if current_user.is_sales:
        return _sales_dashboard()
    return _admin_dashboard()


# --------------------------------------------------------------------------- #


def _admin_dashboard():
    metrics = admin_metrics()
    series = monthly_revenue_series(6)

    overdue = (
        Invoice.query.filter(Invoice.status == "overdue")
        .order_by(Invoice.due_date.asc())
        .limit(6)
        .all()
    )
    tickets = (
        ServiceTicket.query.filter(
            ServiceTicket.status.in_(("open", "assigned", "in_progress"))
        )
        .order_by(ServiceTicket.created_at.desc())
        .limit(6)
        .all()
    )
    machines = Machine.query.all()
    status_counts = {}
    for machine in machines:
        status_counts[machine.status] = status_counts.get(machine.status, 0) + 1

    return render_template(
        "leasing/dashboard/admin.html",
        page_title="Business Overview",
        metrics=metrics,
        series=series,
        overdue=overdue,
        tickets=tickets,
        expiring=expiring_contracts()[:6],
        low_stock=low_stock_parts()[:5],
        missed_readings=contracts_missing_readings()[:5],
        status_counts=status_counts,
        workload=technician_workload(),
    )


def _sales_dashboard():
    metrics = admin_metrics()
    series = monthly_revenue_series(6)

    outstanding = (
        Invoice.query.filter(Invoice.status.in_(("sent", "part_paid", "overdue")))
        .order_by(Invoice.due_date.asc())
        .limit(8)
        .all()
    )
    recent_customers = (
        Customer.query.order_by(Customer.created_at.desc()).limit(5).all()
    )
    new_leases = (
        Contract.query.filter(Contract.start_date >= date.today() - timedelta(days=30))
        .order_by(Contract.start_date.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "leasing/dashboard/sales.html",
        page_title="Sales & Billing",
        metrics=metrics,
        series=series,
        outstanding=outstanding,
        recent_customers=recent_customers,
        new_leases=new_leases,
        expiring=expiring_contracts()[:6],
        missed_readings=contracts_missing_readings()[:6],
        available_machines=Machine.query.filter_by(status="in_stock").count(),
    )


def _technician_dashboard():
    metrics = technician_metrics(current_user)

    jobs = (
        ServiceTicket.query.filter(
            ServiceTicket.assigned_to_id == current_user.id,
            ServiceTicket.status.in_(("assigned", "in_progress")),
        )
        .order_by(ServiceTicket.scheduled_for.is_(None), ServiceTicket.scheduled_for.asc())
        .all()
    )
    recent = (
        ServiceTicket.query.filter(
            ServiceTicket.assigned_to_id == current_user.id,
            ServiceTicket.status.in_(("resolved", "closed")),
        )
        .order_by(ServiceTicket.resolved_at.desc())
        .limit(5)
        .all()
    )
    readings = (
        MeterReading.query.filter_by(recorded_by_id=current_user.id)
        .order_by(MeterReading.reading_date.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "leasing/dashboard/technician.html",
        page_title="My Workday",
        metrics=metrics,
        jobs=jobs,
        recent=recent,
        readings=readings,
        low_stock=low_stock_parts()[:6],
    )
