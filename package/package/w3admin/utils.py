"""Small form-parsing helpers shared by the blueprints."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import request


def form_str(name, default=None, max_length=None):
    value = (request.form.get(name) or "").strip()
    if not value:
        return default
    return value[:max_length] if max_length else value


def form_int(name, default=0):
    raw = (request.form.get(name) or "").strip().replace(",", "")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def form_decimal(name, default=Decimal("0")):
    raw = (request.form.get(name) or "").strip().replace(",", "")
    try:
        return Decimal(raw)
    except (TypeError, ValueError, InvalidOperation):
        return default


def form_date(name, default=None):
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return default


def form_datetime(name, default=None):
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return default


def form_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


def arg_str(name, default=""):
    return (request.args.get(name) or default).strip()


def add_months(source, months):
    """Date `months` after `source`, clamped to the end of the target month."""
    month = source.month - 1 + months
    year = source.year + month // 12
    month = month % 12 + 1
    day = min(
        source.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day)
