"""Role-based access control and audit-trail helpers."""

from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from .extensions import db
from .models import ROLE_ADMIN, ROLE_SALES, ROLE_TECH, AuditLog

#: Convenience groupings used by the blueprints.
MANAGEMENT = (ROLE_ADMIN, ROLE_SALES)
ALL_ROLES = (ROLE_ADMIN, ROLE_SALES, ROLE_TECH)


def roles_required(*roles):
    """Allow the view only for the listed roles (admin always passes)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.full_path))
            if current_user.role not in roles and current_user.role != ROLE_ADMIN:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(view):
    return roles_required(ROLE_ADMIN)(view)


def management_required(view):
    """Admin or Sales Manager — the commercial side of the business."""
    return roles_required(*MANAGEMENT)(view)


def technician_or_management(view):
    return roles_required(ROLE_ADMIN, ROLE_SALES, ROLE_TECH)(view)


def can_edit_billing():
    return current_user.is_authenticated and current_user.role in MANAGEMENT


def can_manage_users():
    return current_user.is_authenticated and current_user.is_admin


def owns_ticket(ticket):
    """Technicians may only touch tickets assigned to them."""
    if not current_user.is_authenticated:
        return False
    if current_user.role in MANAGEMENT:
        return True
    return ticket.assigned_to_id == current_user.id


def audit(action, entity, entity_id=None, detail=None):
    """Record who did what, when (requirements s.6 — basic audit trail)."""
    db.session.add(
        AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=(detail or "")[:255] or None,
        )
    )
