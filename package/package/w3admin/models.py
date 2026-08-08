"""Domain model for the photocopier leasing management system.

Entity map (see requirements doc s.4):
    User            -> Admin / Sales Manager / Technician accounts
    Customer        -> leasing clients
    Machine         -> copier units, in stock or leased out
    Contract        -> lease agreement linking a customer to a machine
    MeterReading    -> periodic copy counts used for pay-per-copy billing
    Invoice/Line    -> billing documents raised against a contract
    Payment         -> money received against an invoice
    ServiceTicket   -> maintenance job assigned to a technician
    Part/PartUsage  -> consumables stock and its consumption on a job
    AuditLog        -> who created/updated what and when
    Setting         -> company + billing configuration
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

ROLE_ADMIN = "admin"
ROLE_SALES = "sales"
ROLE_TECH = "technician"

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_SALES: "Sales Manager",
    ROLE_TECH: "Technician",
}


def utcnow():
    return datetime.utcnow()


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(40))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_SALES)
    job_title = db.Column(db.String(120))
    active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)

    tickets = db.relationship(
        "ServiceTicket",
        back_populates="technician",
        foreign_keys="ServiceTicket.assigned_to_id",
    )

    # -- auth helpers ------------------------------------------------------- #
    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self):  # Flask-Login honours this on login
        return self.active

    # -- role helpers ------------------------------------------------------- #
    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.title())

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_sales(self):
        return self.role == ROLE_SALES

    @property
    def is_technician(self):
        return self.role == ROLE_TECH

    @property
    def initials(self):
        parts = [p for p in (self.name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #

CUSTOMER_STATUSES = ("active", "prospect", "inactive")


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    company_name = db.Column(db.String(160), nullable=False)
    contact_name = db.Column(db.String(120))
    email = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    address = db.Column(db.String(255))
    city = db.Column(db.String(80))
    branch = db.Column(db.String(80))
    status = db.Column(db.String(20), default="active", nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    machines = db.relationship("Machine", back_populates="customer")
    contracts = db.relationship(
        "Contract", back_populates="customer", cascade="all, delete-orphan"
    )
    invoices = db.relationship(
        "Invoice", back_populates="customer", cascade="all, delete-orphan"
    )
    tickets = db.relationship(
        "ServiceTicket", back_populates="customer", cascade="all, delete-orphan"
    )
    communications = db.relationship(
        "CommunicationLog",
        back_populates="customer",
        cascade="all, delete-orphan",
        order_by="CommunicationLog.happened_at.desc()",
    )

    @property
    def active_contracts(self):
        return [c for c in self.contracts if c.status == "active"]

    @property
    def outstanding_balance(self):
        return sum((inv.balance for inv in self.invoices if inv.is_outstanding), Decimal("0"))

    @property
    def display(self):
        return f"{self.code} · {self.company_name}"

    def __repr__(self):
        return f"<Customer {self.code}>"


class CommunicationLog(db.Model):
    """Free-form contact history against a customer (calls, emails, visits)."""

    __tablename__ = "communication_logs"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    channel = db.Column(db.String(30), default="call", nullable=False)
    summary = db.Column(db.Text, nullable=False)
    happened_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    logged_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer", back_populates="communications")
    logged_by = db.relationship("User")


# --------------------------------------------------------------------------- #
# Machines
# --------------------------------------------------------------------------- #

MACHINE_STATUSES = ("in_stock", "leased", "maintenance", "retired")
MACHINE_STATUS_LABELS = {
    "in_stock": "In Stock",
    "leased": "Leased Out",
    "maintenance": "In Maintenance",
    "retired": "Retired",
}
MACHINE_CONDITIONS = ("new", "refurbished", "used", "faulty")


class Machine(TimestampMixin, db.Model):
    __tablename__ = "machines"

    id = db.Column(db.Integer, primary_key=True)
    asset_tag = db.Column(db.String(30), unique=True, nullable=False, index=True)
    manufacturer = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    serial_number = db.Column(db.String(80), unique=True, nullable=False)
    condition = db.Column(db.String(20), default="new", nullable=False)
    status = db.Column(db.String(20), default="in_stock", nullable=False)
    supports_colour = db.Column(db.Boolean, default=True, nullable=False)
    location = db.Column(db.String(160))
    purchase_date = db.Column(db.Date)
    purchase_cost = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)

    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    customer = db.relationship("Customer", back_populates="machines")

    contracts = db.relationship("Contract", back_populates="machine")
    readings = db.relationship(
        "MeterReading",
        back_populates="machine",
        cascade="all, delete-orphan",
        order_by="MeterReading.reading_date.desc()",
    )
    tickets = db.relationship("ServiceTicket", back_populates="machine")

    @property
    def status_label(self):
        return MACHINE_STATUS_LABELS.get(self.status, self.status.title())

    @property
    def active_contract(self):
        for c in self.contracts:
            if c.status == "active":
                return c
        return None

    @property
    def latest_reading(self):
        return self.readings[0] if self.readings else None

    @property
    def display(self):
        return f"{self.asset_tag} · {self.manufacturer} {self.model}"

    def __repr__(self):
        return f"<Machine {self.asset_tag}>"


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #

BILLING_TYPES = ("flat", "per_copy", "hybrid")
BILLING_TYPE_LABELS = {
    "flat": "Flat monthly fee",
    "per_copy": "Pay per copy",
    "hybrid": "Flat fee + per copy",
}
CONTRACT_STATUSES = ("draft", "active", "expired", "terminated")


class Contract(TimestampMixin, db.Model):
    __tablename__ = "contracts"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"), nullable=False)

    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    billing_type = db.Column(db.String(20), default="flat", nullable=False)
    billing_day = db.Column(db.Integer, default=1, nullable=False)  # day of month
    flat_monthly_fee = db.Column(db.Numeric(12, 2), default=0)
    mono_rate = db.Column(db.Numeric(10, 4), default=0)  # per copy
    colour_rate = db.Column(db.Numeric(10, 4), default=0)
    included_mono = db.Column(db.Integer, default=0)  # free copies per cycle
    included_colour = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="active", nullable=False)
    terminated_on = db.Column(db.Date)
    termination_reason = db.Column(db.String(255))
    renewed_from_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer", back_populates="contracts")
    machine = db.relationship("Machine", back_populates="contracts")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    renewed_from = db.relationship("Contract", remote_side=[id])
    invoices = db.relationship("Invoice", back_populates="contract")
    readings = db.relationship("MeterReading", back_populates="contract")

    @property
    def billing_type_label(self):
        return BILLING_TYPE_LABELS.get(self.billing_type, self.billing_type)

    @property
    def days_to_expiry(self):
        return (self.end_date - date.today()).days

    @property
    def is_expiring_soon(self):
        return self.status == "active" and 0 <= self.days_to_expiry <= 30

    @property
    def is_expired(self):
        return self.status == "active" and self.days_to_expiry < 0

    @property
    def charges_per_copy(self):
        return self.billing_type in ("per_copy", "hybrid")

    @property
    def monthly_value(self):
        """Indicative monthly value used for revenue forecasting."""
        return Decimal(self.flat_monthly_fee or 0)

    def __repr__(self):
        return f"<Contract {self.reference}>"


# --------------------------------------------------------------------------- #
# Meter readings
# --------------------------------------------------------------------------- #

READING_SOURCES = ("technician", "customer", "sales", "remote")


class MeterReading(db.Model):
    __tablename__ = "meter_readings"

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    reading_date = db.Column(db.Date, nullable=False, default=date.today)
    mono_count = db.Column(db.Integer, default=0, nullable=False)
    colour_count = db.Column(db.Integer, default=0, nullable=False)
    source = db.Column(db.String(20), default="technician", nullable=False)
    billed = db.Column(db.Boolean, default=False, nullable=False)
    notes = db.Column(db.String(255))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    machine = db.relationship("Machine", back_populates="readings")
    contract = db.relationship("Contract", back_populates="readings")
    recorded_by = db.relationship("User")

    @property
    def total_count(self):
        return (self.mono_count or 0) + (self.colour_count or 0)

    def __repr__(self):
        return f"<MeterReading m{self.machine_id} {self.reading_date}>"


# --------------------------------------------------------------------------- #
# Invoicing & payments
# --------------------------------------------------------------------------- #

INVOICE_STATUSES = ("draft", "sent", "part_paid", "paid", "overdue", "cancelled")
INVOICE_STATUS_LABELS = {
    "draft": "Draft",
    "sent": "Sent",
    "part_paid": "Part Paid",
    "paid": "Paid",
    "overdue": "Overdue",
    "cancelled": "Cancelled",
}
PAYMENT_METHODS = ("mobile_money", "bank_transfer", "cash", "cheque", "card")
PAYMENT_METHOD_LABELS = {
    "mobile_money": "Mobile Money",
    "bank_transfer": "Bank Transfer",
    "cash": "Cash",
    "cheque": "Cheque",
    "card": "Card",
}


class Invoice(TimestampMixin, db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))

    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    status = db.Column(db.String(20), default="draft", nullable=False)
    tax_rate = db.Column(db.Numeric(5, 2), default=0)
    notes = db.Column(db.Text)
    sent_at = db.Column(db.DateTime)
    last_reminder_at = db.Column(db.DateTime)
    reminder_count = db.Column(db.Integer, default=0, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer", back_populates="invoices")
    contract = db.relationship("Contract", back_populates="invoices")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    lines = db.relationship(
        "InvoiceLine", back_populates="invoice", cascade="all, delete-orphan"
    )
    payments = db.relationship(
        "Payment", back_populates="invoice", cascade="all, delete-orphan"
    )

    # -- money -------------------------------------------------------------- #
    @property
    def subtotal(self):
        return sum((line.amount for line in self.lines), Decimal("0"))

    @property
    def tax_amount(self):
        return (self.subtotal * Decimal(self.tax_rate or 0) / Decimal("100")).quantize(
            Decimal("0.01")
        )

    @property
    def total(self):
        return (self.subtotal + self.tax_amount).quantize(Decimal("0.01"))

    @property
    def amount_paid(self):
        return sum((p.amount for p in self.payments), Decimal("0"))

    @property
    def balance(self):
        return (self.total - self.amount_paid).quantize(Decimal("0.01"))

    # -- state -------------------------------------------------------------- #
    @property
    def is_outstanding(self):
        return self.status in ("sent", "part_paid", "overdue")

    @property
    def days_overdue(self):
        if not self.is_outstanding:
            return 0
        return max((date.today() - self.due_date).days, 0)

    @property
    def status_label(self):
        return INVOICE_STATUS_LABELS.get(self.status, self.status.title())

    @property
    def status_class(self):
        return {
            "draft": "secondary",
            "sent": "primary",
            "part_paid": "info",
            "paid": "success",
            "overdue": "danger",
            "cancelled": "dark",
        }.get(self.status, "secondary")

    def recalculate_status(self):
        """Keep invoice status in step with payments and the due date."""
        if self.status in ("draft", "cancelled"):
            return
        paid = self.amount_paid
        if paid >= self.total and self.total > 0:
            self.status = "paid"
        elif paid > 0:
            self.status = "part_paid" if date.today() <= self.due_date else "overdue"
        elif date.today() > self.due_date:
            self.status = "overdue"
        else:
            self.status = "sent"

    def __repr__(self):
        return f"<Invoice {self.number}>"


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), default=1, nullable=False)
    unit_price = db.Column(db.Numeric(12, 4), default=0, nullable=False)
    kind = db.Column(db.String(20), default="other")  # rental | mono | colour | part

    invoice = db.relationship("Invoice", back_populates="lines")

    @property
    def amount(self):
        return (Decimal(self.quantity or 0) * Decimal(self.unit_price or 0)).quantize(
            Decimal("0.01")
        )


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_on = db.Column(db.Date, nullable=False, default=date.today)
    method = db.Column(db.String(30), default="bank_transfer", nullable=False)
    reference = db.Column(db.String(80))
    notes = db.Column(db.String(255))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    invoice = db.relationship("Invoice", back_populates="payments")
    recorded_by = db.relationship("User")

    @property
    def method_label(self):
        return PAYMENT_METHOD_LABELS.get(self.method, self.method.title())


# --------------------------------------------------------------------------- #
# Service & maintenance
# --------------------------------------------------------------------------- #

TICKET_STATUSES = ("open", "assigned", "in_progress", "resolved", "closed")
TICKET_STATUS_LABELS = {
    "open": "Open",
    "assigned": "Assigned",
    "in_progress": "In Progress",
    "resolved": "Resolved",
    "closed": "Closed",
}
TICKET_PRIORITIES = ("low", "normal", "high", "critical")


class ServiceTicket(TimestampMixin, db.Model):
    __tablename__ = "service_tickets"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"))
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default="normal", nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    scheduled_for = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)
    resolution = db.Column(db.Text)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    logged_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer", back_populates="tickets")
    machine = db.relationship("Machine", back_populates="tickets")
    technician = db.relationship(
        "User", back_populates="tickets", foreign_keys=[assigned_to_id]
    )
    logged_by = db.relationship("User", foreign_keys=[logged_by_id])
    part_usages = db.relationship(
        "PartUsage", back_populates="ticket", cascade="all, delete-orphan"
    )

    @property
    def status_label(self):
        return TICKET_STATUS_LABELS.get(self.status, self.status.title())

    @property
    def status_class(self):
        return {
            "open": "warning",
            "assigned": "info",
            "in_progress": "primary",
            "resolved": "success",
            "closed": "secondary",
        }.get(self.status, "secondary")

    @property
    def priority_class(self):
        return {
            "low": "secondary",
            "normal": "info",
            "high": "warning",
            "critical": "danger",
        }.get(self.priority, "secondary")

    @property
    def is_open(self):
        return self.status in ("open", "assigned", "in_progress")

    @property
    def age_days(self):
        return (utcnow() - self.created_at).days

    @property
    def parts_cost(self):
        return sum((u.line_cost for u in self.part_usages), Decimal("0"))

    def __repr__(self):
        return f"<ServiceTicket {self.reference}>"


# --------------------------------------------------------------------------- #
# Consumables / parts inventory
# --------------------------------------------------------------------------- #

PART_CATEGORIES = ("toner", "drum", "part", "consumable")


class Part(TimestampMixin, db.Model):
    __tablename__ = "parts"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(30), default="part", nullable=False)
    unit = db.Column(db.String(20), default="unit")
    quantity_in_stock = db.Column(db.Integer, default=0, nullable=False)
    reorder_level = db.Column(db.Integer, default=5, nullable=False)
    unit_cost = db.Column(db.Numeric(12, 2), default=0)
    charge_to_customer = db.Column(db.Boolean, default=False, nullable=False)
    supplier = db.Column(db.String(120))
    notes = db.Column(db.String(255))

    usages = db.relationship("PartUsage", back_populates="part")

    @property
    def is_low_stock(self):
        return self.quantity_in_stock <= self.reorder_level

    @property
    def stock_value(self):
        return (Decimal(self.quantity_in_stock) * Decimal(self.unit_cost or 0)).quantize(
            Decimal("0.01")
        )

    def __repr__(self):
        return f"<Part {self.sku}>"


class PartUsage(db.Model):
    __tablename__ = "part_usages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("service_tickets.id"), nullable=False)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_cost = db.Column(db.Numeric(12, 2), default=0)
    used_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    ticket = db.relationship("ServiceTicket", back_populates="part_usages")
    part = db.relationship("Part", back_populates="usages")
    recorded_by = db.relationship("User")

    @property
    def line_cost(self):
        return (Decimal(self.quantity or 0) * Decimal(self.unit_cost or 0)).quantize(
            Decimal("0.01")
        )


# --------------------------------------------------------------------------- #
# Audit trail & settings
# --------------------------------------------------------------------------- #


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(40), nullable=False)  # created | updated | deleted ...
    entity = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.Integer)
    detail = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")


class Setting(db.Model):
    """Simple key/value store for company + billing configuration."""

    __tablename__ = "settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(500))

    DEFAULTS = {
        "company_name": "Zohar",
        "company_address": "12 Ring Road East, Accra",
        "company_email": "billing@copytrack.example",
        "company_phone": "+233 30 000 0000",
        "currency_symbol": "GHS ",
        "default_tax_rate": "0",
        "payment_terms_days": "14",
        "contract_alert_days": "30",
        "invoice_prefix": "INV",
    }

    @classmethod
    def get(cls, key, default=None):
        row = db.session.get(cls, key)
        if row and row.value is not None:
            return row.value
        return default if default is not None else cls.DEFAULTS.get(key, "")

    @classmethod
    def get_int(cls, key, default=0):
        try:
            return int(cls.get(key))
        except (TypeError, ValueError):
            return default

    @classmethod
    def get_decimal(cls, key, default=Decimal("0")):
        try:
            return Decimal(str(cls.get(key)))
        except Exception:
            return default

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value = str(value)

    @classmethod
    def as_dict(cls):
        data = dict(cls.DEFAULTS)
        for row in cls.query.all():
            if row.value is not None:
                data[row.key] = row.value
        return data


# --------------------------------------------------------------------------- #
# Reference generators
# --------------------------------------------------------------------------- #


def _next_sequence(model, column, prefix, width=4):
    """Return the next `PREFIX-0001` style reference for a model."""
    like = f"{prefix}-%"
    last = (
        db.session.query(func.max(column))
        .filter(column.like(like))
        .scalar()
    )
    seq = 1
    if last:
        tail = str(last).rsplit("-", 1)[-1]
        if tail.isdigit():
            seq = int(tail) + 1
    return f"{prefix}-{seq:0{width}d}"


def next_customer_code():
    return _next_sequence(Customer, Customer.code, "CUS")


def next_asset_tag():
    return _next_sequence(Machine, Machine.asset_tag, "MC")


def next_contract_reference():
    return _next_sequence(Contract, Contract.reference, "LSE")


def next_ticket_reference():
    return _next_sequence(ServiceTicket, ServiceTicket.reference, "TKT")


def next_invoice_number():
    prefix = f"{Setting.get('invoice_prefix', 'INV')}-{date.today():%Y}"
    return _next_sequence(Invoice, Invoice.number, prefix)


def default_due_date(issue=None):
    issue = issue or date.today()
    return issue + timedelta(days=Setting.get_int("payment_terms_days", 14))
