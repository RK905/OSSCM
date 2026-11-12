# OSSCM

**A clear view, from source to shipment.**

OSSCM is a self-hosted, Apache-2.0 supply chain workspace for small distributors. It connects purchasing, inventory, fulfillment, traceability, and replenishment planning. The core uses industry-neutral products, partners, quantities, and locations; lot/serial tracking and expiry are optional.

> Initial implementation / beta. Core acceptance tests and a local HTTP benchmark pass. Container builds and the complete RabbitMQ/Caddy deployment still need validation on a Docker host. This is not a compliance certification or an enterprise-scale guarantee.

## What works

- Products, suppliers/customers, warehouses/bins, metadata, and fixed purchasing conversions.
- Draft/approved purchase and sales orders, partial receipts/shipments, reservations, backorders, cancellation of outstanding quantities, and carrier/tracking references.
- Append-only stock ledger, counts/adjustments with reversals, transfers with in-transit inventory, customer returns into quarantine, release/scrap disposition, and supplier returns.
- Lot/serial/expiry controls and receipt-to-shipment movement history.
- Visibility dashboard with available/reserved/inbound/quarantined quantities, expiry/low-stock/overdue alerts, and supporting records.
- Weekly forecasts, historical model comparisons, manual fallback, replenishment recommendations, and human-approved purchase drafts.
- Versioned REST API, OpenAPI/Swagger, scoped bearer credentials, idempotent commands, CSV preview/commit, signed webhooks with retry and replay.
- Role and warehouse permissions, sessions/CSRF, database-backed sign-in throttling, TOTP MFA, encrypted signing/TOTP secrets, and immutable audit history.

## Local development

Requires Python 3.14, Node 24+, and PostgreSQL 14+. Production Compose selects PostgreSQL 17. All commands below start at the repository root.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
npm --prefix frontend ci
createdb osscm
export DEBUG=true
export DATABASE_URL=postgresql://localhost/osscm
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py collectstatic --noinput
.venv/bin/python backend/manage.py bootstrap --organization 'Your company' --username admin
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

In another terminal:

```sh
npm --prefix frontend run dev -- --port 5173
```

Open [the local workspace](http://127.0.0.1:5173). The Vite proxy keeps API and browser traffic same-origin. Only the two localhost:5173 development origins are trusted by default; set `CSRF_TRUSTED_ORIGINS` when using a different development address.

For a disposable demo, use `seed_demo` **instead of** `bootstrap` on an empty database. It prints a randomly generated password for username `demo`. It creates clearly synthetic order history and sample inventory; it refuses to run outside DEBUG mode. There are no embedded default credentials.

```sh
DEBUG=true .venv/bin/python backend/manage.py seed_demo
```

For webhook delivery in development, run RabbitMQ and then:

```sh
DEBUG=true .venv/bin/celery --workdir backend -A config worker --loglevel=INFO
DEBUG=true .venv/bin/celery --workdir backend -A config beat --loglevel=INFO --schedule=/tmp/osscm-celerybeat
```

The operational API remains usable without a broker; webhook events stay in the transactional database outbox until workers run. The worker polls every 30 seconds.

## First workflow

1. Create a warehouse and a bin in **Getting started**.
2. Add products or preview and commit a product CSV. Select a tracking mode before creating stock/order history.
3. Create a supplier and customer.
4. Create a purchase order. Open its details, approve it, open a line, and receive into a bin. Receipt quantities default to base units; the purchasing-unit checkbox applies the factor snapshotted on that order line.
5. Create and approve a sales order. Open a line, reserve a stock balance, then open its reservation to record a shipment.
6. Open a receipt/shipment movement to record a return. Customer returns must receive a disposition before becoming available.
7. Create a replenishment policy. Run a forecast, inspect the model evidence, and accept a positive recommendation into a purchase draft.

Stock totals on the dashboard sum base-unit quantities across products. They are operational activity totals, not weight, monetary value, or a substitute for SKU-level quantities. Native tables are paginated; drill into each product or warehouse for its history.

## Deployment and operations

See [deployment and backup/restore](docs/OPERATIONS.md), [security](SECURITY.md), [API examples](docs/API.md), and [architecture decisions](docs/ARCHITECTURE.md).

```sh
cp .env.example .env
# Replace domain and ALL secrets before starting.
docker compose up --build -d
docker compose exec api python manage.py bootstrap --organization 'Your company' --username admin
```

Caddy terminates HTTPS. The database and broker have no host-published ports. Production administrators must enroll MFA before accessing operational endpoints.

## Verification

```sh
DEBUG=true .venv/bin/pytest -q
DEBUG=true .venv/bin/python backend/manage.py makemigrations --check --dry-run
DEBUG=true .venv/bin/python backend/manage.py spectacular --file /tmp/osscm-openapi.yaml --validate --fail-on-warn
npm --prefix frontend run build
.venv/bin/pip-audit -r backend/requirements.lock
npm --prefix frontend audit --audit-level=high
DEBUG=true .venv/bin/python backend/manage.py reconcile
```

Tests use a separate PostgreSQL test database and include real concurrent allocation/idempotency tests. SQLite is allowed only for lightweight local exploration; its missing row locks make it unsuitable for deployment or concurrency verification.

The [benchmark report](docs/BENCHMARK.md) documents the measured 20-user run, fixture generation, reference host, and limits. CI runs PostgreSQL tests, migration/schema checks, frontend builds, dependency audits, and container builds.

## Project layout

- `backend/core/domain/`: shared transactional catalog, inventory, order, planning, and integration services.
- `backend/core/identity.py`, `models.py`, `api.py`: authentication, persistence, and validated API adapters.
- `frontend/src/`: React workspace, workflow forms, record detail views, and forecast visualization.
- `deploy/`, `compose.yaml`, `scripts/`: self-hosting and operational verification.

## Boundaries

V1 is one organization per installation, not a shared multi-tenant SaaS. It does not implement accounting, payment processing, manufacturing/BOMs, carrier feeds, arbitrary code plugins, offline operation, or named vendor connectors. English strings use an i18n foundation; translations are not supplied yet. The UI is desktop-first and adapts to narrow screens.

Forecast intervals are approximate, not calibrated guarantees. Stockout flags use closing weekly availability and cannot reconstruct demand a customer never placed. Recommendations subtract all confirmed inbound orders, so operators must review inbound due dates. See the architecture document for these explicit limitations.

Contributions are welcome under [Apache-2.0](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md).
