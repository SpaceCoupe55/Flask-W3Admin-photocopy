"""Feature blueprints for the photocopier leasing management system."""

from .auth import auth_bp
from .billing import billing_bp
from .contracts import contracts_bp
from .customers import customers_bp
from .dashboard import dashboard_bp
from .inventory import inventory_bp
from .machines import machines_bp
from .reports import reports_bp
from .service import service_bp
from .settings import settings_bp

ALL_BLUEPRINTS = (
    auth_bp,
    dashboard_bp,
    customers_bp,
    machines_bp,
    contracts_bp,
    billing_bp,
    service_bp,
    inventory_bp,
    reports_bp,
    settings_bp,
)
