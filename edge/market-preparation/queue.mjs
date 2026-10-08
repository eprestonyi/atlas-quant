/** One irreversible provider intent per authorized request; control delivery may retry. */
import { NOW, random, parse } from "../runtime.mjs";
import { object } from "../financial/common.mjs";
import { id, hash, fail, canonical, digest, LIMITS } from "./common.mjs";
import { MARKET_LIMITS, marketAuthorizationScope } from "./planner.mjs";
export const MARKET_CAPABILITY = "market-acquire/1";
const terminal = new Set(["completed", "failed", "cancelled"]);
export function enabled(env) {
  return (
    env.MARKET_ACQUISITION_ENABLED === "true" &&
    env.TUSHARE_PUBLIC_AUTHORIZED === "true" &&
    !!marketAuthorizationScope(env)
  );
}
export function requireEnabled(env, job = null) {
  if (!enabled(env))
    fail("MARKET_ACQUISITION_DISABLED", "独立市场准备尚未启用", 503);
  if (job && job.authorization_scope !== marketAuthorizationScope(env))
    fail("MARKET_AUTHORIZATION_CHANGED", "市场来源授权范围已变化", 409);
}
export async function loadPlan(env, owner, planId, expected = null) {
  const row = await env.DB.prepare(
    "SELECT * FROM quant_market_plans WHERE id=? AND owner=?",
  )
    .bind(id(planId), owner)
    .first();
  if (!row) fail("NOT_FOUND", "市场计划不存在", 404);
  let p;
  try {
    p = JSON.parse(row.spec);
  } catch {
    fail("MARKET_PLAN_INTEGRITY", "市场计划损坏", 409);
  }
  const { planRoot, ...content } = p;
  if (
    row.spec.length > LIMITS.planBytes ||
    canonical(p) !== row.spec ||
    planRoot !== row.plan_root ||
    (await digest(content)) !== planRoot ||
    (expected && expected !== planRoot)
  )
    fail("MARKET_PLAN_INTEGRITY", "市场计划根不匹配", 409);
  return { row, plan: p };
}
export const jobView = (j) => ({
  id: j.id,
  planId: j.plan_id,
  status: j.status,
  phase: j.phase,
  createdAt: j.created_at,
  updatedAt: j.updated_at,
  error: parse(j.error),
  result: parse(j.result),
});
export async function ownedJob(env, owner, jobId) {
  const j = await env.DB.prepare(
    "SELECT * FROM quant_market_jobs WHERE id=? AND owner=?",
  )
    .bind(id(jobId), owner)
    .first();
  if (!j) fail("NOT_FOUND", "市场准备任务不存在", 404);
  return j;
}
export async function expire(env) {
  const now = NOW();
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE quant_market_requests SET state='outcome_unknown',updated_at=? WHERE state='intent' AND job_id IN(SELECT id FROM quant_market_jobs WHERE status IN('running','cancel_requested') AND (lease_until<? OR deadline<?))",
    ).bind(now, now, now),
    env.DB.prepare(
      "UPDATE quant_market_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'failed' END,error=?,updated_at=? WHERE status IN('running','cancel_requested') AND (lease_until<? OR deadline<?)",
    ).bind(
      JSON.stringify({
        code: "MARKET_LEASE_EXPIRED",
        message: "市场准备租约到期；无原始回执的已发请求不可自动重读",
      }),
      now,
      now,
      now,
    ),
  ]);
}
export async function leased(env, jobId, token, allowTerminal = false) {
  const j = await env.DB.prepare(
    "SELECT * FROM quant_market_jobs WHERE id=? AND lease_token=?",
  )
    .bind(id(jobId), id(token))
    .first();
  if (!j) fail("STALE_LEASE", "市场准备租约不匹配", 409);
  if (allowTerminal && terminal.has(j.status)) return j;
  if (
    !["running", "cancel_requested"].includes(j.status) ||
    j.lease_until < NOW() ||
    j.deadline < NOW()
  ) {
    await expire(env);
    fail("STALE_LEASE", "市场准备租约已终止", 409);
  }
  return j;
}
export async function start(env, owner, planId, v) {
  object(v, ["requestId", "planRoot"]);
  id(v.requestId);
  hash(v.planRoot);
  const previous = await env.DB.prepare(
    "SELECT * FROM quant_market_jobs WHERE owner=? AND request_id=?",
  )
    .bind(owner, v.requestId)
    .first();
  if (previous) {
    if (previous.plan_id !== planId || previous.plan_root !== v.planRoot)
      fail("START_CONFLICT", "相同启动标识不能用于另一计划", 409);
    return { job: jobView(previous), idempotent: true };
  }
  requireEnabled(env);
  const { plan } = await loadPlan(env, owner, planId, v.planRoot);
  if (
    plan.blockedReasons.length ||
    plan.authorizationScope !== marketAuthorizationScope(env)
  )
    fail("MARKET_PLAN_BLOCKED", "计划存在阻断或授权范围已改变", 409);
  const pause = await env.DB.prepare(
    "SELECT value FROM meta WHERE key='market_acquisition_maintenance'",
  ).first();
  if (pause?.value === "paused")
    fail("MARKET_MAINTENANCE", "市场准备正在维护", 409);
  const jid = random(),
    now = NOW();
  await env.DB.prepare(
    "INSERT OR IGNORE INTO quant_market_jobs(id,owner,request_id,plan_id,plan_root,authorization_scope,status,created_at,updated_at) SELECT ?,?,?,?,?,?,'queued',?,? WHERE NOT EXISTS(SELECT 1 FROM quant_market_jobs WHERE owner=? AND status IN('queued','running','cancel_requested')) AND (SELECT count(*) FROM quant_market_jobs WHERE status IN('queued','running','cancel_requested'))<2",
  )
    .bind(
      jid,
      owner,
      v.requestId,
      planId,
      plan.planRoot,
      plan.authorizationScope,
      now,
      now,
      owner,
    )
    .run();
  const j = await env.DB.prepare(
    "SELECT * FROM quant_market_jobs WHERE owner=? AND request_id=?",
  )
    .bind(owner, v.requestId)
    .first();
  if (!j)
    fail("MARKET_QUEUE_FULL", "独立市场准备已有任务；完整范围不会缩小", 409);
  return { job: jobView(j), idempotent: j.id !== jid };
}
async function mayClaim(env) {
  if (!enabled(env)) return false;
  const p = await env.DB.prepare(
    "SELECT value FROM meta WHERE key='market_acquisition_maintenance'",
  ).first();
  return p?.value !== "paused";
}
export async function claim(env, v) {
  object(v, ["requestId", "capability", "engineVersion"]);
  id(v.requestId);
  if (
    v.capability !== MARKET_CAPABILITY ||
    typeof v.engineVersion !== "string" ||
    v.engineVersion.length > 80
  )
    fail("MARKET_CAPABILITY", "市场准备协议不匹配");
  await expire(env);
  const old = await env.DB.prepare(
    "SELECT * FROM quant_market_claims WHERE request_id=?",
  )
    .bind(v.requestId)
    .first();
  if (!old && !(await mayClaim(env)))
    return {
      claim: { requestId: v.requestId, status: "empty", jobId: null },
      job: null,
    };
  const now = NOW(),
    token = random(),
    until = new Date(Date.now() + 120000).toISOString();
  const results = await env.DB.batch([
    env.DB.prepare(
      "INSERT OR IGNORE INTO quant_market_claims(request_id,job_id,created_at) SELECT ?,id,? FROM quant_market_jobs WHERE status='queued' AND authorization_scope=? ORDER BY created_at,id LIMIT 1",
    ).bind(v.requestId, now, marketAuthorizationScope(env)),
    env.DB.prepare(
      "UPDATE quant_market_jobs SET status='running',phase='checking_plan',lease_token=?,lease_until=?,deadline=strftime('%Y-%m-%dT%H:%M:%fZ',?,'+7200 seconds'),updated_at=? WHERE id=(SELECT job_id FROM quant_market_claims WHERE request_id=?) AND status='queued' AND changes()=1",
    ).bind(token, until, now, now, v.requestId),
    env.DB.prepare(
      "SELECT j.* FROM quant_market_claims c JOIN quant_market_jobs j ON j.id=c.job_id WHERE c.request_id=?",
    ).bind(v.requestId),
  ]);
  const j = results[2].results[0];
  return {
    claim: {
      requestId: v.requestId,
      status: j?.status ?? "empty",
      jobId: j?.id ?? null,
    },
    job:
      !j || terminal.has(j.status)
        ? null
        : {
            id: j.id,
            kind: "market_acquire",
            planId: j.plan_id,
            planRoot: j.plan_root,
            leaseToken: j.lease_token,
            leaseUntil: j.lease_until,
            deadline: j.deadline,
            inputUrl: `/quant/api/runner/market-acquire/jobs/${j.id}/input`,
          },
  };
}
export async function heartbeat(env, v) {
  object(
    v,
    ["capability", "engineVersion", "state", "jobId", "leaseToken", "phase"],
    ["capability", "engineVersion", "state"],
  );
  if (
    v.capability !== MARKET_CAPABILITY ||
    typeof v.engineVersion !== "string" ||
    v.engineVersion.length > 80 ||
    !["idle", "busy"].includes(v.state)
  )
    fail("MARKET_CAPABILITY", "市场准备协议无效");
  const now = NOW();
  await env.DB.prepare(
    "INSERT INTO meta(key,value,updated_at) VALUES('market_acquisition_runner',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
  )
    .bind(
      JSON.stringify({
        capability: MARKET_CAPABILITY,
        engineVersion: v.engineVersion,
        state: v.state,
      }),
      now,
    )
    .run();
  if (!v.jobId)
    return {
      ok: true,
      canClaim:
        (await mayClaim(env)) &&
        !!(await env.DB.prepare(
          "SELECT 1 FROM quant_market_jobs WHERE status='queued' AND authorization_scope=? LIMIT 1",
        )
          .bind(marketAuthorizationScope(env))
          .first()),
    };
  const j = await leased(env, v.jobId, v.leaseToken);
  if (
    ![
      "checking_plan",
      "fetching_sources",
      "normalizing",
      "writing_evidence",
    ].includes(v.phase)
  )
    fail("MARKET_PHASE", "未知准备阶段");
  const until = new Date(
    Math.min(Date.now() + 120000, Date.parse(j.deadline)),
  ).toISOString();
  const updated = await env.DB.prepare(
    "UPDATE quant_market_jobs SET lease_until=?,phase=?,updated_at=? WHERE id=? AND lease_token=? AND status IN('running','cancel_requested') AND lease_until>=? AND deadline>=? RETURNING status",
  )
    .bind(until, v.phase, now, j.id, j.lease_token, now, now)
    .first();
  return {
    ok: true,
    leaseValid: !!updated,
    cancelRequested: updated?.status === "cancel_requested",
    leaseUntil: until,
  };
}
export async function failJob(env, jobId, v) {
  object(v, ["leaseToken", "error"]);
  object(v.error, ["code", "message"]);
  if (
    typeof v.error.code !== "string" ||
    !/^[A-Z][A-Z0-9_]{1,79}$/.test(v.error.code) ||
    typeof v.error.message !== "string" ||
    v.error.message.length > 700
  )
    fail("MARKET_ERROR", "错误回执无效");
  const j = await leased(env, jobId, v.leaseToken, true);
  if (terminal.has(j.status)) return { job: jobView(j) };
  const now = NOW();
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE quant_market_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'failed' END,error=?,updated_at=? WHERE id=? AND lease_token=? AND status IN('running','cancel_requested')",
    ).bind(JSON.stringify(v.error), now, j.id, j.lease_token),
    env.DB.prepare(
      "UPDATE quant_market_requests SET state='outcome_unknown',updated_at=? WHERE job_id=? AND state='intent'",
    ).bind(now, j.id),
  ]);
  return { job: jobView(await ownedJob(env, j.owner, j.id)) };
}
export async function cancel(env, owner, jobId) {
  await ownedJob(env, owner, jobId);
  await env.DB.prepare(
    "UPDATE quant_market_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated_at=? WHERE id=? AND owner=? AND status IN('queued','running')",
  )
    .bind(NOW(), jobId, owner)
    .run();
  return { job: jobView(await ownedJob(env, owner, jobId)) };
}
