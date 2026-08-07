"""Lease / contract management: onboarding, renewal, termination (s.5.1, 5.4)."""

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    BILLING_TYPES,
    BILLING_TYPE_LABELS,
    CONTRACT_STATUSES,
    Contract,
    Customer,
    Machine,
    next_contract_reference,
)
from ..security import management_required, audit
from ..services import (
    activate_contract,
    expiring_contracts,
    renew_contract,
    terminate_contract,
    unbilled_volume,
)
from ..utils import add_months, arg_str, form_date, form_decimal, form_int, form_str

contracts_bp = Blueprint("contracts", __name__, url_prefix="/contracts")


def _available_machines(include=None):
    machines = Machine.query.filter(Machine.status == "in_stock").all()
    if include is not None and include not in machines:
        machines.append(include)
    return sorted(machines, key=lambda m: m.asset_tag)


@contracts_bp.route("/")
@management_required
def index():
    status = arg_str("status")
    search = arg_str("q")
    view = arg_str("view")

    query = Contract.query
    if status:
        query = query.filter(Contract.status == status)
    if search:
        like = f"%{search}%"
        query = query.join(Customer).filter(
            db.or_(Contract.reference.ilike(like), Customer.company_name.ilike(like))
        )

    contracts = query.order_by(Contract.end_date.asc()).all()
    if view == "expiring":
        contracts = [c for c in contracts if c.is_expiring_soon or c.is_expired]

    return render_template(
        "leasing/contracts/list.html",
        page_title="Lease Contracts",
        contracts=contracts,
        statuses=CONTRACT_STATUSES,
        status=status,
        search=search,
        view=view,
        expiring_count=len(expiring_contracts()),
        billing_labels=BILLING_TYPE_LABELS,
    )


@contracts_bp.route("/<int:contract_id>")
@management_required
def detail(contract_id):
    contract = db.get_or_404(Contract, contract_id)
    mono, colour, baseline, current = unbilled_volume(contract)
    return render_template(
        "leasing/contracts/detail.html",
        page_title=f"Lease {contract.reference}",
        contract=contract,
        unbilled=(mono, colour),
        baseline=baseline,
        current_reading=current,
        today=date.today(),
        default_renewal_end=add_months(max(contract.end_date, date.today()), 12),
    )


@contracts_bp.route("/new", methods=["GET", "POST"])
@management_required
def create():
    preset_customer = arg_str("customer")

    if request.method == "POST":
        contract = Contract(
            reference=next_contract_reference(), created_by_id=current_user.id
        )
        error = _populate(contract, new=True)
        if error:
            flash(error, "danger")
        else:
            db.session.add(contract)
            activate_contract(contract)
            db.session.flush()
            audit(
                "created",
                "Contract",
                contract.id,
                f"{contract.reference} for {contract.customer.company_name}",
            )
            db.session.commit()
            flash(
                f"Lease {contract.reference} created — "
                f"{contract.machine.asset_tag} is now marked as leased.",
                "success",
            )
            return redirect(url_for("contracts.detail", contract_id=contract.id))

        return _render_form(contract, "New Lease Contract")

    draft = Contract(
        start_date=date.today(),
        end_date=add_months(date.today(), 12),
        billing_type="flat",
        billing_day=1,
        status="active",
        customer_id=int(preset_customer) if preset_customer.isdigit() else None,
    )
    return _render_form(draft, "New Lease Contract")


@contracts_bp.route("/<int:contract_id>/edit", methods=["GET", "POST"])
@management_required
def edit(contract_id):
    contract = db.get_or_404(Contract, contract_id)
    if request.method == "POST":
        error = _populate(contract, new=False)
        if error:
            flash(error, "danger")
        else:
            audit("updated", "Contract", contract.id, contract.reference)
            db.session.commit()
            flash("Contract updated.", "success")
            return redirect(url_for("contracts.detail", contract_id=contract.id))
    return _render_form(contract, f"Edit {contract.reference}")


