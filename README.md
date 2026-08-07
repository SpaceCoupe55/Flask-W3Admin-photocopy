# Photocopier Leasing Management System

A working management system for a copier leasing business, built by restructuring the
**W3Admin Flask** dashboard template. The template's layout, styling and front-end vendor
libraries are reused; its demo modules (e-commerce, blog, chat, email, UI kit, four sample
dashboards) were dropped in favour of the modules the business actually runs on.

## Run it

```bash
pip install -r package/package/requirements.txt
python package/package/main.py
```

Then open <http://localhost:5000>. On first start an empty SQLite database is created at
`package/package/instance/leasing.db` and seeded with a demo dataset (8 customers, 12
machines, 10 leases, 40 invoices, service tickets and parts stock). Set `SEED_DEMO_DATA=0`
to start empty, and `flask --app main create-admin` to make the first real account.

### Demo accounts

| Role | Email | Password |
|---|---|---|
| Admin | `admin@copytrack.example` | `Password123` |
| Sales Manager | `sales@copytrack.example` | `Password123` |
| Technician | `tech@copytrack.example` | `Password123` |

## Modules

Every module from section 4 of the requirements is implemented:

| Requirement | Where it lives |
|---|---|
| Customer management | `/customers` — profiles, linked machines, lease history, communication log |
| Machine / equipment inventory | `/machines` — asset register, in-stock vs leased, condition, service history |
| Lease / contract management | `/contracts` — terms, flat / per-copy / hybrid billing, renewal reminders, renewal & termination |
| Invoicing | `/billing/invoices` — generated from leases or raised manually, printable to PDF |
| Meter readings & billing | `/machines/readings` — periodic counts, auto-priced per copy, missed-reading alerts |
| Payment tracking & reminders | payments recorded against invoices; overdue detection and reminder logging |
| Service & maintenance tickets | `/service` — log, assign, schedule, progress, resolve, parts used |
| Consumables / parts inventory | `/inventory` — stock levels, automatic deduction on jobs, low-stock alerts |
| Reporting & dashboard | three role-specific dashboards plus `/reports` with CSV exports |
| User & role management | `/admin/users` — create, edit, deactivate, assign roles |

### Roles

Enforced on every route via `security.py`, not just hidden in the menu.

- **Admin** — everything, plus users, settings and the audit trail.
- **Sales Manager** — customers, machines, leases, invoicing, payments, service, stock, reports.
- **Technician** — own tickets only, machine/customer look-up, meter readings, parts usage.
  Leases, billing, reports and administration return `403`.

### Workflows (section 5)

- **Onboarding** — creating a lease flags the machine `Leased` and links it to the customer.
  A machine already on an active lease cannot be double-booked.
- **Billing** — per-copy invoices price the difference between the last *billed* meter reading
  and the newest one, minus any included allowance; the reading is then marked billed so the
  same copies are never billed twice. Meter counts cannot go backwards.
- **Service** — log → assign → in progress (machine flagged `In Maintenance`) → resolve
  (machine returns to `Leased`). Logging parts deducts stock and refuses over-issue.
- **Renewal / termination** — leases expiring within the alert window (default 30 days)
  surface on the dashboard and in the alert bell. Renewal opens a new contract on the same
  machine and closes the old one; termination returns the machine to available stock.

## Decisions on the open items (section 7)

These were unspecified, so the system takes the wider option and leaves it configurable
under **Admin → Settings** rather than blocking on an answer:

| Open item | What was built |
|---|---|
| Billing models | All three supported: flat monthly, pay-per-copy, and hybrid (flat + copies), with separate black-and-white and colour rates and optional included allowances. |
| Payment gateway | Not integrated. Payments are recorded manually with method (mobile money, bank transfer, cash, cheque, card) and a reference. |
| Customer logins | Staff-only. No customer-facing portal. |
| Notification channel | In-app: alert bell, dashboard panels, and timestamped "sent" / "reminder" events on invoices. No email/SMS sending is wired up. |
| Technician scheduling | Tickets carry a scheduled visit time and appear on a FullCalendar schedule at `/service/schedule`, coloured by priority. |
| Reporting detail | Revenue (billed vs collected), receivables ageing, overdue list, machine utilisation, technician workload — all exportable as CSV. |
| Multi-branch | Single business, but customers carry a `branch` field and machines a `location`, so a branch dimension can be added without a migration of existing records. |

Invoice **PDF export** is done through a print-optimised invoice view (`/billing/invoices/<id>/print`)
and the browser's "save as PDF" — no extra rendering dependency. The **audit trail**
(`/admin/audit`) records who created or changed customers, leases, invoices, payments,
tickets, stock and users.

## Layout of the code

```
package/package/
  main.py                 entry point
  w3admin/
    __init__.py           app factory, template filters, CLI commands
    extensions.py         db / login manager / CSRF
    models.py             all entities + reference-number generators
    services.py           billing maths, alerts, dashboard metrics, workflow helpers
    security.py           role decorators, ticket ownership, audit()
    utils.py              form parsing helpers
    seed.py               demo dataset
    routes.py             the error pages kept from the template
    blueprints/           auth, dashboard, customers, machines, contracts,
                          billing, service, inventory, reports, settings
    templates/leasing/    all business screens
    templates/w3admin/    original template pages, no longer routed
    static/w3admin/       template assets + css/leasing.css
```

## Notes

- The database is SQLite by default; point `DATABASE_URL` at PostgreSQL for deployment —
  the models are plain SQLAlchemy and relational throughout (no flat lists).
- CSRF protection is on for every form; passwords are hashed; sessions are HTTP-only.
- `SECRET_KEY` falls back to a development value — set it in the environment before deploying.
