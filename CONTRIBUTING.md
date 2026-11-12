# Contributing

Contributions are licensed under Apache-2.0. Please open an issue describing the operational problem before a large change. Include a concrete workflow example, data assumptions, and intended acceptance tests.

Set up the local environment from README.md. Keep domain mutations in transactional services, not views/serializers/tasks. Any stock change must use the ledger service, preserve quantity/reservation invariants, and pass PostgreSQL concurrency tests. Do not replace row-lock tests with SQLite tests.

Run backend tests, migration checks, OpenAPI validation, frontend build, and dependency audits before submitting a PR. Include tests for permissions and transaction rollback when changing a command. Include a migration whenever models change. Preserve API compatibility within `/api/v1`; document new commands and input semantics.

Use decimal base quantities, explicit timezones, user-facing error messages, accessible labeled controls, and no mandatory external service for basic operations. Never commit credentials, local databases, backups, or real customer data. Keep demonstration data explicitly synthetic.

PR descriptions should explain the user-visible behavior, relevant design tradeoffs, and what was verified. Report untested deployment paths candidly. Follow SECURITY.md for vulnerabilities.
