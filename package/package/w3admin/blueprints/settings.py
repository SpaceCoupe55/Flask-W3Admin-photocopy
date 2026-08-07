"""Admin area: user & role management, system settings, audit trail (s.4, s.6)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    ROLE_LABELS,
    AuditLog,
    ServiceTicket,
    Setting,
    User,
)
from ..security import admin_required, audit
from ..utils import arg_str, form_bool, form_str

settings_bp = Blueprint("settings", __name__, url_prefix="/admin")


# --------------------------------------------------------------------------- #
# Users & roles
# --------------------------------------------------------------------------- #


@settings_bp.route("/users")
@admin_required
def users():
    role = arg_str("role")
    query = User.query
    if role:
        query = query.filter(User.role == role)
    return render_template(
        "leasing/admin/users.html",
        page_title="Users & Roles",
        users=query.order_by(User.name).all(),
        roles=ROLE_LABELS,
        role=role,
    )


@settings_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def create_user():
    if request.method == "POST":
        user = User()
        error = _populate_user(user, new=True)
        if error:
            flash(error, "danger")
        else:
            db.session.add(user)
            db.session.flush()
            audit("created", "User", user.id, f"{user.name} ({user.role_label})")
            db.session.commit()
            flash(f"{user.name} can now sign in as {user.role_label}.", "success")
            return redirect(url_for("settings.users"))

    return render_template(
        "leasing/admin/user_form.html",
        page_title="New User",
        user=User(role="sales", active=True),
        roles=ROLE_LABELS,
        is_new=True,
    )


@settings_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == "POST":
        error = _populate_user(user, new=False)
        if error:
            flash(error, "danger")
        else:
            audit("updated", "User", user.id, f"{user.name} ({user.role_label})")
            db.session.commit()
            flash("User updated.", "success")
            return redirect(url_for("settings.users"))

    return render_template(
        "leasing/admin/user_form.html",
        page_title=f"Edit {user.name}",
        user=user,
        roles=ROLE_LABELS,
        is_new=False,
    )


@settings_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)

    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("settings.users"))

    if user.active:
        open_jobs = ServiceTicket.query.filter(
            ServiceTicket.assigned_to_id == user.id,
            ServiceTicket.status.in_(("assigned", "in_progress")),
        ).count()
        if open_jobs:
            flash(
                f"{user.name} still has {open_jobs} open job(s). Reassign them first.",
                "danger",
            )
            return redirect(url_for("settings.users"))

    user.active = not user.active
    audit("deactivated" if not user.active else "reactivated", "User", user.id, user.name)
    db.session.commit()
    flash(
        f"{user.name} has been {'reactivated' if user.active else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("settings.users"))


def _populate_user(user, new):
    user.name = form_str("name", max_length=120)
    user.email = (form_str("email", max_length=160) or "").lower() or None
    user.phone = form_str("phone", max_length=40)
    user.job_title = form_str("job_title", max_length=120)
    user.role = form_str("role", "sales", 20)
    user.active = form_bool("active") if not new else True

    password = request.form.get("password") or ""
    if new and len(password) < 8:
        return "Set an initial password of at least 8 characters."
    if password:
        if len(password) < 8:
            return "Password must be at least 8 characters."
        user.set_password(password)

    if not user.name:
        return "Name is required."
    if not user.email or "@" not in user.email:
        return "A valid email address is required."
    if user.role not in ROLE_LABELS:
        return "Choose a valid role."

    clash = User.query.filter(User.email == user.email, User.id != user.id).first()
    if clash:
        return "That email address is already in use."
    return None


# --------------------------------------------------------------------------- #
# System settings
# --------------------------------------------------------------------------- #

EDITABLE_SETTINGS = (
    "company_name",
    "company_address",
    "company_email",
    "company_phone",
    "currency_symbol",
    "default_tax_rate",
    "payment_terms_days",
    "contract_alert_days",
    "invoice_prefix",
)


@settings_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def system_settings():
    if request.method == "POST":
        for key in EDITABLE_SETTINGS:
            if key in request.form:
                Setting.set(key, (request.form.get(key) or "").strip())
        audit("updated", "Setting", None, "system settings")
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("settings.system_settings"))

    return render_template(
        "leasing/admin/settings.html",
        page_title="System Settings",
        settings=Setting.as_dict(),
    )


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #


@settings_bp.route("/audit")
@admin_required
def audit_log():
    entity = arg_str("entity")
    query = AuditLog.query
    if entity:
        query = query.filter(AuditLog.entity == entity)

    entries = query.order_by(AuditLog.created_at.desc()).limit(400).all()
    entities = [
        row[0]
        for row in db.session.query(AuditLog.entity).distinct().order_by(AuditLog.entity)
    ]
    return render_template(
        "leasing/admin/audit.html",
        page_title="Audit Trail",
        entries=entries,
        entities=entities,
        entity=entity,
    )
