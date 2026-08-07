"""Invoicing, payment tracking and reminders (requirements s.4, s.5.2)."""

from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    INVOICE_STATUSES,
    PAYMENT_METHODS,
    PAYMENT_METHOD_LABELS,
    Contract,
    Customer,
    Invoice,
    InvoiceLine,
    Setting,
    default_due_date,
    next_invoice_number,
)
from ..security import management_required, audit
from ..services import (
    apply_payment,
    build_invoice_for_contract,
    mark_invoice_sent,
    refresh_overdue_invoices,
    unbilled_volume,
)
from ..utils import add_months, arg_str, form_date, form_decimal, form_str

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


@billing_bp.route("/invoices")
@management_required
def invoices():
    refresh_overdue_invoices()
    status = arg_str("status")
    search = arg_str("q")

    query = Invoice.query.join(Customer)
    if status == "outstanding":
        query = query.filter(Invoice.status.in_(("sent", "part_paid", "overdue")))
    elif status:
        query = query.filter(Invoice.status == status)
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(Invoice.number.ilike(like), Customer.company_name.ilike(like))
        )

    rows = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    totals = {
        "count": len(rows),
        "billed": sum((i.total for i in rows), Decimal("0")),
        "paid": sum((i.amount_paid for i in rows), Decimal("0")),
        "due": sum((i.balance for i in rows if i.is_outstanding), Decimal("0")),
    }
    return render_template(
        "leasing/billing/list.html",
        page_title="Invoices",
        invoices=rows,
        statuses=INVOICE_STATUSES,
        status=status,
        search=search,
        totals=totals,
    )


