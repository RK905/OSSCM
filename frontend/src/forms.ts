export type Field = {
  key: string;
  label: string;
  type?: string;
  resource?: string;
  options?: string[];
  required?: boolean;
  value?: unknown;
  hint?: string;
};
export type FormSpec = {
  title: string;
  command: string;
  fields: Field[];
  initial?: Record<string, unknown>;
  description?: string;
};
const ref = (key: string, label: string, resource: string): Field => ({
  key,
  label,
  resource,
  required: true,
});
const text = (key: string, label: string, value?: unknown): Field => ({
  key,
  label,
  value,
  required: true,
});
const qty = (key = "quantity", label = "Quantity", value?: unknown): Field => ({
  key,
  label,
  type: "number",
  value,
  required: true,
});
const select = (
  key: string,
  label: string,
  options: string[],
  value?: string,
): Field => ({
  key,
  label,
  type: "select",
  options,
  value: value || options[0],
});
const reason = text("reason", "Reason");
export const forms: Record<string, FormSpec> = {
  product: {
    title: "Add product",
    command: "catalog/products",
    fields: [
      text("sku", "SKU"),
      text("name", "Product name"),
      text("unit", "Base unit", "each"),
      text("purchase_unit", "Purchasing unit", "each"),
      qty("purchase_factor", "Base units per purchasing unit", 1),
      select("tracking", "Tracking", ["none", "lot", "serial"]),
      { key: "active", label: "Active product", type: "checkbox", value: true },
      {
        key: "metadata",
        label: "Custom fields (JSON object)",
        type: "json",
        value: "{}",
      },
    ],
  },
  partner: {
    title: "Add partner",
    command: "catalog/partners",
    fields: [
      text("name", "Name"),
      select("kind", "Partner type", ["supplier", "customer"]),
      { key: "email", label: "Email", type: "email" },
      {
        key: "metadata",
        label: "Custom fields (JSON object)",
        type: "json",
        value: "{}",
      },
    ],
  },
  warehouse: {
    title: "Add warehouse",
    command: "catalog/warehouses",
    fields: [text("code", "Code"), text("name", "Warehouse name")],
  },
  bin: {
    title: "Add bin",
    command: "catalog/bins",
    fields: [
      ref("warehouse", "Warehouse", "warehouses"),
      text("code", "Bin code"),
    ],
  },
  policy: {
    title: "Add replenishment policy",
    command: "catalog/policies",
    fields: [
      ref("product", "Product", "products"),
      ref("warehouse", "Warehouse", "warehouses"),
      ref("supplier", "Supplier", "partners?kind=supplier"),
      qty("lead_days", "Lead time (days)", 7),
      qty("review_days", "Review period (days)", 7),
      qty("safety_stock", "Safety stock", 0),
      qty("reorder_point", "Reorder point", 0),
      qty("target_stock", "Manual target stock", 0),
      qty("minimum_order", "Minimum order (base units)", 1),
      qty("pack_size", "Pack size (base units)", 1),
    ],
  },
  purchase: {
    title: "New purchase order",
    command: "orders/create",
    initial: { kind: "purchase" },
    fields: [
      text("reference", "Order reference"),
      ref("partner", "Supplier", "partners?kind=supplier"),
      ref("warehouse", "Receiving warehouse", "warehouses"),
      { key: "due_date", label: "Expected date", type: "date" },
      { key: "lines", label: "Order lines", type: "lines" },
      { key: "notes", label: "Notes", type: "textarea" },
    ],
  },
  sale: {
    title: "New sales order",
    command: "orders/create",
    initial: { kind: "sale" },
    fields: [
      text("reference", "Order reference"),
      ref("partner", "Customer", "partners?kind=customer"),
      ref("warehouse", "Fulfilling warehouse", "warehouses"),
      { key: "due_date", label: "Due date", type: "date" },
      { key: "lines", label: "Order lines", type: "lines" },
      { key: "notes", label: "Notes", type: "textarea" },
    ],
  },
  approve: {
    title: "Approve order",
    command: "orders/approve",
    fields: [ref("document", "Order", "documents")],
  },
  cancel: {
    title: "Cancel outstanding quantities",
    command: "orders/cancel",
    description:
      "Releases reservations and cancels only the remaining unprocessed quantity.",
    fields: [ref("document", "Order", "documents")],
  },
  receive: {
    title: "Receive goods",
    command: "orders/receive",
    fields: [
      ref("line", "Purchase order line", "lines"),
      ref("bin", "Receiving bin", "bins"),
      qty(),
      {
        key: "purchase_units",
        label: "Quantity is in purchasing units",
        type: "checkbox",
      },
      { key: "code", label: "Lot or serial number" },
      { key: "expires_on", label: "Expiry date", type: "date" },
      { key: "quarantine", label: "Receive into quarantine", type: "checkbox" },
    ],
  },
  allocate: {
    title: "Reserve inventory",
    command: "orders/allocate",
    fields: [
      ref("line", "Sales order line", "lines"),
      ref("balance", "Stock balance", "balances"),
      qty(),
    ],
  },
  ship: {
    title: "Record shipment",
    command: "orders/ship",
    fields: [
      ref("line", "Sales order line", "lines"),
      ref("reservation", "Reservation", "reservations"),
      qty(),
      { key: "carrier", label: "Carrier" },
      { key: "tracking", label: "Tracking reference" },
    ],
  },
  adjust: {
    title: "Adjust stock",
    command: "inventory/adjust",
    fields: [
      ref("product", "Product", "products"),
      ref("bin", "Bin", "bins"),
      qty(),
      select("state", "Stock state", ["available", "quarantine"]),
      { key: "code", label: "Lot or serial number" },
      { key: "expires_on", label: "Expiry date", type: "date" },
      reason,
    ],
  },
  count: {
    title: "Record inventory count",
    command: "inventory/adjust",
    initial: { count: true },
    description:
      "Enter the physical count. The system posts the difference as an auditable movement.",
    fields: [
      ref("product", "Product", "products"),
      ref("bin", "Bin", "bins"),
      qty("quantity", "Counted quantity"),
      select("state", "Stock state", ["available", "quarantine"]),
      { key: "code", label: "Lot or serial number" },
      reason,
    ],
  },
  reverse: {
    title: "Reverse adjustment",
    command: "inventory/reverse",
    fields: [
      ref("movement", "Original adjustment / count", "movements"),
      reason,
    ],
  },
  transfer: {
    title: "Send transfer",
    command: "inventory/transfer-send",
    fields: [
      ref("balance", "Source stock", "balances"),
      ref("destination", "Destination bin", "bins"),
      qty(),
    ],
  },
  transferReceive: {
    title: "Receive transfer",
    command: "inventory/transfer-receive",
    fields: [ref("transfer", "Transfer", "transfers"), qty()],
  },
  return: {
    title: "Return goods",
    command: "orders/return",
    description:
      "Customer returns enter quarantine. Supplier returns remove stock from the original receipt identity.",
    fields: [
      ref("movement", "Original receipt / shipment", "movements"),
      qty(),
      reason,
    ],
  },
  disposition: {
    title: "Resolve quarantined stock",
    command: "inventory/disposition",
    fields: [
      ref("balance", "Quarantined stock", "balances?state=quarantine"),
      qty(),
      select("disposition", "Disposition", ["release", "scrap"]),
      reason,
    ],
  },
  forecast: {
    title: "Run demand forecast",
    command: "planning/forecast",
    fields: [ref("policy", "Replenishment policy", "policies")],
  },
  accept: {
    title: "Create purchase draft",
    command: "planning/accept",
    description:
      "Review the recommendation and inbound due dates. This creates a draft; it does not approve or send an order.",
    fields: [
      ref("forecast", "Forecast run", "forecasts"),
      text("reference", "Purchase order reference"),
    ],
  },
  import: {
    title: "Preview product import",
    command: "imports/preview",
    description:
      "Paste CSV or choose a file. Required columns: sku,name. Optional: unit,purchase_unit,purchase_factor,tracking.",
    fields: [{ key: "csv", label: "Product CSV", type: "csv", required: true }],
  },
  importCommit: {
    title: "Commit validated import",
    command: "imports/commit",
    fields: [ref("batch", "Import preview", "imports")],
  },
  webhook: {
    title: "Add webhook",
    command: "webhooks/create",
    description:
      "HTTPS public destinations only. The signing secret is displayed once.",
    fields: [text("url", "Destination URL")],
  },
  webhookToggle: {
    title: "Change webhook status",
    command: "webhooks/update",
    fields: [
      ref("webhook", "Webhook", "webhooks"),
      { key: "active", label: "Enabled", type: "checkbox" },
    ],
  },
  replay: {
    title: "Replay webhook delivery",
    command: "deliveries/replay",
    fields: [ref("delivery", "Delivery", "deliveries")],
  },
  credential: {
    title: "Create API credential",
    command: "credentials/create",
    description: "Copy the token now; it cannot be retrieved later.",
    fields: [
      text("name", "Credential name"),
      {
        key: "scopes",
        label: "Scopes",
        type: "multi",
        options: [
          "read",
          "catalog",
          "purchasing",
          "warehouse",
          "sales",
          "planning",
          "integrations",
        ],
        value: ["read"],
      },
      qty("days", "Expires in days", 90),
    ],
  },
  revoke: {
    title: "Revoke API credential",
    command: "credentials/revoke",
    fields: [ref("credential", "Credential", "credentials")],
  },
  user: {
    title: "Create team member",
    command: "users/create",
    fields: [
      text("username", "Username"),
      { ...text("password", "Temporary password"), type: "password" },
      select("role", "Role", [
        "readonly",
        "purchasing",
        "warehouse",
        "sales",
        "administrator",
      ]),
      {
        key: "warehouses",
        label: "Warehouse IDs (comma separated)",
        type: "ids",
        value: "",
      },
      { key: "email", label: "Email", type: "email" },
    ],
  },
  permissions: {
    title: "Update team access",
    command: "users/permissions",
    fields: [
      ref("membership", "Membership", "memberships"),
      select("role", "Role", [
        "readonly",
        "purchasing",
        "warehouse",
        "sales",
        "administrator",
      ]),
      {
        key: "warehouses",
        label: "Warehouse IDs (comma separated)",
        type: "ids",
      },
      { key: "active", label: "Active account", type: "checkbox", value: true },
    ],
  },
};
