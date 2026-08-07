"""Machine / equipment inventory and meter readings (requirements s.4)."""

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..extensions import db
from ..models import (
    MACHINE_CONDITIONS,
    MACHINE_STATUSES,
    MACHINE_STATUS_LABELS,
    READING_SOURCES,
    Contract,
    Customer,
    Machine,
    MeterReading,
    ROLE_TECH,
    next_asset_tag,
)
from ..security import management_required, audit
from ..services import contracts_missing_readings
from ..utils import arg_str, form_bool, form_date, form_decimal, form_int, form_str

machines_bp = Blueprint("machines", __name__, url_prefix="/machines")


@machines_bp.route("/")
@login_required
def index():
    search = arg_str("q")
    status = arg_str("status")

    query = Machine.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Machine.asset_tag.ilike(like),
                Machine.model.ilike(like),
                Machine.manufacturer.ilike(like),
                Machine.serial_number.ilike(like),
                Machine.location.ilike(like),
            )
        )
    if status:
        query = query.filter(Machine.status == status)

    machines = query.order_by(Machine.asset_tag.asc()).all()
    counts = {s: Machine.query.filter_by(status=s).count() for s in MACHINE_STATUSES}
    return render_template(
        "leasing/machines/list.html",
        page_title="Machine Inventory",
        machines=machines,
        search=search,
        status=status,
        statuses=MACHINE_STATUSES,
        status_labels=MACHINE_STATUS_LABELS,
        counts=counts,
    )


@machines_bp.route("/<int:machine_id>")
@login_required
def detail(machine_id):
    machine = db.get_or_404(Machine, machine_id)
    return render_template(
        "leasing/machines/detail.html",
        page_title=machine.display,
        machine=machine,
        status_labels=MACHINE_STATUS_LABELS,
        sources=READING_SOURCES,
        today=date.today(),
    )


@machines_bp.route("/new", methods=["GET", "POST"])
@management_required
def create():
    if request.method == "POST":
        machine = Machine(asset_tag=form_str("asset_tag") or next_asset_tag())
        error = _populate(machine)
        if error:
            flash(error, "danger")
        else:
            db.session.add(machine)
            db.session.flush()
            audit("created", "Machine", machine.id, machine.display)
            db.session.commit()
            flash(f"Machine {machine.asset_tag} added to inventory.", "success")
            return redirect(url_for("machines.detail", machine_id=machine.id))
        return render_template(
            "leasing/machines/form.html",
            page_title="Register Machine",
            machine=machine,
            statuses=MACHINE_STATUSES,
            status_labels=MACHINE_STATUS_LABELS,
            conditions=MACHINE_CONDITIONS,
            customers=Customer.query.order_by(Customer.company_name).all(),
            suggested_tag=machine.asset_tag,
        )

    return render_template(
        "leasing/machines/form.html",
        page_title="Register Machine",
        machine=Machine(status="in_stock", condition="new", supports_colour=True),
        statuses=MACHINE_STATUSES,
        status_labels=MACHINE_STATUS_LABELS,
        conditions=MACHINE_CONDITIONS,
        customers=Customer.query.order_by(Customer.company_name).all(),
        suggested_tag=next_asset_tag(),
    )


@machines_bp.route("/<int:machine_id>/edit", methods=["GET", "POST"])
@management_required
def edit(machine_id):
    machine = db.get_or_404(Machine, machine_id)
    if request.method == "POST":
        error = _populate(machine)
        if error:
            flash(error, "danger")
        else:
            audit("updated", "Machine", machine.id, machine.display)
            db.session.commit()
            flash("Machine updated.", "success")
            return redirect(url_for("machines.detail", machine_id=machine.id))

    return render_template(
        "leasing/machines/form.html",
        page_title=f"Edit {machine.asset_tag}",
        machine=machine,
        statuses=MACHINE_STATUSES,
        status_labels=MACHINE_STATUS_LABELS,
        conditions=MACHINE_CONDITIONS,
        customers=Customer.query.order_by(Customer.company_name).all(),
        suggested_tag=machine.asset_tag,
    )