@contracts_bp.route("/<int:contract_id>/renew", methods=["POST"])
@management_required
def renew(contract_id):
    contract = db.get_or_404(Contract, contract_id)
    start = form_date("start_date", max(contract.end_date, date.today()))
    end = form_date("end_date", add_months(start, 12))
    if end <= start:
        flash("The renewal end date must be after the start date.", "danger")
        return redirect(url_for("contracts.detail", contract_id=contract.id))

    overrides = {}
    if request.form.get("flat_monthly_fee"):
        overrides["flat_monthly_fee"] = form_decimal("flat_monthly_fee")
    if request.form.get("mono_rate"):
        overrides["mono_rate"] = form_decimal("mono_rate")
    if request.form.get("colour_rate"):
        overrides["colour_rate"] = form_decimal("colour_rate")
    if request.form.get("billing_type"):
        overrides["billing_type"] = form_str("billing_type", contract.billing_type, 20)
    overrides["notes"] = f"Renewal of {contract.reference}"

    new = renew_contract(contract, start, end, overrides, created_by=current_user)
    db.session.flush()
    audit("renewed", "Contract", contract.id, f"{contract.reference} → {new.reference}")
    db.session.commit()
    flash(f"Lease renewed as {new.reference}.", "success")
    return redirect(url_for("contracts.detail", contract_id=new.id))


@contracts_bp.route("/<int:contract_id>/terminate", methods=["POST"])
@management_required
def terminate(contract_id):
    contract = db.get_or_404(Contract, contract_id)
    reason = form_str("reason", "Terminated by agreement", 255)
    return_machine = request.form.get("return_machine") != "0"

    terminate_contract(contract, reason, return_machine)
    audit("terminated", "Contract", contract.id, reason)
    db.session.commit()
    flash(
        f"Lease {contract.reference} terminated."
        + (" Machine returned to available stock." if return_machine else ""),
        "success",
    )
    return redirect(url_for("contracts.detail", contract_id=contract.id))


# --------------------------------------------------------------------------- #


def _render_form(contract, title):
    return render_template(
        "leasing/contracts/form.html",
        page_title=title,
        contract=contract,
        customers=Customer.query.order_by(Customer.company_name).all(),
        machines=_available_machines(contract.machine),
        billing_types=BILLING_TYPES,
        billing_labels=BILLING_TYPE_LABELS,
        statuses=CONTRACT_STATUSES,
    )


def _populate(contract, new):
    contract.customer_id = form_int("customer_id") or None
    contract.machine_id = form_int("machine_id") or None
    contract.start_date = form_date("start_date", date.today())
    contract.end_date = form_date("end_date")
    contract.billing_type = form_str("billing_type", "flat", 20)
    contract.billing_day = min(max(form_int("billing_day", 1), 1), 28)
    contract.flat_monthly_fee = form_decimal("flat_monthly_fee")
    contract.mono_rate = form_decimal("mono_rate")
    contract.colour_rate = form_decimal("colour_rate")
    contract.included_mono = form_int("included_mono")
    contract.included_colour = form_int("included_colour")
    contract.notes = form_str("notes")
    if not new:
        contract.status = form_str("status", contract.status, 20)

    if not contract.customer_id:
        return "Select the customer this lease belongs to."
    if not contract.machine_id:
        return "Select a machine to assign."
    if not contract.end_date:
        return "An end date is required."
    if contract.end_date <= contract.start_date:
        return "The end date must be after the start date."
    if contract.billing_type not in BILLING_TYPES:
        return "Choose a valid billing type."
    if contract.billing_type in ("flat", "hybrid") and contract.flat_monthly_fee <= 0:
        return "Enter the monthly fee for a flat-rate lease."
    if contract.billing_type in ("per_copy", "hybrid") and contract.mono_rate <= 0:
        return "Enter at least a black & white per-copy rate."

    clash = Contract.query.filter(
        Contract.machine_id == contract.machine_id,
        Contract.status == "active",
        Contract.id != contract.id,
    ).first()
    if clash:
        return f"That machine is already on active lease {clash.reference}."
    return None
