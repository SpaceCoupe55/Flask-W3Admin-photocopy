"""Customer management (requirements s.4)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..extensions import db
from ..models import (
    CUSTOMER_STATUSES,
    CommunicationLog,
    Customer,
    Invoice,
    next_customer_code,
)
from ..security import management_required, audit
from ..utils import arg_str, form_str

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


@customers_bp.route("/")
@login_required
def index():
    search = arg_str("q")
    status = arg_str("status")

    query = Customer.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Customer.company_name.ilike(like),
                Customer.contact_name.ilike(like),
                Customer.code.ilike(like),
                Customer.email.ilike(like),
                Customer.phone.ilike(like),
                Customer.city.ilike(like),
            )
        )
    if status:
        query = query.filter(Customer.status == status)

    customers = query.order_by(Customer.company_name.asc()).all()
    return render_template(
        "leasing/customers/list.html",
        page_title="Customers",
        customers=customers,
        search=search,
        status=status,
        statuses=CUSTOMER_STATUSES,
    )


@customers_bp.route("/<int:customer_id>")
@login_required
def detail(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    invoices = (
        Invoice.query.filter_by(customer_id=customer.id)
        .order_by(Invoice.issue_date.desc())
        .all()
    )
    return render_template(
        "leasing/customers/detail.html",
        page_title=customer.company_name,
        customer=customer,
        invoices=invoices,
    )


@customers_bp.route("/new", methods=["GET", "POST"])
@management_required
def create():
    if request.method == "POST":
        customer = Customer(code=next_customer_code(), created_by_id=current_user.id)
        error = _populate(customer)
        if error:
            flash(error, "danger")
            return render_template(
                "leasing/customers/form.html",
                page_title="New Customer",
                customer=customer,
                statuses=CUSTOMER_STATUSES,
            )
        db.session.add(customer)
        db.session.flush()
        audit("created", "Customer", customer.id, customer.company_name)
        db.session.commit()
        flash(f"Customer {customer.code} created.", "success")
        return redirect(url_for("customers.detail", customer_id=customer.id))

    return render_template(
        "leasing/customers/form.html",
        page_title="New Customer",
        customer=Customer(status="active"),
        statuses=CUSTOMER_STATUSES,
    )


@customers_bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@management_required
def edit(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    if request.method == "POST":
        error = _populate(customer)
        if error:
            flash(error, "danger")
        else:
            audit("updated", "Customer", customer.id, customer.company_name)
            db.session.commit()
            flash("Customer updated.", "success")
            return redirect(url_for("customers.detail", customer_id=customer.id))

    return render_template(
        "leasing/customers/form.html",
        page_title=f"Edit {customer.company_name}",
        customer=customer,
        statuses=CUSTOMER_STATUSES,
    )


@customers_bp.route("/<int:customer_id>/log", methods=["POST"])
@management_required
def log_communication(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    summary = form_str("summary")
    if not summary:
        flash("Add a short summary before saving the note.", "danger")
    else:
        db.session.add(
            CommunicationLog(
                customer_id=customer.id,
                channel=form_str("channel", "call", 30),
                summary=summary,
                logged_by_id=current_user.id,
            )
        )
        audit("logged contact", "Customer", customer.id, summary[:80])
        db.session.commit()
        flash("Communication logged.", "success")
    return redirect(url_for("customers.detail", customer_id=customer.id))


def _populate(customer):
    customer.company_name = form_str("company_name", max_length=160)
    customer.contact_name = form_str("contact_name", max_length=120)
    customer.email = form_str("email", max_length=160)
    customer.phone = form_str("phone", max_length=40)
    customer.address = form_str("address", max_length=255)
    customer.city = form_str("city", max_length=80)
    customer.branch = form_str("branch", max_length=80)
    customer.status = form_str("status", "active", 20)
    customer.notes = form_str("notes")

    if not customer.company_name:
        return "Company name is required."
    if customer.status not in CUSTOMER_STATUSES:
        return "Choose a valid status."
    return None
