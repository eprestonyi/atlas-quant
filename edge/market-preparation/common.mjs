import { ApiError } from "../errors.mjs";
import { sha } from "../runtime.mjs";
export const SCOPE_FORMAT = "atlas.quant.universe_scope";
export const PROFILE = "pooled_asset_1000_v1";
export const LIMITS = Object.freeze({
  scopeBytes: 256 * 1024,
  scopeSymbols: 10000,
  savedScopes: 200,
  savedPlans: 200,
  planBytes: 1536 * 1024,
});
export const fail = (code, message, status = 400) => {
  throw new ApiError(code, message, status);
};
export function object(value, allowed, label = "参数") {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((k) => !allowed.includes(k))
  )
    fail("INVALID_SCOPE", `${label}结构或字段无效`);
}
export function id(value) {
  if (
    typeof value !== "string" ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      value,
    )
  )
    fail("INVALID_SCOPE", "身份格式无效");
  return value;
}
export function hash(value) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value))
    fail("INVALID_SCOPE", "内容根格式无效");
  return value;
}
export function date(value) {
  if (typeof value !== "string" || !/^\d{8}$/.test(value))
    fail("INVALID_SCOPE", "日期须为YYYYMMDD");
  const iso = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}`;
  const n = Date.parse(iso + "T00:00:00Z");
  if (!Number.isFinite(n) || new Date(n).toISOString().slice(0, 10) !== iso)
    fail("INVALID_SCOPE", "日期不存在");
  return n;
}
function ordered(value) {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, ordered(value[k])]),
    );
  return value;
}
export const canonical = (value) => JSON.stringify(ordered(value));
export const digest = (value) => sha(canonical(value));
export const byteLength = (value) =>
  new TextEncoder().encode(typeof value === "string" ? value : canonical(value))
    .length;
export function scopeRef(row) {
  return {
    scopeId: row.id,
    scopeRoot: row.scope_root,
    format: SCOPE_FORMAT,
    version: 1,
  };
}
export function validateScopeRef(value) {
  object(
    value,
    ["scopeId", "scopeRoot", "format", "version"],
    "完整股票池引用",
  );
  id(value.scopeId);
  hash(value.scopeRoot);
  if (value.format !== SCOPE_FORMAT || value.version !== 1)
    fail("INVALID_SCOPE", "完整股票池引用版本无效");
  return value;
}
