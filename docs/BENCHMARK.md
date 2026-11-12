# Inventory HTTP benchmark

## Recorded local run

Reference host: macOS 26.6.2, Apple ARM64 model Mac15,12, 8 logical CPUs, 16 GiB memory. Python 3.14.4, PostgreSQL 14.21, Gunicorn 27 with four worker processes and five threads per worker. The database and HTTP client ran on the same machine. DEBUG was enabled to permit the guarded benchmark tooling.

Fixture: 10,000 SKUs, five warehouses, 50,000 stock balances, and **1,000,000 initial stock movements**. Twenty independent bearer credentials issued 100 requests each, alternating reads and writes over real loopback HTTP. Reads fetched paginated balance lists; writes performed transactional adjustments including ledger, audit, outbox, and idempotency records.

| Operation | Requests | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Inventory reads | 1,000 | 41.71 ms | 122.87 ms | 226.99 ms |
| Inventory writes | 1,000 | 22.86 ms | 83.69 ms | 145.01 ms |

No errors. Elapsed 4.51 seconds, approximately 443.75 requests/second. Both p95 values passed the sub-second target. The [raw result](benchmark-local.json) includes the measurement configuration.

This is a short, warm local microbenchmark, not an enterprise load certification. It does not measure WAN latency, TLS, cold storage, long-run saturation, dashboard aggregation, large imports, or slow webhook destinations. Writes target distributed products; hot-SKU contention is separately covered by correctness tests, not a throughput claim. Production Compose's default three synchronous API workers have a different capacity profile; tune and benchmark your actual deployment.

## Reproduce

The seed command only runs in DEBUG mode, on an empty PostgreSQL database whose name contains `bench`. It inserts synthetic fixtures directly for speed; production mutations must use domain services.

```sh
createdb osscm_bench
DEBUG=true DATABASE_URL=postgresql://localhost/osscm_bench .venv/bin/python backend/manage.py migrate
DEBUG=true DATABASE_URL=postgresql://localhost/osscm_bench .venv/bin/python backend/manage.py benchmark --seed
DEBUG=true DATABASE_URL=postgresql://localhost/osscm_bench .venv/bin/gunicorn --chdir backend config.wsgi:application --bind 127.0.0.1:8001 --workers 4 --threads 5
```

In another terminal:

```sh
DEBUG=true DATABASE_URL=postgresql://localhost/osscm_bench .venv/bin/python backend/manage.py benchmark --output benchmark-results.json
```

Use a fresh benchmark database to reproduce the baseline exactly. Every run creates additional movements and credentials. Keep the fixture isolated from real operations. Only remove the disposable database after retaining the report you need.
