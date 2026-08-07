"""Photocopier Leasing Management System.

Built on the W3Admin Flask dashboard template — the layout, styling and
front-end vendor libraries are reused, while the modules, navigation and data
model follow the leasing business requirements (customers, machines, leases,
invoicing, meter billing, service tickets and parts inventory).
"""

import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, render_template
from flask_login import current_user

from .extensions import csrf, db, login_manager

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(os.path.dirname(BASE_DIR), "instance")


def create_app(config=None):
    app = Flask(__name__, instance_path=INSTANCE_DIR)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "oJew_hVN9dv46ZkLReHCVw"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + os.path.join(app.instance_path, "leasing.db"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "1") == "1",
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .models import User  # noqa: E402  (import after db is bound)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()
        if app.config["SEED_DEMO_DATA"]:
            from .seed import seed_if_empty

            seed_if_empty()

    return app


def _register_blueprints(app):
    from .blueprints import ALL_BLUEPRINTS
    from .routes import main as main_blueprint

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)
    app.register_blueprint(main_blueprint)


def _register_template_helpers(app):
    from .models import (
        BILLING_TYPE_LABELS,
        INVOICE_STATUS_LABELS,
        MACHINE_STATUS_LABELS,
        PAYMENT_METHOD_LABELS,
        ROLE_LABELS,
        TICKET_STATUS_LABELS,
        Setting,
    )
    from .services import alert_feed

    @app.context_processor
    def inject_globals():
        settings = Setting.as_dict()
        alerts = []
        if current_user.is_authenticated:
            alerts = alert_feed(current_user)
        return {
            "app_name": settings.get("company_name", "CopyTrack"),
            "settings": settings,
            "alerts": alerts,
            "today": date.today(),
            "now": datetime.utcnow(),
            "role_labels": ROLE_LABELS,
            "machine_status_labels": MACHINE_STATUS_LABELS,
            "invoice_status_labels": INVOICE_STATUS_LABELS,
            "ticket_status_labels": TICKET_STATUS_LABELS,
            "billing_type_labels": BILLING_TYPE_LABELS,
            "payment_method_labels": PAYMENT_METHOD_LABELS,
        }

    @app.template_filter("money")
    def money_filter(value):
        symbol = Setting.get("currency_symbol", "GHS ")
        try:
            amount = Decimal(str(value or 0))
        except Exception:
            amount = Decimal("0")
        return f"{symbol}{amount:,.2f}"

    @app.template_filter("qty")
    def qty_filter(value):
        try:
            return f"{int(value or 0):,}"
        except (TypeError, ValueError):
            return value

    @app.template_filter("rate")
    def rate_filter(value):
        symbol = Setting.get("currency_symbol", "GHS ")
        try:
            return f"{symbol}{Decimal(str(value or 0)):,.4f}"
        except Exception:
            return value

    @app.template_filter("options")
    def options_filter(values, labels=None):
        """Turn ('in_stock', …) into [('in_stock', 'In Stock'), …] for <select>."""
        labels = labels or {}
        return [
            (value, labels.get(value, str(value).replace("_", " ").title()))
            for value in values
        ]

    @app.template_filter("choices")
    def choices_filter(items, value_attr="id", label_attr="display"):
        """Turn model instances into (value, label) pairs for <select>."""
        return [(getattr(i, value_attr), getattr(i, label_attr)) for i in items]

    @app.template_filter("day")
    def day_filter(value, fmt="%d %b %Y"):
        if not value:
            return "—"
        return value.strftime(fmt)

    @app.template_filter("datetime")
    def datetime_filter(value, fmt="%d %b %Y, %H:%M"):
        if not value:
            return "—"
        return value.strftime(fmt)


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(error):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("500.html"), 500


def _register_cli(app):
    @app.cli.command("seed-demo")
    def seed_demo():
        """Populate the database with a realistic demo dataset."""
        from .seed import seed_if_empty

        created = seed_if_empty(force=True)
        print("Demo data created." if created else "Database already populated.")

    @app.cli.command("create-admin")
    def create_admin():
        """Create an administrator account interactively."""
        from .models import ROLE_ADMIN, User

        name = input("Full name: ").strip()
        email = input("Email: ").strip().lower()
        password = input("Password (min 8 chars): ").strip()
        if len(password) < 8:
            print("Password too short.")
            return
        if User.query.filter_by(email=email).first():
            print("That email is already registered.")
            return
        user = User(name=name, email=email, role=ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Administrator {email} created.")
