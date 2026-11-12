# Self-hosting and operations

## Production configuration

Use a host with Docker Compose, persistent encrypted storage, DNS pointing to the host, and ports 80/443 available. The bundled stack is API, migration job, worker, scheduler, PostgreSQL, RabbitMQ, static frontend, and Caddy reverse proxy.

Copy `.env.example` to `.env`, restrict its permissions (`chmod 600 .env`), and generate fresh secrets:

```sh
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))'
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Use separate generated values for Django SECRET_KEY, FIELD_ENCRYPTION_KEY, PostgreSQL password, and RabbitMQ password. Use URL-safe characters in service passwords because Compose builds connection URLs from them. Set DOMAIN, ALLOWED_HOSTS (include localhost for health checks), and CSRF_TRUSTED_ORIGINS to the real HTTPS domain. Never use DEBUG in production.

`TRUST_PROXY_IP=true` trusts Caddy's overwritten X-Real-IP solely for login throttling. Keep the API port private to the Compose network; otherwise a caller could spoof that header. Django also trusts the proxy HTTPS header; direct public access to the API container is unsupported.

```sh
docker compose config --quiet
docker compose up --build -d
docker compose exec api python manage.py bootstrap --organization 'Acme Supply' --username admin
docker compose ps
```

The migration service completes before API/worker/scheduler start. On a fresh install, bootstrap uses an interactive password prompt and the browser enforces administrator MFA enrollment. Add warehouse assignments for nonadministrators; empty assignments grant no warehouse access. Host operators can run `reset_mfa <username> --confirm` for verified recovery; it invalidates that user's sessions and writes an audit event.

Caddy obtains TLS certificates. All browser assets, including Swagger UI, are local. The main application has a restrictive CSP; API docs allow Swagger's inline initialization script. Production cookies are secure, HttpOnly sessions with SameSite Lax; writes require CSRF for session authentication. Bearer tokens authenticate separately.

## Health and monitoring

`/health/` checks API/database availability; it is not a worker/broker health check. Monitor Compose container health, `celery -A config inspect ping`, scheduler activity, delivery backlog/oldest `next_attempt`, failed deliveries, database storage, and backup age. Webhook retry history is available in the UI. Alert on reconciliation failures and sustained HTTP errors.

Logs include request paths/status and worker summaries, not request bodies or credentials. Do not enable request-header/body logging at your proxy. Production error responses do not expose traceback locals. Audit events record actors, operation types, entity IDs, and safe change metadata.

## Backup and restore

PostgreSQL is the source of truth for balances, ledger, documents, audit records, memberships, credentials, imports, forecasts, and webhook delivery state. Also back up `.env`/FIELD_ENCRYPTION_KEY securely; a database backup without the matching encryption key cannot decrypt authenticator and webhook secrets. RabbitMQ can be rebuilt because pending deliveries remain in PostgreSQL. Preserve Caddy data to avoid unnecessary certificate reissuance.

Use encrypted host volumes, encrypted backup storage, restrictive access, and an off-host copy. Example using `age` (install and manage recipient/recovery keys separately):

```sh
mkdir -p backups
umask 077
set -o pipefail
docker compose exec -T db pg_dump -U osscm -Fc osscm | age -r "$BACKUP_RECIPIENT" > backups/osscm.dump.age
```

Verify pipeline exit status and retain backup logs without secrets. Schedule backups in your infrastructure; no external scheduler account is required by OSSCM.

Restore into a **new empty database**, never over the only working copy:

```sh
age -d -i /secure/location/backup-key backups/osscm.dump.age | \
  docker compose exec -T db pg_restore -U osscm --no-owner -d osscm_restored
```

Create `osscm_restored` first with the deployment operator. Supply the same encryption key, run compatible migrations, and run `manage.py reconcile` pointed at the restored database. For an exact verification drill, quiesce source writes for the dump/compare window, then run:

```sh
.venv/bin/python scripts/verify_restore.py "$SOURCE_DATABASE_URL" "$RESTORED_DATABASE_URL"
```

The verifier compares row counts and deterministic SHA-256 content digests for every public table without printing record contents. A live source that changes after the dump will correctly fail comparison. In the recorded local drill, all 34 tables matched and all 11 demo balances reconciled; see VALIDATION.md.

## Upgrades and rollback

Back up and test restore first. Read release notes and migration changes. Stop operational writes/workers during an upgrade, build the new images, run the one-shot migration service explicitly, then recreate API/worker/scheduler and verify health, sign-in, and reconciliation. `docker compose run --rm migrate` reruns migrations even when an old completed migration container exists.

Keep the previous application images and backup together. Roll back application images only if schema compatibility is established; otherwise restore the backup into a new database and switch configuration. Do not run destructive reverse migrations against the sole production copy. Transactions and migrations are schema-managed, not a substitute for backup testing.

## Limits

The local development environment used for implementation had a stopped Docker daemon. Compose structure is provided, but image builds, Caddy certificate issuance, and end-to-end RabbitMQ delivery must be exercised on a Docker host before deployment. HTTP inventory behavior, PostgreSQL locking, frontend build, dependency audits, signature/SSRF/retry logic, and database restoration were tested independently.
