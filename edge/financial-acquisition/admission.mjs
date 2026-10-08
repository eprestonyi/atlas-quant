/** Private rollout admission and small global budgets; no billing/refunds. */
import { UUID } from "../financial/common.mjs";
export function acquisitionAdmission(env) {
  const audience = env.FINANCIAL_ACQUISITION_AUDIENCE || "canary";
  let owners = [];
  if (!["canary", "public"].includes(audience)) return null;
  try {
    owners =
      audience === "public"
        ? []
        : JSON.parse(env.FINANCIAL_ACQUISITION_CANARY_OWNERS || "[]");
  } catch {
    return null;
  }
  if (
    !["canary", "public"].includes(audience) ||
    !Array.isArray(owners) ||
    owners.length > 64 ||
    new Set(owners).size !== owners.length ||
    owners.some((x) => typeof x !== "string" || !UUID.test(x))
  )
    return null;
  function bounded(key, fallback, max) {
    const raw = env[key];
    if (raw === undefined || raw === "") return fallback;
    if (typeof raw !== "string" || !/^\d+$/.test(raw)) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n >= 1 && n <= max ? n : null;
  }
  const maxActive = bounded("FINANCIAL_ACQUISITION_MAX_ACTIVE", 8, 32),
    maxDaily = bounded("FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS", 60, 1000);
  if (maxActive === null || maxDaily === null) return null;
  return { audience, owners, maxActive, maxDaily };
}
export function ownerAllowed(env, owner) {
  const a = acquisitionAdmission(env);
  return (
    !!a &&
    (a.audience === "public" ||
      (owner === undefined ? a.owners.length > 0 : a.owners.includes(owner)))
  );
}
export function utcWindow(now) {
  const value = new Date(now);
  if (!Number.isFinite(value.getTime())) throw Error("Invalid server clock");
  const day = value.toISOString().slice(0, 10),
    start = day + "T00:00:00.000Z",
    end = new Date(Date.parse(start) + 86400000).toISOString();
  return { day, start, end };
}
export async function budgetState(env, now) {
  const a = acquisitionAdmission(env),
    window = utcWindow(now);
  const used = Number(
    (
      await env.DB.prepare(
        "SELECT COUNT(*) n FROM financial_acquisition_requests WHERE created_at>=? AND created_at<?",
      )
        .bind(window.start, window.end)
        .first()
    ).n,
  );
  return {
    utcDay: window.day,
    maxActiveJobs: a?.maxActive ?? 0,
    maxNewProviderRequestsPerUtcDay: a?.maxDaily ?? 0,
    newProviderRequestsUsed: used,
    newProviderRequestsRemaining: Math.max(0, (a?.maxDaily ?? 0) - used),
  };
}
/** SQL fragment shared by both read-only heartbeat and durable claim. */
export function claimEligibility(env, scope) {
  const a = acquisitionAdmission(env);
  if (!a || (a.audience === "canary" && !a.owners.length))
    return { sql: "0", bindings: [] };
  return {
    sql: `status='queued' AND json_extract(spec,'$.requests[0].authorizationScope')=? AND NOT EXISTS(SELECT 1 FROM meta WHERE key='financial_acquisition_maintenance' AND value='paused')${a.audience === "canary" ? ` AND owner IN (${a.owners.map(() => "?").join(",")})` : ""}`,
    bindings: [scope, ...(a.audience === "canary" ? a.owners : [])],
  };
}
