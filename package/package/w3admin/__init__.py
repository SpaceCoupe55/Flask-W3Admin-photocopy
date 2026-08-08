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
PROJECT_ROOT = os.path.dirname(BASE_DIR)
INSTANCE_DIR = os.path.join(PROJECT_ROOT, "instance")
# Static assets live in the project-root ``public/`` directory so that on
# Vercel they are served directly from the CDN (files under ``public/`` are
# published at the site root) instead of through the Python function. Locally,
# Flask serves them from the same folder, keeping ``/static/...`` URLs identical
# in both environments.
STATIC_DIR = os.path.join(PROJECT_ROOT, "public", "static")


def create_app(config=None):
    app = Flask(
        __name__,
        instance_path=INSTANCE_DIR,
        static_folder=STATIC_DIR,
        static_url_path="/static",
    )
    # The instance dir is only needed for the local SQLite fallback. On a
    # read-only serverless filesystem (Vercel) creating it would raise, so treat
    # failure as non-fatal — production uses Postgres and writes nothing to disk.
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        pass

    # Vercel sets VERCEL=1 automatically; treat that (or an explicit
    # FLASK_ENV=production) as the signal to enforce production hardening.
    is_production = (
        os.environ.get("VERCEL") == "1"
        or os.environ.get("FLASK_ENV") == "production"
    )

    database_url = os.environ.get("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        # SQLAlchemy 2.0 only accepts the postgresql:// scheme.
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if not database_url:
        database_url = "sqlite:///" + os.path.join(app.instance_path, "leasing.db")

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if is_production:
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production."
            )
        secret_key = "dev-only-insecure-secret-change-me"

    app.config.update(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=is_production,
        SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "1") == "1",
    )

    if is_production and database_url.startswith("postgresql"):
        # Serverless invocations are short-lived and sit behind Supabase's
        # transaction pooler, so don't keep a client-side connection pool.
        from sqlalchemy.pool import NullPool

        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"poolclass": NullPool}

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
            "app_name": settings.get("company_name", "Zohar"),
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
