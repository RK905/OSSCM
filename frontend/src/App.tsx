import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Database,
  Download,
  ExternalLink,
  Layers3,
  Link2,
  LoaderCircle,
  LogOut,
  Menu,
  Package,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  TrendingUp,
  Truck,
  Users,
  Warehouse,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import "./i18n";
import {
  api,
  command,
  label,
  number,
  lookup,
  clearCache,
  type Row,
} from "./api";
import { forms, type Field, type FormSpec } from "./forms";
import "./style.css";

type User = {
  username: string;
  role: string;
  organization: string;
  currency: string;
  mfa_enabled: boolean;
  mfa_required: boolean;
  mfa_verified: boolean;
};
type Page =
  | "overview"
  | "inventory"
  | "purchasing"
  | "fulfillment"
  | "planning"
  | "integrations"
  | "catalog"
  | "locations"
  | "team"
  | "setup";
const pages: { id: Page; title: string; icon: typeof Boxes; group: string }[] =
  [
    { id: "overview", title: "Overview", icon: Layers3, group: "WORKSPACE" },
    { id: "inventory", title: "Inventory", icon: Boxes, group: "" },
    { id: "purchasing", title: "Purchasing", icon: ArrowDownLeft, group: "" },
    { id: "fulfillment", title: "Fulfillment", icon: Truck, group: "" },
    { id: "planning", title: "Demand planning", icon: TrendingUp, group: "" },
    {
      id: "catalog",
      title: "Products & partners",
      icon: Package,
      group: "MANAGE",
    },
    { id: "locations", title: "Locations", icon: Warehouse, group: "" },
    { id: "integrations", title: "Integrations", icon: Link2, group: "" },
    { id: "team", title: "Team & security", icon: ShieldCheck, group: "" },
    { id: "setup", title: "Getting started", icon: Settings2, group: "" },
  ];
