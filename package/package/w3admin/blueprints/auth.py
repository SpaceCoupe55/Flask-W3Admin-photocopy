"""Authentication: secure per-user login (requirements s.6)."""

from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User
from ..security import audit
from ..utils import form_str

auth_bp = Blueprint("auth", __name__)


def _safe_next(target):
    """Only follow relative redirects — never an off-site 'next' parameter."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target if target.startswith("/") else None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        email = (form_str("email") or "").lower()
        password = request.form.get("password") or ""
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
        elif not user.active:
            flash("This account has been deactivated. Contact an administrator.", "danger")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            user.last_login_at = datetime.utcnow()
            audit("logged in", "User", user.id)
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or url_for("dashboard.home"))

    return render_template("leasing/auth/login.html", hide_chrome=True)


@auth_bp.route("/logout")
@login_required
def logout():
    audit("logged out", "User", current_user.id)
    db.session.commit()
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = form_str("name", current_user.name, 120)
        current_user.phone = form_str("phone", None, 40)
        current_user.job_title = form_str("job_title", None, 120)

        new_password = request.form.get("new_password") or ""
        if new_password:
            if not current_user.check_password(request.form.get("current_password") or ""):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("auth.profile"))
            if len(new_password) < 8:
                flash("New password must be at least 8 characters.", "danger")
                return redirect(url_for("auth.profile"))
            current_user.set_password(new_password)
            flash("Password updated.", "success")

        audit("updated", "User", current_user.id, "own profile")
        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("leasing/auth/profile.html", page_title="My Profile")
