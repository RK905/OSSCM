export type Row = Record<string, any>;
let csrf = "";
const lookups = new Map<string, { at: number; result: Promise<any> }>();
export function clearCache() {
  lookups.clear();
}
export function lookup(resource: string, id: unknown): Promise<any> {
  const path = `${resource}/${id}/`;
  const cached = lookups.get(path);
  if (cached && Date.now() - cached.at < 30000) return cached.result;
  const result = api(path).catch((e) => {
    lookups.delete(path);
    throw e;
  });
  lookups.set(path, { at: Date.now(), result });
  return result;
}
export async function api(
  path: string,
  data?: unknown,
  key?: string,
): Promise<any> {
  const response = await fetch(
    path.startsWith("/api/") ? path : `/api/v1/${path}`,
    {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(data !== undefined
          ? { "X-CSRFToken": csrf, ...(key ? { "Idempotency-Key": key } : {}) }
          : {}),
      },
      ...(data !== undefined
        ? { method: "POST", body: JSON.stringify(data) }
        : {}),
    },
  );
  const result = await response
    .json()
    .catch(() => ({ detail: "Server returned an unreadable response." }));
  if (result.csrf) csrf = result.csrf;
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : JSON.stringify(result),
    );
  return result;
}
export function command(
  name: string,
  data: unknown,
  key = crypto.randomUUID(),
) {
  clearCache();
  return api(`commands/${name}/`, data, key);
}
export function number(value: unknown) {
  return Number(value || 0).toLocaleString(undefined, {
    maximumFractionDigits: 4,
  });
}
export function label(row: Row) {
  return row.username
    ? row.username
    : row.product_sku
      ? `${row.product_sku} · ${row.code || row.product_name}${row.warehouse ? " · warehouse #" + row.warehouse : ""}`
      : row.sku
        ? `${row.sku} · ${row.name}`
        : row.reference ||
          row.name ||
          row.code ||
          (row.product
            ? `Product #${row.product} · ${row.quantity ?? row.on_hand ?? ""}`
            : row.line
              ? `Line #${row.line} · ${row.quantity}`
              : `#${row.id}`);
}
