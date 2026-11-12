# Security policy

OSSCM is an initial beta. No compliance certification is claimed. Use maintained releases and perform a deployment review appropriate to your data and threat model.

## Reporting

Do not post credentials, personal data, or exploit details in a public issue. Repository maintainers should enable GitHub private vulnerability reporting before public launch; use that channel when enabled. Until then, request a private reporting channel from the repository owner without including vulnerability details. No invented security mailbox is provided.

## Controls and boundaries

- Role and explicit warehouse access checks apply server-side to commands, lists, details, dashboard totals, and traceability.
- Production administrators enroll TOTP; codes are replay-protected. Authenticator secrets and webhook signing keys use Fernet encryption with a deployment-supplied key. API credentials are hashed, expiring, scoped, and revocable.
- Sign-in and MFA enrollment attempts are limited in PostgreSQL across processes. General API throttling is a convenience guard in each worker, not a distributed denial-of-service defense.
- Secure session cookies, CSRF checks, HTTPS redirect/HSTS, proxy CSP, content-type protection, and clickjacking controls are configured. Network boundaries must prevent public direct access to API/database/broker.
- Webhooks require public HTTPS port 443, revalidate every DNS result per attempt, pin the chosen IP for connection, validate TLS against the original hostname, and reject redirects. An egress firewall is an additional operator-controlled layer.
- Stock/audit tables reject mutation through application models and database triggers. Database owners and host administrators remain trusted and can change database enforcement.
- Audits omit passwords, tokens, TOTP keys, and signing secrets. Idempotency replay never retains issued plaintext credentials. Catalog metadata is user-controlled; do not put secrets in product metadata or notes.
- Use encrypted disks, encrypted backups, least-privilege host access, and protected encryption-key backups. Rotate keys with an explicit migration/re-encryption procedure; replacing FIELD_ENCRYPTION_KEY alone makes existing encrypted secrets unreadable.
- CI audits Python/npm dependencies, and Dependabot checks weekly. Keep Python, Node, PostgreSQL, base images, and reverse proxy updated.

The API does not execute arbitrary plugins or run uploaded code. CSV imports are bounded product-data imports. There is no mandatory telemetry, external model provider, or automatic purchasing.