@billing_bp.route("/invoices/<int:invoice_id>")
@management_required
def detail(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    return render_template(
        "leasing/billing/detail.html",
        page_title=f"Invoice {invoice.number}",
        invoice=invoice,
        methods=PAYMENT_METHODS,
        method_labels=PAYMENT_METHOD_LABELS,
        today=date.today(),
        company=Setting.as_dict(),
    )


@billing_bp.route("/invoices/<int:invoice_id>/print")
@management_required
def print_invoice(invoice_id):
    """Print / save-as-PDF view (requirements s.6 — export/print support)."""
    invoice = db.get_or_404(Invoice, invoice_id)
    return render_template(
        "leasing/billing/print.html", invoice=invoice, company=Setting.as_dict()
    )


# --------------------------------------------------------------------------- #
# Generating invoices
# --------------------------------------------------------------------------- #


@billing_bp.route("/invoices/generate", methods=["GET", "POST"])
@management_required
def generate():
    contracts = (
        Contract.query.filter(Contract.status == "active")
        .order_by(Contract.reference)
        .all()
    )

    if request.method == "POST":
        contract = db.session.get(Contract, int(request.form.get("contract_id") or 0))
        if contract is None:
            flash("Select a lease to bill.", "danger")
            return redirect(url_for("billing.generate"))

        period_start = form_date("period_start", date.today().replace(day=1))
        period_end = form_date("period_end", add_months(period_start, 1))

        invoice = build_invoice_for_contract(
            contract, period_start, period_end, created_by=current_user
        )
        if not invoice.lines:
            db.session.rollback()
            flash(
                "Nothing to bill for that period — a per-copy lease needs a newer "
                "meter reading than the last billed one.",
                "warning",
            )
            return redirect(url_for("billing.generate"))

        db.session.flush()
        audit(
            "generated",
            "Invoice",
            invoice.id,
            f"{invoice.number} for {contract.reference}",
        )
        db.session.commit()
        flash(f"Draft invoice {invoice.number} created.", "success")
        return redirect(url_for("billing.detail", invoice_id=invoice.id))

    previews = []
    for contract in contracts:
        mono, colour, _, current = unbilled_volume(contract)
        previews.append(
            {
                "contract": contract,
                "mono": mono,
                "colour": colour,
                "last_reading": current,
                "billable": contract.billing_type == "flat"
                or bool(mono or colour),
            }
        )

    start = date.today().replace(day=1)
    return render_template(
        "leasing/billing/generate.html",
        page_title="Generate Invoices",
        previews=previews,
        period_start=start,
        period_end=add_months(start, 1),
    )


@billing_bp.route("/invoices/new", methods=["GET", "POST"])
@management_required
def create_manual():
    if request.method == "POST":
        customer = db.session.get(Customer, int(request.form.get("customer_id") or 0))
        if customer is None:
            flash("Select a customer.", "danger")
            return redirect(url_for("billing.create_manual"))

        invoice = Invoice(
            number=next_invoice_number(),
            customer_id=customer.id,
            contract_id=int(request.form.get("contract_id") or 0) or None,
            issue_date=form_date("issue_date", date.today()),
            due_date=form_date("due_date", default_due_date()),
            status="draft",
            tax_rate=form_decimal("tax_rate", Setting.get_decimal("default_tax_rate")),
            notes=form_str("notes"),
            created_by_id=current_user.id,
        )

        descriptions = request.form.getlist("line_description")
        quantities = request.form.getlist("line_quantity")
        prices = request.form.getlist("line_price")
        for i, description in enumerate(descriptions):
            description = (description or "").strip()
            if not description:
                continue
            try:
                qty = Decimal((quantities[i] or "1").replace(",", ""))
                price = Decimal((prices[i] or "0").replace(",", ""))
            except Exception:
                continue
            invoice.lines.append(
                InvoiceLine(
                    description=description[:255],
                    quantity=qty,
                    unit_price=price,
                    kind="other",
                )
            )

        if not invoice.lines:
            flash("Add at least one invoice line.", "danger")
            return redirect(url_for("billing.create_manual"))

        db.session.add(invoice)
        db.session.flush()
        audit("created", "Invoice", invoice.id, invoice.number)
        db.session.commit()
        flash(f"Invoice {invoice.number} created.", "success")
        return redirect(url_for("billing.detail", invoice_id=invoice.id))

    return render_template(
        "leasing/billing/form.html",
        page_title="New Invoice",
        customers=Customer.query.order_by(Customer.company_name).all(),
        contracts=Contract.query.filter_by(status="active").order_by(Contract.reference).all(),
        today=date.today(),
        due=default_due_date(),
        tax_rate=Setting.get_decimal("default_tax_rate"),
    )


# --------------------------------------------------------------------------- #
# Invoice lifecycle
# --------------------------------------------------------------------------- #


@billing_bp.route("/invoices/<int:invoice_id>/send", methods=["POST"])
@management_required
def send(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    mark_invoice_sent(invoice)
    audit("sent", "Invoice", invoice.id, invoice.number)
    db.session.commit()
    flash(f"Invoice {invoice.number} marked as sent to {invoice.customer.company_name}.", "success")
    return redirect(url_for("billing.detail", invoice_id=invoice.id))


@billing_bp.route("/invoices/<int:invoice_id>/remind", methods=["POST"])
@management_required
def remind(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    invoice.last_reminder_at = datetime.utcnow()
    invoice.reminder_count = (invoice.reminder_count or 0) + 1
    audit("reminder sent", "Invoice", invoice.id, f"reminder #{invoice.reminder_count}")
    db.session.commit()
    flash(
        f"Payment reminder #{invoice.reminder_count} logged for {invoice.number}.",
        "success",
    )
    return redirect(url_for("billing.detail", invoice_id=invoice.id))


@billing_bp.route("/invoices/<int:invoice_id>/cancel", methods=["POST"])
@management_required
def cancel(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.payments:
        flash("Invoices with recorded payments cannot be cancelled.", "danger")
    else:
        invoice.status = "cancelled"
        audit("cancelled", "Invoice", invoice.id, invoice.number)
        db.session.commit()
        flash(f"Invoice {invoice.number} cancelled.", "success")
    return redirect(url_for("billing.detail", invoice_id=invoice.id))


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #


@billing_bp.route("/invoices/<int:invoice_id>/payments", methods=["POST"])
@management_required
def record_payment(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    amount = form_decimal("amount")

    if amount <= 0:
        flash("Enter a payment amount greater than zero.", "danger")
    elif amount > invoice.balance:
        flash(
            f"That is more than the outstanding balance of {invoice.balance:,.2f}.",
            "danger",
        )
    else:
        apply_payment(
            invoice,
            amount,
            form_date("paid_on", date.today()),
            form_str("method", "bank_transfer", 30),
            form_str("reference", max_length=80),
            current_user,
            form_str("notes", max_length=255),
        )
        audit("payment recorded", "Invoice", invoice.id, f"{amount:,.2f} on {invoice.number}")
        db.session.commit()
        flash(f"Payment of {amount:,.2f} recorded. Invoice is now {invoice.status_label}.", "success")
    return redirect(url_for("billing.detail", invoice_id=invoice.id))


@billing_bp.route("/payments")
@management_required
def payments():
    from ..models import Payment

    rows = (
        Payment.query.order_by(Payment.paid_on.desc(), Payment.id.desc()).limit(300).all()
    )
    return render_template(
        "leasing/billing/payments.html",
        page_title="Payments Received",
        payments=rows,
        method_labels=PAYMENT_METHOD_LABELS,
        total=sum((p.amount for p in rows), Decimal("0")),
    )
