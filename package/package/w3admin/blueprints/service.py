"""Service & maintenance tickets (requirements s.4, workflow s.5.3)."""

from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    ROLE_TECH,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    TICKET_STATUS_LABELS,
    Customer,
    Machine,
    Part,
    ServiceTicket,
    User,
    next_ticket_reference,
)
from ..security import MANAGEMENT, management_required, owns_ticket, audit
from ..services import log_part_usage, resolve_ticket
from ..utils import arg_str, form_datetime, form_int, form_str

service_bp = Blueprint("service", __name__, url_prefix="/service")


def _visible_tickets():
    """Technicians only see their own jobs; management sees everything."""
    query = ServiceTicket.query
    if current_user.role == ROLE_TECH:
        query = query.filter(ServiceTicket.assigned_to_id == current_user.id)
    return query


@service_bp.route("/tickets")
@login_required
def tickets():
    status = arg_str("status")
    priority = arg_str("priority")
    search = arg_str("q")
    technician = arg_str("technician")

    query = _visible_tickets()
    if status == "open":
        query = query.filter(ServiceTicket.status.in_(("open", "assigned", "in_progress")))
    elif status:
        query = query.filter(ServiceTicket.status == status)
    if priority:
        query = query.filter(ServiceTicket.priority == priority)
    if technician.isdigit():
        query = query.filter(ServiceTicket.assigned_to_id == int(technician))
    if search:
        like = f"%{search}%"
        query = query.join(Customer).filter(
            db.or_(
                ServiceTicket.reference.ilike(like),
                ServiceTicket.title.ilike(like),
                Customer.company_name.ilike(like),
            )
        )

    rows = query.order_by(ServiceTicket.created_at.desc()).all()
    return render_template(
        "leasing/service/list.html",
        page_title="Service Tickets",
        tickets=rows,
        statuses=TICKET_STATUSES,
        status_labels=TICKET_STATUS_LABELS,
        priorities=TICKET_PRIORITIES,
        status=status,
        priority=priority,
        search=search,
        technician=technician,
        technicians=User.query.filter_by(role=ROLE_TECH, active=True).all(),
    )


