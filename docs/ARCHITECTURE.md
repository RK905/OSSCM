# Architecture decisions

## Modular monolith

Django 5.2 LTS / DRF, PostgreSQL, React/TypeScript/Vite, and Celery/RabbitMQ. A single deployable application keeps transactions and maintenance approachable. Domain services are separated by catalog, orders, inventory, planning, integrations, and identity; Django models live in one migration-owning app to keep cross-domain constraints straightforward.

The API and UI never write operational model fields directly. Commands validate typed input, check role and organization/location access, and call the same domain services used by imports. Catalog updates preserve references; unit/tracking changes are blocked once product history exists. Purchasing conversion factors are snapshotted on order lines.

No business data or telemetry is sent to an external AI service. Browser assets and interactive API documentation are self-hosted.

## Inventory invariants

- Stock identity = product + lot/serial code. Nontracked products use an empty code. Expiry belongs to the identity, not each movement.
- Balance = identity + bin + state (available, quarantine, transit).
- Available-to-promise = available-state on-hand minus reserved, excluding expired identities.
- On-hand and reserved are nonnegative and reserved cannot exceed on-hand. PostgreSQL check constraints enforce these local invariants.
- Product row locks serialize stock mutations, including serial uniqueness across locations. Reservation and order rows are locked where they participate in a command. Balance creation happens after acquiring the product lock.
- Serial movements require whole quantities; the identity can have at most one physical unit across all states/locations.
- Reservation movements change only reserved quantities. Shipping reduces on-hand and reserved together. Over-allocation fails; backorders remain in the order's unprocessed quantity.
- Transfers debit source and credit transit atomically; receipt debits transit and credits the destination. Partial transfer receipts are supported.
- Customer returns reference a shipment and enter quarantine. Supplier returns reference a receipt, use the same identity/original warehouse, and cannot exceed previously received quantities less earlier returns.
- Counts post the physical-minus-book difference. Only count/adjustment corrections use direct reversal; receipts/shipments use explicit return flows to preserve document meaning.
- PostgreSQL triggers and ORM managers reject movement/audit UPDATE/DELETE. Database administrators remain trusted operators; table ownership is not a cryptographic audit guarantee.
- `reconcile` independently checks ledger totals against balances and active reservations.

## Order lifecycle

Draft → approved → partial → complete. Cancelling releases remaining reservations and cancels unprocessed units. A partly fulfilled, subsequently cancelled document has status complete with explicit cancelled quantities; an entirely unfulfilled cancellation has status cancelled. Return movements do not reopen already fulfilled orders. Create a new order for replacement goods.

One organization per installation, many warehouses/bins. All business records carry organization ownership. Non-administrators receive explicit warehouse assignments; an empty assignment grants no warehouse access. Global product/partner catalog is organization-visible. Transfer access requires both endpoints. Audit, credential, webhook, delivery, and team administration are administrator-only.

## Reliability and integrations

Every operational/catalog command requires an idempotency key. A membership row serializes keys per actor; product locks protect inventory across actors. Responses and payload fingerprints commit with the operation. A repeated key with different input fails. Secret-bearing responses retain only nonsensitive fields in the idempotency record; credential/signing secrets are shown once.

An operation commits its audit record, outbox event, and per-subscriber delivery records in the same transaction. New webhook subscriptions receive future events. Workers poll due deliveries, revalidate DNS, pin a public IP for the TLS connection with original hostname verification, and do not follow redirects. At-least-once delivery means consumers deduplicate the immutable event UUID. There are eight attempts with capped exponential delays, then operator replay. Inactive webhooks pause delivery. Replay retains the event UUID. Request bodies and secret values are not logged.

CSV onboarding supports product creation with at most 5,000 rows / 1 MiB per preview. Preview persists validated rows and row errors; commit revalidates inside one transaction and never silently overwrites a SKU. Other integration entities use the documented API.

## Planning

Demand is accepted gross sales-order quantity, bucketed by approval week at SKU/warehouse level. Cancellations are reported separately in their actual occurrence week, using immutable audit events; cancelling an old order never rewrites earlier demand observations. The open week is excluded. Customer returns are reported separately and do not subtract from order demand. History is capped at 156 weeks.

Eight complete weeks are required for modeling. Candidates are exponential smoothing (alpha .25), SBA-corrected Croston (alpha .1), and a 52-week seasonal naive model when at least 60 weeks exist. Each candidate is scored on the same rolling, one-step historical folds; forecasts receive only the training prefix. Minimum MAE wins, with deterministic ties. MAE, signed bias, and WAPE are exposed; WAPE is undefined when validation demand totals zero.

Approximate 90% residual-based intervals are available with eight validation residuals and widen with horizon. They are not calibrated coverage claims. No model consumes future observations from the demand series. Because the target is accepted gross demand, businesses with substantial cancellations should inspect the separately reported cancellations before accepting recommendations; cancellation-rate modeling is not implemented.

The planning horizon covers lead time plus review period (maximum 365 days). Target = forecast demand during that period + safety stock. Available stock and confirmed approved/partial purchase inbound reduce the requirement. Positive quantities round up to minimum order and base-unit pack size. Sparse history uses reorder-point/target-stock settings. All confirmed inbound is subtracted regardless of its due date; operators must verify timing before accepting a recommendation.

Acceptance creates a draft order only, once per forecast, within 24 hours of the run. It never approves or transmits an order. It does not lock stock/inbound against subsequent changes; the user remains the approval authority.

## Extension path

Industry fields belong in metadata or optional core tracking features. Future manufacturing, replenishment optimization, named connectors, and enterprise hosting should reuse the command/outbox interfaces. Defer untrusted plugin code execution, a workflow builder, and distributed services until a concrete requirement justifies their security and operational cost.
