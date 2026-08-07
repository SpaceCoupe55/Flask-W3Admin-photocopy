"""Consumables / parts inventory (requirements s.4)."""

from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..extensions import db
from ..models import PART_CATEGORIES, Part, PartUsage
from ..security import management_required, audit
from ..utils import arg_str, form_bool, form_decimal, form_int, form_str

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


@inventory_bp.route("/parts")
@login_required
def parts():
    search = arg_str("q")
    category = arg_str("category")
    view = arg_str("view")

    query = Part.query
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Part.name.ilike(like), Part.sku.ilike(like)))
    if category:
        query = query.filter(Part.category == category)

    rows = query.order_by(Part.name.asc()).all()
    if view == "low":
        rows = [p for p in rows if p.is_low_stock]

    return render_template(
        "leasing/inventory/list.html",
        page_title="Parts & Consumables",
        parts=rows,
        categories=PART_CATEGORIES,
        category=category,
        search=search,
        view=view,
        low_count=len([p for p in Part.query.all() if p.is_low_stock]),
        stock_value=sum((p.stock_value for p in Part.query.all()), Decimal("0")),
    )


@inventory_bp.route("/parts/new", methods=["GET", "POST"])
@management_required
def create():
    if request.method == "POST":
        part = Part(sku=form_str("sku", max_length=40))
        error = _populate(part, new=True)
        if error:
            flash(error, "danger")
        else:
            db.session.add(part)
            db.session.flush()
            audit("created", "Part", part.id, part.name)
            db.session.commit()
            flash(f"{part.name} added to inventory.", "success")
            return redirect(url_for("inventory.parts"))

    return render_template(
        "leasing/inventory/form.html",
        page_title="New Part",
        part=Part(category="toner", unit="unit", reorder_level=5),
        categories=PART_CATEGORIES,
    )


@inventory_bp.route("/parts/<int:part_id>/edit", methods=["GET", "POST"])
@management_required
def edit(part_id):
    part = db.get_or_404(Part, part_id)
    if request.method == "POST":
        error = _populate(part, new=False)
        if error:
            flash(error, "danger")
        else:
            audit("updated", "Part", part.id, part.name)
            db.session.commit()
            flash("Part updated.", "success")
            return redirect(url_for("inventory.parts"))

    return render_template(
        "leasing/inventory/form.html",
        page_title=f"Edit {part.name}",
        part=part,
        categories=PART_CATEGORIES,
    )


@inventory_bp.route("/parts/<int:part_id>/stock", methods=["POST"])
@management_required
def adjust_stock(part_id):
    part = db.get_or_404(Part, part_id)
    delta = form_int("delta")
    reason = form_str("reason", "stock adjustment", 120)

    if delta == 0:
        flash("Enter a quantity to add or remove.", "danger")
    else:
        part.quantity_in_stock = max((part.quantity_in_stock or 0) + delta, 0)
        audit(
            "stock adjusted",
            "Part",
            part.id,
            f"{delta:+d} ({reason}) → {part.quantity_in_stock}",
        )
        db.session.commit()
        flash(
            f"{part.name} stock adjusted by {delta:+d} — now {part.quantity_in_stock}.",
            "success",
        )
    return redirect(request.referrer or url_for("inventory.parts"))


@inventory_bp.route("/usage")
@login_required
def usage():
    rows = (
        PartUsage.query.order_by(PartUsage.used_at.desc()).limit(300).all()
    )
    return render_template(
        "leasing/inventory/usage.html",
        page_title="Consumables Used",
        usages=rows,
        total_cost=sum((u.line_cost for u in rows), Decimal("0")),
    )


def _populate(part, new):
    part.name = form_str("name", max_length=160)
    part.category = form_str("category", "part", 30)
    part.unit = form_str("unit", "unit", 20)
    part.reorder_level = max(form_int("reorder_level", 5), 0)
    part.unit_cost = form_decimal("unit_cost")
    part.charge_to_customer = form_bool("charge_to_customer")
    part.supplier = form_str("supplier", max_length=120)
    part.notes = form_str("notes", max_length=255)
    if new:
        part.quantity_in_stock = max(form_int("quantity_in_stock"), 0)

    if not part.sku:
        return "A SKU / part code is required."
    if not part.name:
        return "Part name is required."
    if part.category not in PART_CATEGORIES:
        return "Choose a valid category."

    clash = Part.query.filter(Part.sku == part.sku, Part.id != part.id).first()
    if clash:
        return f"SKU {part.sku} is already used by {clash.name}."
    return None