const tableColumns: Record<string, string[]> = {
  products: ["sku", "name", "unit", "tracking", "purchase_factor"],
  partners: ["name", "kind", "email"],
  warehouses: ["code", "name"],
  bins: ["code", "warehouse"],
  balances: ["identity", "bin", "state", "on_hand", "reserved"],
  documents: ["reference", "partner", "warehouse", "status", "due_date"],
  lines: ["product", "quantity", "processed", "cancelled"],
  reservations: ["line", "balance", "quantity"],
  identities: ["product", "code", "expires_on"],
  movements: [
    "created_at",
    "kind",
    "balance",
    "quantity",
    "reserved_delta",
    "reason",
  ],
  transfers: ["source", "destination", "quantity", "received"],
  policies: ["product", "warehouse", "supplier", "lead_days", "reorder_point"],
  forecasts: ["created_at", "policy", "recommended", "document"],
  imports: ["created_at", "committed_at"],
  webhooks: ["url", "active"],
  deliveries: ["event", "webhook", "status", "attempts", "response_code"],
  credentials: ["name", "prefix", "scopes", "expires_at", "revoked"],
  memberships: ["username", "role", "warehouses", "totp_enabled", "active"],
  audit: ["created_at", "actor", "action", "entity"],
  shipments: ["document", "carrier", "tracking", "created_at"],
};
const relationship: Record<string, string> = {
  product: "products",
  partner: "partners",
  supplier: "partners",
  warehouse: "warehouses",
  bin: "bins",
  destination: "bins",
  identity: "identities",
  balance: "balances",
  source: "balances",
  document: "documents",
  line: "lines",
  policy: "policies",
  reservation: "reservations",
};
function nice(s: string) {
  return s.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
function timestamp(s: string) {
  return new Date(s).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}

function ReferenceSelect({
  field,
  value,
  onChange,
}: {
  field: Field;
  value: any;
  onChange: (v: any) => void;
}) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const timeout = setTimeout(() => {
      const [resource, params = ""] = field.resource!.split("?");
      api(`${resource}/?${params}&q=${encodeURIComponent(query)}`)
        .then((r) => {
          if (live) {
            setRows(r.results);
            setError("");
          }
        })
        .catch((e) => live && setError(e.message));
    }, 180);
    return () => {
      live = false;
      clearTimeout(timeout);
    };
  }, [query, field.resource]);
  return (
    <div className="reference-input">
      <input
        aria-label={`Search ${field.label}`}
        placeholder={`Search ${field.label.toLowerCase()}…`}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <select
        required={field.required}
        aria-label={field.label}
        value={value || ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        <option value="">Choose {field.label.toLowerCase()}</option>
        {value && !rows.some((r) => r.id === Number(value)) && (
          <option value={value}>Selected #{value}</option>
        )}
        {rows.map((r) => (
          <option key={r.id} value={r.id}>
            {label(r)} · #{r.id}
          </option>
        ))}
      </select>
      {error && <small className="danger">{error}</small>}
    </div>
  );
}
function LinesEditor({
  value,
  onChange,
  purchase,
}: {
  value: Row[];
  onChange: (v: Row[]) => void;
  purchase: boolean;
}) {
  const rows = value?.length
    ? value
    : [{ product: "", quantity: 1, purchase_units: false }];
  return (
    <div className="line-editor">
      {rows.map((row, i) => (
        <div className="line-edit" key={i}>
          <ReferenceSelect
            field={{
              key: "product",
              label: "Product",
              resource: "products",
              required: true,
            }}
            value={row.product}
            onChange={(v) =>
              onChange(rows.map((r, n) => (n === i ? { ...r, product: v } : r)))
            }
          />
          <input
            aria-label={`Line ${i + 1} quantity`}
            type="number"
            step="0.0001"
            min="0.0001"
            required
            value={row.quantity}
            onChange={(e) =>
              onChange(
                rows.map((r, n) =>
                  n === i ? { ...r, quantity: e.target.value } : r,
                ),
              )
            }
          />
          {purchase && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={!!row.purchase_units}
                onChange={(e) =>
                  onChange(
                    rows.map((r, n) =>
                      n === i ? { ...r, purchase_units: e.target.checked } : r,
                    ),
                  )
                }
              />
              Purchasing units
            </label>
          )}
          <button
            type="button"
            className="icon-button"
            aria-label="Remove line"
            disabled={rows.length === 1}
            onClick={() => onChange(rows.filter((_, n) => n !== i))}
          >
            <X size={16} />
          </button>
        </div>
      ))}
      <button
        className="button secondary small"
        type="button"
        onClick={() =>
          onChange([
            ...rows,
            { product: "", quantity: 1, purchase_units: false },
          ])
        }
      >
        <Plus size={14} />
        Add line
      </button>
    </div>
  );
}
function Forecast({ result }: { result: Row }) {
  const values: number[] = result.forecast || [];
  const history: number[] = result.history || [];
  const shown = [...history.slice(-12), ...values];
  const max = Math.max(1, ...shown);
  return (
    <div className="forecast-result">
      <div className="eyebrow">{nice(result.method || "Forecast")}</div>
      <h3>{number(result.recommended)} units recommended</h3>
      <p>{result.reason || result.explanation}</p>
      {shown.length > 0 && (
        <div
          className="forecast-chart"
          aria-label="Weekly historical demand and forecast"
        >
          {shown.map((v, i) => (
            <div
              key={i}
              className={i >= Math.min(12, history.length) ? "predicted" : ""}
              style={{ height: `${Math.max(2, (v / max) * 100)}%` }}
              title={`${i >= Math.min(12, history.length) ? "Forecast" : "Historical"} week: ${number(v)}`}
            />
          ))}
        </div>
      )}
      <div className="chart-legend">
        <i /> Historical demand <i className="forecast" /> Forecast · base units
        / week
      </div>
      {result.metrics && (
        <div className="metric-row">
          <span>
            MAE <b>{number(result.metrics.mae)}</b>
          </span>
          <span>
            Bias <b>{number(result.metrics.bias)}</b>
          </span>
          <span>
            WAPE{" "}
            <b>
              {result.metrics.wape === null
                ? "Undefined (zero demand)"
                : `${number(result.metrics.wape * 100)}%`}
            </b>
          </span>
        </div>
      )}
      <p className="muted">{result.interval_note}</p>
      {result.stockout_weeks?.length > 0 && (
        <div className="notice">
          {result.stockout_weeks.length} weeks had nonpositive closing
          availability. Observed orders may understate demand.
        </div>
      )}
      <details>
        <summary>Model comparison, history & assumptions</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}
function Modal({
  spec,
  onClose,
  onDone,
}: {
  spec: FormSpec;
  onClose: () => void;
  onDone: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [values, setValues] = useState<Row>(() =>
    Object.fromEntries(
      spec.fields.map((f) => [
        f.key,
        (f.type === "json" && spec.initial?.[f.key]
          ? JSON.stringify(spec.initial[f.key], null, 2)
          : spec.initial?.[f.key]) ??
          f.value ??
          (f.type === "checkbox"
            ? false
            : f.type === "lines"
              ? [{ product: "", quantity: 1, purchase_units: false }]
              : f.type === "multi"
                ? []
                : ""),
      ]),
    ),
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Row | null>(null);
  const key = useRef(crypto.randomUUID());
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  function change(k: string, v: any) {
    setValues((p) => ({ ...p, [k]: v }));
    key.current = crypto.randomUUID();
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body: Row = {
        ...(spec.initial?.kind ? { kind: spec.initial.kind } : {}),
        ...(spec.initial?.count ? { count: true } : {}),
        ...values,
      };
      for (const f of spec.fields) {
        if (f.type === "json") {
          body[f.key] = JSON.parse(values[f.key] || "{}");
          if (
            typeof body[f.key] !== "object" ||
            body[f.key] === null ||
            Array.isArray(body[f.key])
          )
            throw new Error("Custom fields must be a JSON object.");
        }
        if (f.type === "ids")
          body[f.key] = String(values[f.key] || "")
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean)
            .map(Number);
        if (body[f.key] === "" && !f.required) delete body[f.key];
      }
      const output = await command(spec.command, body, key.current);
      setResult(output);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog ref={dialog} className="modal" onCancel={onClose}>
      <div className="modal-header">
        <div>
          <div className="eyebrow">WORKSPACE ACTION</div>
          <h2>{spec.title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <X />
        </button>
      </div>
      {result ? (
        <div className="modal-body">
          <div className="success">
            <Check size={18} />
            Completed successfully
          </div>
          {result.method ? (
            <Forecast result={result} />
          ) : result.rows ? (
            <>
              <p>
                {result.rows.length} rows validated. {result.errors.length}{" "}
                errors.
              </p>
              {result.errors.length > 0 ? (
                <pre>{JSON.stringify(result.errors, null, 2)}</pre>
              ) : (
                <p>
                  Preview #{result.id} is ready. Close this dialog and choose
                  Commit from the import history.
                </p>
              )}
              <pre>{JSON.stringify(result.rows.slice(0, 10), null, 2)}</pre>
            </>
          ) : (
            <pre>{JSON.stringify(result, null, 2)}</pre>
          )}
          {(result.token || result.secret) && (
            <div className="notice">
              Copy this secret before closing. It will not be shown again.
            </div>
          )}
          <button className="button" onClick={onClose}>
            Done
          </button>
        </div>
      ) : (
        <form onSubmit={submit}>
          <div className="modal-body">
            {spec.description && <p className="muted">{spec.description}</p>}
            <ErrorBox error={error} />
            {spec.fields.map((f) => (
              <div className="form-field" key={f.key}>
                <label htmlFor={`field-${f.key}`}>
                  {f.label}
                  {f.required && <span className="required"> *</span>}
                </label>
                {f.resource ? (
                  <ReferenceSelect
                    field={f}
                    value={values[f.key]}
                    onChange={(v) => change(f.key, v)}
                  />
                ) : f.type === "lines" ? (
                  <LinesEditor
                    value={values[f.key]}
                    onChange={(v) => change(f.key, v)}
                    purchase={spec.initial?.kind === "purchase"}
                  />
                ) : f.type === "multi" ? (
                  <div className="check-grid">
                    {f.options!.map((o) => (
                      <label className="checkbox" key={o}>
                        <input
                          type="checkbox"
                          checked={values[f.key].includes(o)}
                          onChange={(e) =>
                            change(
                              f.key,
                              e.target.checked
                                ? [...values[f.key], o]
                                : values[f.key].filter((x: string) => x !== o),
                            )
                          }
                        />
                        {o}
                      </label>
                    ))}
                  </div>
                ) : f.type === "select" ? (
                  <select
                    id={`field-${f.key}`}
                    value={values[f.key]}
                    onChange={(e) => change(f.key, e.target.value)}
                  >
                    {f.options!.map((o) => (
                      <option key={o}>{o}</option>
                    ))}
                  </select>
                ) : f.type === "checkbox" ? (
                  <input
                    id={`field-${f.key}`}
                    type="checkbox"
                    checked={!!values[f.key]}
                    onChange={(e) => change(f.key, e.target.checked)}
                  />
                ) : f.type === "textarea" ||
                  f.type === "csv" ||
                  f.type === "json" ? (
                  <>
                    <textarea
                      id={`field-${f.key}`}
                      rows={f.type === "csv" ? 9 : 3}
                      required={f.required}
                      value={values[f.key]}
                      onChange={(e) => change(f.key, e.target.value)}
                    />
                    {f.type === "csv" && (
                      <input
                        aria-label="Upload CSV"
                        type="file"
                        accept=".csv,text/csv"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (file) {
                            if (file.size > 1048576) {
                              setError("Maximum file size is 1 MiB.");
                              return;
                            }
                            change(f.key, await file.text());
                          }
                        }}
                      />
                    )}
                  </>
                ) : (
                  <input
                    id={`field-${f.key}`}
                    type={f.type === "ids" ? "text" : f.type || "text"}
                    step={f.type === "number" ? "0.0001" : undefined}
                    required={f.required}
                    value={values[f.key]}
                    onChange={(e) => change(f.key, e.target.value)}
                  />
                )}
              </div>
            ))}
          </div>
          <div className="modal-footer">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
            >
              Cancel
            </button>
            <button className="button" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Check size={16} />
              )}{" "}
              {busy
                ? "Saving…"
                : spec.command === "imports/preview"
                  ? "Validate & preview"
                  : "Confirm"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}

function App() {
  const { t } = useTranslation();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState<Page>("overview");
  const [tab, setTab] = useState("balances");
  const [mobile, setMobile] = useState(false);
  const [revision, setRevision] = useState(0);
  const [modal, setModal] = useState<FormSpec | null>(null);
  const [detail, setDetail] = useState<{ resource: string; row: Row } | null>(
    null,
  );
  const [dashboard, setDashboard] = useState<Row | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [mfaSecret, setMfaSecret] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  useEffect(() => {
    api("session/")
      .then((r) => setUser(r.user))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (user && (!user.mfa_required || user.mfa_verified))
      api("dashboard/")
        .then((r) => {
          setDashboard(r);
          setError("");
        })
        .catch((e) => setError(e.message));
  }, [user, revision]);
  const refresh = () => {
    clearCache();
    setRevision((r) => r + 1);
  };
  const open = (name: string, initial: Row = {}) => {
    const { __update, ...values } = initial;
    setModal({
      ...forms[name],
      ...(__update
        ? {
            title: `Edit ${forms[name].title.replace(/^Add /, "").toLowerCase()}`,
            command: `${forms[name].command}/${__update}/update/`.replace(
              /\/$/,
              "",
            ),
          }
        : {}),
      initial: { ...forms[name].initial, ...values },
    });
  };
  const admin = user?.role === "administrator";
  function can(name: string) {
    if (admin) return true;
    const role = user?.role;
    const allowed: Record<string, string[]> = {
      purchasing: [
        "product",
        "partner",
        "purchase",
        "approve",
        "cancel",
        "receive",
        "policy",
        "forecast",
        "accept",
        "import",
        "importCommit",
      ],
      warehouse: [
        "bin",
        "receive",
        "allocate",
        "ship",
        "adjust",
        "count",
        "reverse",
        "transfer",
        "transferReceive",
        "return",
        "disposition",
      ],
      sales: ["partner", "sale", "approve", "cancel", "allocate"],
    };
    return !!role && !!allowed[role]?.includes(name);
  }
  const action = (
    name: string,
    labelText?: string,
    initial: Row = {},
    secondary = false,
  ) =>
    can(name) ? (
      <button
        className={`button ${secondary ? "secondary" : ""}`}
        onClick={() => open(name, initial)}
      >
        <Plus size={16} />
        {labelText || forms[name].title}
      </button>
    ) : null;
  function navigate(p: Page) {
    setPage(p);
    setDetail(null);
    setMobile(false);
    setTab(
      (
        {
          inventory: "balances",
          purchasing: "documents",
          fulfillment: "documents",
          planning: "policies",
          integrations: "imports",
          catalog: "products",
          locations: "warehouses",
          team: "memberships",
        } as Record<string, string>
      )[p] || "balances",
    );
  }
  async function login(e: React.FormEvent) {
    e.preventDefault();
    setSigningIn(true);
    setError("");
    try {
      const r = await api("session/", { username, password, otp });
      setUser(r.user);
      setPassword("");
      setOtp("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSigningIn(false);
    }
  }
  async function mfaBegin() {
    try {
      const r = await api("mfa/", { action: "begin" });
      setMfaSecret(r.secret);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function mfaFinish(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api("mfa/", { action: "confirm", otp });
      setUser(r.user);
      setOtp("");
      setMfaSecret("");
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (loading)
    return (
      <div className="loading-screen">
        <LoaderCircle className="spin" />
        Opening workspace…
      </div>
    );
  if (!user)
    return (
      <div className="login-page">
        <div className="login-story">
          <div className="brand">
            <span className="brand-mark">
              <Layers3 />
            </span>
            OSSCM
          </div>
          <div>
            <div className="eyebrow">YOUR OPERATIONS. CONNECTED.</div>
            <h1>
              A clear view.
              <br />
              From source
              <br />
              to shipment.
            </h1>
            <p>
              Inventory, purchasing, fulfillment, and demand planning in one
              open source workspace.
            </p>
          </div>
          <span className="login-footer">
            <ShieldCheck size={18} /> Self-hosted. Your data stays yours.
          </span>
        </div>
        <div className="login-form">
          <div className="eyebrow">SUPPLY CHAIN WORKSPACE</div>
          <h2>Welcome back</h2>
          <p className="muted">Sign in to keep things moving.</p>
          <ErrorBox error={error} />
          <form onSubmit={login}>
            <label>
              Username
              <input
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label>
              Authenticator code <span className="muted">(if enabled)</span>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
            </label>
            <button className="button" disabled={signingIn}>
              {signingIn ? "Signing in…" : "Sign in"}
              <ArrowRight size={18} />
            </button>
          </form>
          <p className="login-help">
            First installation? Create your administrator with the setup command
            in the project README.
          </p>
        </div>
      </div>
    );
  if (user.mfa_required && !user.mfa_verified)
    return (
      <div className="loading-screen">
        <ShieldCheck size={36} />
        <h2>Secure your administrator account</h2>
        <p>Set up your authenticator before opening the workspace.</p>
        <ErrorBox error={error} />
        {!mfaSecret ? (
          <button className="button" onClick={mfaBegin}>
            Set up authenticator
          </button>
        ) : (
          <form className="mfa-form" onSubmit={mfaFinish}>
            <p>Add this setup key to your authenticator:</p>
            <code>{mfaSecret}</code>
            <label>
              Six-digit code
              <input
                required
                inputMode="numeric"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
            </label>
            <button className="button">Verify & continue</button>
          </form>
        )}
      </div>
    );
  const title = pages.find((p) => p.id === page)!.title;
  const descriptions: Record<Page, string> = {
    overview: "Your supply chain, at a glance.",
    inventory: "Know what you have. Know where it is.",
    purchasing: "Keep supply moving, from order to receipt.",
    fulfillment: "Turn customer demand into delivered orders.",
    planning: "Make the next order an informed one.",
    integrations: "Connect your operations. Keep your data flowing.",
    catalog: "One shared source for products and business partners.",
    locations: "Organize your warehouses and the space within them.",
    team: "Give the right people the right access.",
    setup: "Build your operational foundation, one step at a time.",
  };
  const tabs: Record<string, string[]> = {
    inventory: ["balances", "movements", "transfers", "identities"],
    purchasing: ["documents"],
    fulfillment: ["documents", "reservations", "shipments"],
    planning: ["policies", "forecasts"],
    integrations: admin
      ? ["imports", "webhooks", "deliveries", "credentials"]
      : ["imports"],
    catalog: ["products", "partners"],
    locations: ["warehouses", "bins"],
    team: ["memberships", "audit"],
  };
  const primary: Record<string, string> = {
    products: "product",
    partners: "partner",
    warehouses: "warehouse",
    bins: "bin",
    policies: "policy",
    forecasts: "forecast",
    imports: "import",
    webhooks: "webhook",
    credentials: "credential",
    memberships: "user",
  };
  return (
    <div className="app">
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("overview");
          }}
        >
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          OSSCM<span className="version">BETA</span>
        </a>
        <div className="org-card">
          <span className="org-icon">{user.organization[0]}</span>
          <div>
            <strong>{user.organization}</strong>
            <small>Operations workspace</small>
          </div>
        </div>
        <nav>
          {pages.map((p) => (
            <React.Fragment key={p.id}>
              {p.group && <div className="nav-group">{p.group}</div>}
              <button
                className={page === p.id ? "active" : ""}
                onClick={() => navigate(p.id)}
              >
                <p.icon size={19} />
                {t(p.title)}
                {p.id === "inventory" && dashboard && (
                  <span className="nav-count">{dashboard.products}</span>
                )}
              </button>
            </React.Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-status">
            <span /> Self-hosted workspace
          </div>
          <div className="profile">
            <span className="avatar">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{user.username}</strong>
              <small>{nice(user.role)}</small>
            </div>
            <button
              title="Sign out"
              aria-label="Sign out"
              className="icon-button"
              onClick={async () => {
                await api("session/", { action: "logout" });
                setUser(null);
                setDashboard(null);
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle navigation"
              onClick={() => setMobile(!mobile)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <b>{title}</b>
          </div>
          <div className="topbar-right">
            <span className="date">
              {new Date().toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </span>
            <span className="secure-label">
              <ShieldCheck size={15} />
              Private instance
            </span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {page === "overview"
                  ? "CONTROL CENTER"
                  : "OPERATIONS WORKSPACE"}
              </div>
              <h1>{t(title)}</h1>
              <p>{descriptions[page]}</p>
            </div>
            <div className="heading-actions">
              <button
                className="button secondary icon-only"
                title="Refresh data"
                aria-label="Refresh data"
                onClick={refresh}
              >
                <RefreshCw size={17} />
              </button>
              {page === "overview"
                ? action("purchase", "New purchase order")
                : page === "purchasing"
                  ? action("purchase")
                  : page === "fulfillment"
                    ? action("sale")
                    : page === "inventory"
                      ? action("adjust")
                      : primary[tab] && page !== "setup"
                        ? action(primary[tab])
                        : null}
            </div>
          </div>
          <ErrorBox error={error} />
          {page === "overview" ? (
            <>
              <div className="overview-banner">
                <div>
                  <span className="banner-label">
                    <span /> LIVE OPERATIONS
                  </span>
                  <h2>Every movement tells a story.</h2>
                  <p>
                    Follow your inventory from incoming supply to outgoing
                    demand.
                  </p>
                  <button onClick={() => navigate("inventory")}>
                    Explore inventory <ArrowRight size={16} />
                  </button>
                </div>
                <div className="flow-art" aria-hidden="true">
                  <span>
                    <Package />
                  </span>
                  <i />
                  <span className="central">
                    <Warehouse size={33} />
                  </span>
                  <i />
                  <span>
                    <Truck />
                  </span>
                  <div className="flow-caption">
                    SOURCE <b>→</b> STOCK <b>→</b> SHIP
                  </div>
                </div>
              </div>
              <div className="stats">
                {[
                  {
                    label: "Available inventory",
                    value: dashboard?.available,
                    icon: Boxes,
                    target: "inventory",
                  },
                  {
                    label: "Inbound units",
                    value: dashboard?.inbound,
                    icon: ArrowDownLeft,
                    target: "purchasing",
                  },
                  {
                    label: "Reserved units",
                    value: dashboard?.reserved,
                    icon: ClipboardList,
                    target: "fulfillment",
                  },
                  {
                    label: "Open sales orders",
                    value: dashboard?.open_orders,
                    icon: Truck,
                    target: "fulfillment",
                  },
                ].map((s) => (
                  <button
                    className="stat"
                    key={s.label}
                    onClick={() => navigate(s.target as Page)}
                  >
                    <div>
                      <span>{s.label}</span>
                      <s.icon size={18} />
                    </div>
                    <strong>{dashboard ? number(s.value) : "—"}</strong>
                    <small>
                      View supporting records <ArrowUpRight size={13} />
                    </small>
                  </button>
                ))}
              </div>
              <div className="overview-grid">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Recent stock movements</h2>
                      <p>The latest activity across your locations</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => {
                        navigate("inventory");
                        setTab("movements");
                      }}
                    >
                      View all <ArrowRight size={15} />
                    </button>
                  </div>
                  {dashboard?.recent?.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Movement</th>
                            <th>Product</th>
                            <th>Location</th>
                            <th className="numeric">Quantity</th>
                            <th>When</th>
                          </tr>
                        </thead>
                        <tbody>
                          {dashboard.recent.map((r: Row) => (
                            <tr
                              key={r.id}
                              onClick={() =>
                                api(`movements/${r.id}/`)
                                  .then((row) =>
                                    setDetail({ resource: "movements", row }),
                                  )
                                  .catch((e) => setError(e.message))
                              }
                            >
                              <td>
                                <span
                                  className={`movement-dot ${Number(r.quantity) < 0 ? "out" : ""}`}
                                />
                                {nice(r.kind)}
                              </td>
                              <td className="mono">{r.sku}</td>
                              <td>{r.warehouse}</td>
                              <td className="numeric">
                                {Number(r.quantity) > 0 ? "+" : ""}
                                {number(r.quantity)}
                              </td>
                              <td className="muted">
                                {timestamp(r.created_at)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty
                      title="Your activity starts here"
                      description="Receive your first purchase order or record opening stock to see movements."
                      onClick={() => navigate("setup")}
                      button="Set up your workspace"
                    />
                  )}
                </section>
                <section className="panel attention">
                  <div className="panel-heading">
                    <div>
                      <h2>Needs attention</h2>
                      <p>Exceptions worth a closer look</p>
                    </div>
                    <span className="count-badge">
                      {dashboard?.alerts?.length || 0}
                    </span>
                  </div>
                  {dashboard?.alerts?.length ? (
                    <div className="alert-list">
                      {dashboard.alerts.map((a: Row, i: number) => (
                        <button
                          key={i}
                          onClick={() =>
                            api(`${a.resource}/${a.id}/`)
                              .then((row) =>
                                setDetail({ resource: a.resource, row }),
                              )
                              .catch((e) => setError(e.message))
                          }
                        >
                          <span className={`alert-icon ${a.type}`}>
                            <Activity size={16} />
                          </span>
                          <div>
                            <small>{nice(a.type)}</small>
                            <p>{a.label}</p>
                          </div>
                          <ChevronRight size={15} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="all-clear">
                      <span>
                        <Check size={24} />
                      </span>
                      <h3>No active alerts</h3>
                      <p>
                        Low stock, overdue orders, and expiring inventory will
                        appear here.
                      </p>
                    </div>
                  )}
                </section>
              </div>
              <div className="bottom-strip">
                <span>
                  <Warehouse size={18} />
                  <b>{dashboard?.warehouses || 0}</b> warehouses
                </span>
                <span>
                  <Package size={18} />
                  <b>{dashboard?.products || 0}</b> products
                </span>
                <span>
                  <Truck size={18} />
                  <b>{number(dashboard?.in_transit)}</b> units in transit
                </span>
                <span>
                  <ShieldCheck size={18} />
                  <b>{number(dashboard?.quarantined)}</b> quarantined units
                </span>
              </div>
            </>
          ) : page === "setup" ? (
            <div className="setup-grid">
              {[
                {
                  n: "01",
                  title: "Map your locations",
                  description:
                    "Create a warehouse, then add receiving and storage bins.",
                  actions: ["warehouse", "bin"],
                },
                {
                  n: "02",
                  title: "Define your catalog",
                  description:
                    "Add products with base units, purchasing conversions, and optional tracking.",
                  actions: ["product", "import"],
                },
                {
                  n: "03",
                  title: "Connect your partners",
                  description:
                    "Create suppliers and customers for purchasing and fulfillment.",
                  actions: ["partner"],
                },
                {
                  n: "04",
                  title: "Start moving stock",
                  description:
                    "Create a purchase order, approve it, and receive goods into a bin.",
                  actions: ["purchase", "adjust"],
                },
                {
                  n: "05",
                  title: "Plan replenishment",
                  description:
                    "Set lead times, reorder levels, and pack sizes for each location.",
                  actions: ["policy"],
                },
                {
                  n: "06",
                  title: "Bring your team",
                  description:
                    "Assign roles and warehouse access. Enable authenticator protection.",
                  actions: ["user"],
                },
              ].map((s) => (
                <section className="panel setup-card" key={s.n}>
                  <span className="step-number">{s.n}</span>
                  <h2>{s.title}</h2>
                  <p>{s.description}</p>
                  <div className="toolbar">
                    {s.actions.map((a) => (
                      <React.Fragment key={a}>
                        {action(a, undefined, {}, true)}
                      </React.Fragment>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <>
              {page === "planning" && (
                <div className="notice">
                  <TrendingUp size={20} />
                  <div>
                    <strong>Recommendations you can explain.</strong>
                    <p>
                      Compare forecasting methods against historical demand.
                      Sparse history uses your manual reorder policy. Every
                      purchase stays under your control.
                    </p>
                  </div>
                </div>
              )}
              {page === "team" && !user.mfa_enabled && (
                <div className="notice">
                  <ShieldCheck size={20} />
                  <div>
                    <strong>Add authenticator protection</strong>
                    {mfaSecret ? (
                      <form onSubmit={mfaFinish}>
                        <code>{mfaSecret}</code>
                        <input
                          aria-label="Authenticator code"
                          value={otp}
                          onChange={(e) => setOtp(e.target.value)}
                        />
                        <button className="button small">Verify code</button>
                      </form>
                    ) : (
                      <button className="text-button" onClick={mfaBegin}>
                        Set up MFA <ArrowRight size={15} />
                      </button>
                    )}
                  </div>
                </div>
              )}
              <section className="panel">
                <div className="tabs">
                  {(page === "team" && !admin ? [] : tabs[page] || []).map(
                    (name) => (
                      <button
                        className={tab === name ? "active" : ""}
                        key={name}
                        onClick={() => setTab(name)}
                      >
                        {nice(name)}
                      </button>
                    ),
                  )}
                </div>
                {page === "inventory" && (
                  <div className="toolbar secondary-toolbar">
                    {[
                      "count",
                      "transfer",
                      "transferReceive",
                      "disposition",
                    ].map((a) => (
                      <React.Fragment key={a}>
                        {action(a, undefined, {}, true)}
                      </React.Fragment>
                    ))}
                  </div>
                )}
                {page === "team" && !admin ? (
                  <Empty
                    title="Your account security"
                    description={
                      user.mfa_enabled
                        ? "Authenticator protection is enabled. Contact an administrator for access changes."
                        : "Use the setup action above to enable authenticator protection."
                    }
                  />
                ) : (
                  <ResourceTable
                    key={`${page}-${tab}`}
                    resource={tab}
                    query={
                      tab === "documents"
                        ? `kind=${page === "purchasing" ? "purchase" : "sale"}`
                        : ""
                    }
                    revision={revision}
                    onDetail={(row) => setDetail({ resource: tab, row })}
                  />
                )}
              </section>
            </>
          )}
          <footer>
            <span>
              OSSCM{" "}
              <span className="muted">
                / Open source supply chain management
              </span>
            </span>
            <a href="/api/docs/" target="_blank" rel="noreferrer">
              API reference <ExternalLink size={12} />
            </a>
          </footer>
        </main>
      </div>
      {modal && (
        <Modal spec={modal} onClose={() => setModal(null)} onDone={refresh} />
      )}{" "}
      {detail && (
        <Detail
          detail={detail}
          revision={revision}
          onClose={() => setDetail(null)}
          open={open}
          can={can}
          onDetail={setDetail}
        />
      )}
    </div>
  );
}
function Empty({
  title,
  description,
  onClick,
  button,
}: {
  title: string;
  description: string;
  onClick?: () => void;
  button?: string;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Database size={25} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {onClick && (
        <button className="button secondary small" onClick={onClick}>
          {button}
          <ArrowRight size={14} />
        </button>
      )}
    </div>
  );
}
function Cell({ name, value }: { name: string; value: any }) {
  const [resolved, setResolved] = useState("");
  useEffect(() => {
    let live = true;
    if (relationship[name] && value)
      lookup(relationship[name], value)
        .then((r) => live && setResolved(label(r)))
        .catch(() => {});
    return () => {
      live = false;
    };
  }, [name, value]);
  if (value === null || value === undefined || value === "")
    return <span className="muted">—</span>;
  if (relationship[name])
    return <span title={`#${value}`}>{resolved || `#${value}`}</span>;
  if (typeof value === "boolean")
    return (
      <span className={`badge ${value ? "complete" : "draft"}`}>
        {value ? "Yes" : "No"}
      </span>
    );
  if (["status", "state", "kind", "tracking", "role"].includes(name))
    return <span className={`badge ${value}`}>{nice(String(value))}</span>;
  if (name.endsWith("_at")) return <>{timestamp(value)}</>;
  if (
    [
      "quantity",
      "on_hand",
      "reserved",
      "processed",
      "recommended",
      "purchase_factor",
      "reserved_delta",
      "received",
    ].includes(name)
  )
    return <span className="mono">{number(value)}</span>;
  if (Array.isArray(value)) return <>{value.join(", ") || "—"}</>;
  if (typeof value === "object") return <>{JSON.stringify(value)}</>;
  return <>{String(value)}</>;
}
function ResourceTable({
  resource,
  query = "",
  revision,
  onDetail,
}: {
  resource: string;
  query?: string;
  revision: number;
  onDetail: (row: Row) => void;
}) {
  const [data, setData] = useState<Row | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let live = true;
    setBusy(true);
    const timer = setTimeout(() => {
      api(`${resource}/?${query}&page=${page}&q=${encodeURIComponent(search)}`)
        .then((r) => {
          if (live) {
            setData(r);
            setError("");
          }
        })
        .catch((e) => live && setError(e.message))
        .finally(() => live && setBusy(false));
    }, 160);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [resource, query, page, search, revision]);
  const columns = tableColumns[resource] || ["id"];
  return (
    <>
      <div className="table-toolbar">
        <div className="search-box">
          <Search size={16} />
          <input
            aria-label={`Search ${resource}`}
            placeholder={`Search ${nice(resource).toLowerCase()}…`}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <span className="muted">
          {data?.count ?? 0} records{" "}
          {busy && <LoaderCircle className="spin inline" size={13} />}
        </span>
      </div>
      <ErrorBox error={error} />
      {data?.results?.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                {columns.map((c) => (
                  <th key={c}>{nice(c)}</th>
                ))}
                <th>
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((row: Row) => (
                <tr key={row.id}>
                  <td className="muted mono">#{row.id}</td>
                  {columns.map((c) => (
                    <td key={c}>
                      <Cell name={c} value={row[c]} />
                    </td>
                  ))}
                  <td>
                    <button
                      className="icon-button"
                      title="Open details"
                      aria-label={`Open ${resource} ${row.id}`}
                      onClick={() => onDetail(row)}
                    >
                      <ChevronRight size={18} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !busy && !error ? (
        <Empty
          title={`No ${resource} yet`}
          description="Add your first record to start building a connected view of your operations."
        />
      ) : busy ? (
        <div className="table-loading">
          <LoaderCircle className="spin" size={20} />
          Loading records…
        </div>
      ) : null}
      <div className="pagination">
        <span>Page {page} · Up to 50 records per page</span>
        <div>
          <button
            className="icon-button"
            aria-label="Previous page"
            disabled={page === 1 || busy}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Next page"
            disabled={!data?.next || busy}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight size={17} />
          </button>
        </div>
      </div>
    </>
  );
}
function Detail({
  detail,
  revision,
  onClose,
  open,
  can,
  onDetail,
}: {
  detail: { resource: string; row: Row };
  revision: number;
  onClose: () => void;
  open: (name: string, initial?: Row) => void;
  can: (name: string) => boolean;
  onDetail: (d: { resource: string; row: Row }) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [row, setRow] = useState(detail.row);
  const [error, setError] = useState("");
  const [trace, setTrace] = useState<Row | null>(null);
  const resource = detail.resource;
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    setRow(detail.row);
    setTrace(null);
    setError("");
    api(`${resource}/${detail.row.id}/`)
      .then(setRow)
      .catch((e) => setError(e.message));
    if (resource === "identities")
      api(`trace/${detail.row.id}/`)
        .then(setTrace)
        .catch((e) => setError(e.message));
  }, [detail, revision, resource]);
  const button = (name: string, initial: Row) =>
    can(name) ? (
      <button
        key={name}
        className="button secondary small"
        onClick={() => open(name, initial)}
      >
        {forms[name].title}
        <ArrowRight size={14} />
      </button>
    ) : null;
  return (
    <dialog ref={dialog} className="modal detail-modal" onCancel={onClose}>
      <div className="modal-header">
        <div>
          <div className="eyebrow">
            {nice(resource)} / #{row.id}
          </div>
          <h2>{label(row)}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close details"
          onClick={onClose}
        >
          <X />
        </button>
      </div>
      <div className="modal-body">
        <ErrorBox error={error} />
        <div className="detail-fields">
          {Object.entries(row)
            .filter(
              ([k, v]) =>
                !["result", "rows", "errors", "metadata", "data"].includes(k) &&
                typeof v !== "object",
            )
            .map(([k, v]) => (
              <div key={k}>
                <small>{nice(k)}</small>
                <strong>
                  <Cell name={k} value={v} />
                </strong>
              </div>
            ))}
        </div>
        <div className="toolbar">
          {["products", "partners", "warehouses", "bins", "policies"].includes(
            resource,
          ) &&
            (() => {
              const name = (
                {
                  products: "product",
                  partners: "partner",
                  warehouses: "warehouse",
                  bins: "bin",
                  policies: "policy",
                } as Record<string, string>
              )[resource];
              return can(name) ? (
                <button
                  className="button secondary small"
                  onClick={() => open(name, { ...row, __update: row.id })}
                >
                  Edit record
                </button>
              ) : null;
            })()}
          {resource === "documents" && (
            <>
              {row.status === "draft" &&
                button("approve", { document: row.id })}
              {!["complete", "cancelled"].includes(row.status) &&
                button("cancel", { document: row.id })}
            </>
          )}
          {resource === "lines" && (
            <>
              {button("receive", { line: row.id })}
              {button("allocate", { line: row.id })}
            </>
          )}
          {resource === "reservations" &&
            button("ship", {
              line: row.line,
              reservation: row.id,
              quantity: row.quantity,
            })}
          {resource === "balances" && (
            <>
              {button("transfer", { balance: row.id })}
              {row.state === "quarantine" &&
                button("disposition", { balance: row.id })}
              <button
                className="button secondary small"
                onClick={() =>
                  api(`identities/${row.identity}/`)
                    .then((r) => onDetail({ resource: "identities", row: r }))
                    .catch((e) => setError(e.message))
                }
              >
                Trace lot / serial <ArrowRight size={14} />
              </button>
            </>
          )}
          {resource === "movements" && (
            <>
              {["receipt", "shipment"].includes(row.kind) &&
                button("return", { movement: row.id })}
              {["count", "adjustment"].includes(row.kind) &&
                button("reverse", { movement: row.id })}
            </>
          )}
          {resource === "transfers" &&
            Number(row.received) < Number(row.quantity) &&
            button("transferReceive", {
              transfer: row.id,
              quantity: Number(row.quantity) - Number(row.received),
            })}
          {resource === "policies" && button("forecast", { policy: row.id })}
          {resource === "forecasts" &&
            !row.document &&
            Number(row.recommended) > 0 &&
            button("accept", { forecast: row.id })}
          {resource === "imports" &&
            !row.committed_at &&
            !row.errors?.length &&
            button("importCommit", { batch: row.id })}
          {resource === "deliveries" && button("replay", { delivery: row.id })}
          {resource === "credentials" &&
            !row.revoked &&
            button("revoke", { credential: row.id })}
          {resource === "memberships" &&
            button("permissions", {
              membership: row.id,
              role: row.role,
              warehouses: row.warehouses?.join(","),
              active: row.active,
            })}
          {resource === "webhooks" &&
            button("webhookToggle", { webhook: row.id, active: !row.active })}
        </div>
        {resource === "forecasts" && <Forecast result={row.result} />}{" "}
        {resource === "documents" && (
          <>
            <h3>Order lines</h3>
            <ResourceTable
              resource="lines"
              query={`document=${row.id}`}
              revision={revision}
              onDetail={(r) => onDetail({ resource: "lines", row: r })}
            />
          </>
        )}
        {resource === "lines" && (
          <>
            <h3>Reservations</h3>
            <ResourceTable
              resource="reservations"
              query={`line=${row.id}`}
              revision={revision}
              onDetail={(r) => onDetail({ resource: "reservations", row: r })}
            />
          </>
        )}
        {["products", "warehouses", "balances"].includes(resource) && (
          <>
            <h3>Movement history</h3>
            <ResourceTable
              resource="movements"
              query={`${resource === "products" ? "product" : resource === "warehouses" ? "warehouse" : "balance"}=${row.id}`}
              revision={revision}
              onDetail={(r) => onDetail({ resource: "movements", row: r })}
            />
          </>
        )}
        {trace && (
          <>
            <h3>Traceability history</h3>
            <p className="muted">
              Receipts, allocations, shipments, transfers, and returns for this
              stock identity.
            </p>
            <ResourceTable
              resource="movements"
              query={`identity=${row.id}`}
              revision={revision}
              onDetail={(r) => onDetail({ resource: "movements", row: r })}
            />
          </>
        )}
        {["imports", "audit"].includes(resource) && (
          <pre>
            {JSON.stringify(
              row.errors?.length ? row.errors : row.rows || row.data,
              null,
              2,
            )}
          </pre>
        )}
      </div>
    </dialog>
  );
}

export default App;