# --------------------------------------------------------------------------- #
# Meter readings
# --------------------------------------------------------------------------- #


@machines_bp.route("/readings")
@login_required
def readings():
    query = MeterReading.query
    machine_id = arg_str("machine")
    if machine_id.isdigit():
        query = query.filter(MeterReading.machine_id == int(machine_id))
    if current_user.role == ROLE_TECH and arg_str("mine") == "1":
        query = query.filter(MeterReading.recorded_by_id == current_user.id)

    entries = (
        query.order_by(MeterReading.reading_date.desc(), MeterReading.id.desc())
        .limit(300)
        .all()
    )
    return render_template(
        "leasing/machines/readings.html",
        page_title="Meter Readings",
        readings=entries,
        machines=Machine.query.order_by(Machine.asset_tag).all(),
        machine_id=machine_id,
        stale=contracts_missing_readings(),
        sources=READING_SOURCES,
        today=date.today(),
    )


@machines_bp.route("/readings/new", methods=["POST"])
@login_required
def add_reading():
    machine_id = form_int("machine_id")
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        flash("Select a machine before saving a reading.", "danger")
        return redirect(url_for("machines.readings"))

    reading_date = form_date("reading_date", date.today())
    mono = form_int("mono_count")
    colour = form_int("colour_count")

    previous = latest_reading_for_machine(machine)
    if previous and reading_date >= previous.reading_date:
        if mono < (previous.mono_count or 0) or colour < (previous.colour_count or 0):
            flash(
                "Meter counts cannot go backwards — last reading was "
                f"{previous.mono_count:,} mono / {previous.colour_count:,} colour "
                f"on {previous.reading_date:%d %b %Y}.",
                "danger",
            )
            return redirect(request.referrer or url_for("machines.readings"))

    contract = machine.active_contract
    reading = MeterReading(
        machine_id=machine.id,
        contract_id=contract.id if contract else None,
        reading_date=reading_date,
        mono_count=mono,
        colour_count=colour,
        source=form_str("source", "technician", 20),
        notes=form_str("notes", max_length=255),
        recorded_by_id=current_user.id,
    )
    db.session.add(reading)
    db.session.flush()
    audit(
        "recorded reading",
        "Machine",
        machine.id,
        f"{mono:,} mono / {colour:,} colour on {reading_date:%d %b %Y}",
    )
    db.session.commit()
    flash(f"Reading saved for {machine.asset_tag}.", "success")
    return redirect(request.referrer or url_for("machines.detail", machine_id=machine.id))


def latest_reading_for_machine(machine):
    return (
        MeterReading.query.filter_by(machine_id=machine.id)
        .order_by(MeterReading.reading_date.desc(), MeterReading.id.desc())
        .first()
    )


# --------------------------------------------------------------------------- #


def _populate(machine):
    machine.manufacturer = form_str("manufacturer", max_length=80)
    machine.model = form_str("model", max_length=120)
    machine.serial_number = form_str("serial_number", max_length=80)
    machine.condition = form_str("condition", "new", 20)
    machine.status = form_str("status", "in_stock", 20)
    machine.supports_colour = form_bool("supports_colour")
    machine.location = form_str("location", max_length=160)
    machine.purchase_date = form_date("purchase_date")
    machine.purchase_cost = form_decimal("purchase_cost")
    machine.notes = form_str("notes")

    customer_id = form_int("customer_id")
    machine.customer_id = customer_id or None

    if not machine.manufacturer or not machine.model:
        return "Manufacturer and model are required."
    if not machine.serial_number:
        return "Serial number is required."
    if machine.status not in MACHINE_STATUSES:
        return "Choose a valid status."

    clash = Machine.query.filter(
        Machine.serial_number == machine.serial_number, Machine.id != machine.id
    ).first()
    if clash:
        return f"Serial number already registered to {clash.asset_tag}."

    if machine.status != "leased":
        active = Contract.query.filter_by(machine_id=machine.id, status="active").first()
        if active:
            return (
                f"This machine is on active lease {active.reference}. "
                "Terminate the lease before changing its status."
            )
    return None