@service_bp.route("/tickets/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = db.get_or_404(ServiceTicket, ticket_id)
    if not owns_ticket(ticket):
        abort(403)
    return render_template(
        "leasing/service/detail.html",
        page_title=f"Ticket {ticket.reference}",
        ticket=ticket,
        statuses=TICKET_STATUSES,
        status_labels=TICKET_STATUS_LABELS,
        priorities=TICKET_PRIORITIES,
        technicians=User.query.filter_by(role=ROLE_TECH, active=True).all(),
        parts=Part.query.order_by(Part.name).all(),
        can_manage=current_user.role in MANAGEMENT,
    )


@service_bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        ticket = ServiceTicket(
            reference=next_ticket_reference(), logged_by_id=current_user.id
        )
        ticket.customer_id = form_int("customer_id") or None
        ticket.machine_id = form_int("machine_id") or None
        ticket.title = form_str("title", max_length=160)
        ticket.description = form_str("description")
        ticket.priority = form_str("priority", "normal", 20)
        ticket.scheduled_for = form_datetime("scheduled_for")

        assigned = form_int("assigned_to_id")
        if assigned and current_user.role in MANAGEMENT:
            ticket.assigned_to_id = assigned
            ticket.status = "assigned"

        if not ticket.customer_id or not ticket.title:
            flash("Customer and a short title are required.", "danger")
        else:
            db.session.add(ticket)
            db.session.flush()
            audit("created", "ServiceTicket", ticket.id, f"{ticket.reference} — {ticket.title}")
            db.session.commit()
            flash(f"Ticket {ticket.reference} logged.", "success")
            return redirect(url_for("service.detail", ticket_id=ticket.id))

    preset_customer = arg_str("customer")
    return render_template(
        "leasing/service/form.html",
        page_title="Log Service Request",
        customers=Customer.query.order_by(Customer.company_name).all(),
        machines=Machine.query.order_by(Machine.asset_tag).all(),
        technicians=User.query.filter_by(role=ROLE_TECH, active=True).all(),
        priorities=TICKET_PRIORITIES,
        preset_customer=int(preset_customer) if preset_customer.isdigit() else None,
        can_assign=current_user.role in MANAGEMENT,
    )


@service_bp.route("/tickets/<int:ticket_id>/assign", methods=["POST"])
@management_required
def assign(ticket_id):
    ticket = db.get_or_404(ServiceTicket, ticket_id)
    technician_id = form_int("assigned_to_id")
    technician = db.session.get(User, technician_id) if technician_id else None

    if technician is None or technician.role != ROLE_TECH:
        flash("Select a technician to assign this job to.", "danger")
    else:
        ticket.assigned_to_id = technician.id
        ticket.scheduled_for = form_datetime("scheduled_for", ticket.scheduled_for)
        if ticket.status == "open":
            ticket.status = "assigned"
        audit("assigned", "ServiceTicket", ticket.id, f"to {technician.name}")
        db.session.commit()
        flash(f"{ticket.reference} assigned to {technician.name}.", "success")
    return redirect(url_for("service.detail", ticket_id=ticket.id))


@service_bp.route("/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
def update_status(ticket_id):
    ticket = db.get_or_404(ServiceTicket, ticket_id)
    if not owns_ticket(ticket):
        abort(403)

    status = form_str("status", ticket.status, 20)
    if status not in TICKET_STATUSES:
        flash("Unknown status.", "danger")
        return redirect(url_for("service.detail", ticket_id=ticket.id))

    if status == "resolved":
        resolution = form_str("resolution")
        if not resolution:
            flash("Describe what you did before resolving the ticket.", "danger")
            return redirect(url_for("service.detail", ticket_id=ticket.id))
        resolve_ticket(ticket, resolution)
    else:
        ticket.status = status
        if status == "in_progress" and ticket.machine:
            ticket.machine.status = "maintenance"

    audit("status changed", "ServiceTicket", ticket.id, f"{ticket.reference} → {status}")
    db.session.commit()
    flash(f"{ticket.reference} is now {ticket.status_label}.", "success")
    return redirect(url_for("service.detail", ticket_id=ticket.id))


@service_bp.route("/tickets/<int:ticket_id>/parts", methods=["POST"])
@login_required
def log_parts(ticket_id):
    ticket = db.get_or_404(ServiceTicket, ticket_id)
    if not owns_ticket(ticket):
        abort(403)

    part = db.session.get(Part, form_int("part_id"))
    quantity = form_int("quantity", 1)

    if part is None:
        flash("Select a part or consumable.", "danger")
    elif quantity < 1:
        flash("Quantity must be at least one.", "danger")
    elif quantity > part.quantity_in_stock:
        flash(
            f"Only {part.quantity_in_stock} × {part.name} left in stock.",
            "danger",
        )
    else:
        log_part_usage(ticket, part, quantity, current_user)
        audit(
            "logged parts",
            "ServiceTicket",
            ticket.id,
            f"{quantity} × {part.name} ({part.quantity_in_stock} left)",
        )
        db.session.commit()
        flash(
            f"{quantity} × {part.name} logged against {ticket.reference} "
            f"— stock now {part.quantity_in_stock}.",
            "success",
        )
    return redirect(url_for("service.detail", ticket_id=ticket.id))


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


@service_bp.route("/schedule")
@login_required
def schedule():
    return render_template(
        "leasing/service/schedule.html",
        page_title="Service Schedule",
        unscheduled=_visible_tickets()
        .filter(
            ServiceTicket.scheduled_for.is_(None),
            ServiceTicket.status.in_(("open", "assigned", "in_progress")),
        )
        .order_by(ServiceTicket.created_at.asc())
        .all(),
    )


@service_bp.route("/schedule/events.json")
@login_required
def schedule_events():
    """Feed for the FullCalendar view kept from the template."""
    start = request.args.get("start")
    end = request.args.get("end")
    query = _visible_tickets().filter(ServiceTicket.scheduled_for.isnot(None))

    def _parse(value, fallback):
        try:
            return datetime.fromisoformat(value[:19])
        except (TypeError, ValueError):
            return fallback

    window_start = _parse(start, datetime.utcnow() - timedelta(days=60))
    window_end = _parse(end, datetime.utcnow() + timedelta(days=120))
    query = query.filter(
        ServiceTicket.scheduled_for >= window_start,
        ServiceTicket.scheduled_for <= window_end,
    )

    colours = {
        "low": "#6c757d",
        "normal": "#17a2b8",
        "high": "#ffb800",
        "critical": "#e74c3c",
    }
    events = []
    for ticket in query.all():
        events.append(
            {
                "id": ticket.id,
                "title": f"{ticket.reference} · {ticket.customer.company_name}",
                "start": ticket.scheduled_for.isoformat(),
                "end": (ticket.scheduled_for + timedelta(hours=2)).isoformat(),
                "url": url_for("service.detail", ticket_id=ticket.id),
                "backgroundColor": colours.get(ticket.priority, "#17a2b8"),
                "borderColor": colours.get(ticket.priority, "#17a2b8"),
            }
        )
    return jsonify(events)


@service_bp.route("/tickets/<int:ticket_id>/schedule", methods=["POST"])
@login_required
def reschedule(ticket_id):
    ticket = db.get_or_404(ServiceTicket, ticket_id)
    if not owns_ticket(ticket):
        abort(403)
    ticket.scheduled_for = form_datetime("scheduled_for")
    audit(
        "scheduled",
        "ServiceTicket",
        ticket.id,
        ticket.scheduled_for.strftime("%d %b %Y %H:%M") if ticket.scheduled_for else "cleared",
    )
    db.session.commit()
    flash("Visit schedule updated.", "success")
    return redirect(request.referrer or url_for("service.detail", ticket_id=ticket.id))
