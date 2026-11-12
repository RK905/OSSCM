# API v1

Interactive documentation: `/api/docs/`. OpenAPI document: `/api/schema/`.

All operational routes are under `/api/v1/`. Lists use `?page=1` (50 rows) and `?q=search`. Read endpoints expose products, partners, warehouses, bins, policies, balances, identities, documents, lines, reservations, shipments, transfers, movements, forecasts, and imports. Administrative resources include audit, memberships, credentials, webhooks, and deliveries.

## Authentication

Browser: fetch `GET /api/v1/session/` to obtain CSRF, then POST `{username,password,otp?}` with `X-CSRFToken`. Read a fresh token from the sign-in response. Logout uses `{action:"logout"}`. Same-origin secure cookie sessions are used in production.

MFA: authenticated sessions POST `/api/v1/mfa/` with `{action:"begin"}` to obtain a one-time setup key/URI, then `{action:"confirm",otp:"123456"}`. Production administrators cannot call operational endpoints until enrolled and verified. The same TOTP timestep cannot be reused for another login.

Integrations: create a credential in **Integrations → Credentials**, then send `Authorization: Bearer <token>`. Tokens have explicit scopes, expiration (1–365 days), and revocation; only a SHA-256 digest and display prefix are stored. Scopes never override the credential owner's role or warehouse access. Account and credential administration require a browser administrator session.

## Commands and idempotency

Every POST under `/commands/` requires `Idempotency-Key: <unique opaque string, max 128 characters>`. Reuse it when retrying the same payload. Do not reuse it for changed input. Successful replay returns the original result; conflicting input returns HTTP 400. Validation failures roll back the transaction and do not consume the key. Authorization failures return 401/403; read access to inaccessible records returns 404. Secrets from key/webhook creation are only in the first response, never replayed.

```sh
# TOKEN is an existing scoped integration token; never put it into source code.
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/products/

curl -X POST http://localhost:8000/api/v1/commands/orders/create/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: purchase-example-001' \
  --data '{"kind":"purchase","reference":"PO-001","partner":1,"warehouse":1,"lines":[{"product":1,"quantity":"2","purchase_units":true}]}'
```

Use returned IDs and read `/lines/?document=<id>`; do not assume IDs across installations.

| Command | Important input |
| --- | --- |
| `catalog/{products,partners,warehouses,bins,policies}` | Catalog fields from OpenAPI |
| `catalog/{resource}/{id}/update` | Fields to change (partial update via POST) |
| `orders/create` | kind, reference, partner, warehouse, lines; optional due_date and notes |
| `orders/approve`, `orders/cancel` | document |
| `orders/receive` | line, bin, quantity; optional purchase_units, code, expires_on, quarantine |
| `orders/allocate` | line, balance, quantity |
| `orders/ship` | line, reservation, quantity; optional carrier and tracking |
| `orders/return` | original movement, quantity, reason; optional return bin or supplier-return balance |
| `inventory/adjust` | product, bin, quantity, reason; optional code, expires_on, state, count |
| `inventory/reverse` | adjustment/count movement, reason |
| `inventory/transfer-send` | balance, destination bin, quantity |
| `inventory/transfer-receive` | transfer, quantity |
| `inventory/disposition` | quarantined balance, quantity, disposition (`release` or `scrap`), reason |
| `planning/forecast` | policy |
| `planning/accept` | forecast, unique purchase reference |
| `imports/preview` | csv string |
| `imports/commit` | batch |
| `webhooks/create`, `webhooks/update` | url / webhook and active |
| `deliveries/replay` | delivery |
| `credentials/create`, `credentials/revoke` | name, scopes, days / credential |
| `users/create`, `users/permissions` | team membership fields from OpenAPI |

Quantities are decimal strings, at most four fractional digits. Orders store quantities in base units and snapshot the purchasing factor. Set `purchase_units:true` only when order/receipt input uses the purchasing unit. A serial receipt is one unit per command. Negative quantities are only accepted for explicit adjustments; other quantities must be positive. Dates use ISO 8601. Timestamps are UTC; the frontend displays local browser time.

Filtered views include `/documents/?kind=purchase`, `/lines/?document=1`, `/reservations/?line=1`, `/balances/?state=quarantine`, and `/movements/?product=1&warehouse=1`. `/trace/<identity_id>/` provides identity, accessible balances, the latest 500 movements, and a paginated history link.

## CSV template

```csv
sku,name,unit,purchase_unit,purchase_factor,tracking
BOX-001,Shipping box,each,case,24,none
PART-002,Bearing kit,each,case,12,lot
SCAN-003,Barcode scanner,each,each,1,serial
```

Preview returns `id`, normalized `rows`, and `errors` containing physical row numbers. A preview with errors cannot commit. Duplicate SKUs, bad conversion factors, invalid tracking, and malformed rows are rejected.

## Webhook verification

Payload: `{id,type,created_at,data}`. Headers: `X-OSSCM-Event`, `X-OSSCM-Timestamp` (Unix seconds), `X-OSSCM-Signature: sha256=<hex>`.

Verify HMAC-SHA256 of `timestamp + "." + raw_body` using the signing secret and constant-time comparison. Reject timestamps outside a short tolerance (for example five minutes), then deduplicate on event ID. Retries use a fresh timestamp/signature with the same event ID. Respond 2xx only after durably accepting the event. Network errors, non-2xx responses, and redirects retry; response bodies are discarded.

```python
import hashlib, hmac, time

def valid(secret, timestamp, raw_body, signature):
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), timestamp.encode() + b'.' + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```
