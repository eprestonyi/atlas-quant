import { object, id, fail, parse, NOW } from "../financial/common.mjs";
import {
  CAPABILITY,
  acquisitionEnabled,
  authorizationScope,
  requireAcquisition,
} from "./planner.mjs";
export {
  CAPABILITY,
  acquisitionEnabled,
  authorizationScope,
  requireAcquisition,
};
export { object, id, fail, parse, NOW };

export async function ownedPlan(env, owner, planId) {
  const row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_plans WHERE id=? AND owner=?",
  )
    .bind(id(planId), owner)
    .first();
  if (!row) fail("NOT_FOUND", "获取计划不存在", 404);
  return row;
}
export async function ownedJob(env, owner, jobId) {
  const row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_jobs WHERE id=? AND owner=?",
  )
    .bind(id(jobId), owner)
    .first();
  if (!row) fail("NOT_FOUND", "获取任务不存在", 404);
  return row;
}
export async function runnerInfo(env) {
  const row = await env.DB.prepare(
    "SELECT value,updated_at FROM meta WHERE key='financial_acquisition_runner'",
  ).first();
  const value = parse(row?.value, {});
  return {
    online:
      !!row &&
      value.capability === CAPABILITY &&
      Date.now() - Date.parse(row.updated_at) < 120000,
    capability: value.capability || null,
    lastSeen: row?.updated_at || null,
  };
}
export const planDTO = (row) => ({
  id: row.id,
  createdAt: row.created_at,
  ownerScope: true,
  ...parse(row.spec),
});
export const jobDTO = (row) => ({
  id: row.id,
  planId: row.plan_id,
  status: row.status,
  phase: row.phase,
  createdAt: row.created_at,
  updatedAt: row.updated_at,
  error: parse(row.error),
  result: parse(row.result),
  researchBinding: false,
});

export async function cacheState(env, request) {
  const row = await env.DB.prepare(
    `SELECT r.state,r.error,c.id,c.sha256,c.byte_length,c.retrieved_at,c.http_status,c.source_kind
    FROM financial_acquisition_requests r LEFT JOIN financial_acquisition_cache c ON c.id=r.receipt_id
    WHERE r.request_key=? AND r.authorization_scope=?`,
  )
    .bind(request.requestKey, request.authorizationScope)
    .first();
  if (!row) return { status: "missing" };
  if (
    row.state === "received" &&
    row.id &&
    row.source_kind !== "provider" &&
    env.ALLOW_ACQUISITION_FIXTURES !== "true"
  )
    return { status: "blocked", reasonCode: "SOURCE_KIND" };
  if (row.state === "received" && row.id && row.http_status !== 200)
    return { status: "failed", reasonCode: "PROVIDER_RESPONSE_FAILED" };
  if (row.state === "received" && row.id)
    return {
      status: "frozen",
      receiptId: row.id,
      sha256: row.sha256,
      byteLength: row.byte_length,
      retrievedAt: row.retrieved_at,
      httpStatus: row.http_status,
      sourceKind: row.source_kind,
    };
  return {
    status: row.state === "intent" ? "in_progress" : row.state,
    reasonCode:
      row.state === "intent"
        ? "REQUEST_IN_PROGRESS"
        : "REQUEST_REQUIRES_REVIEW",
  };
}

export async function expireAcquisitions(env) {
  const now = NOW();
  // A sent intent without a receipt is never reset to missing. This is the
  // boundary preventing another plan/workspace from repeating an unknown read.
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE financial_acquisition_requests SET state='outcome_unknown',updated_at=?
      WHERE state='intent' AND job_id IN (SELECT id FROM financial_acquisition_jobs
      WHERE status IN ('running','cancel_requested') AND (lease_until<? OR deadline<?))`,
    ).bind(now, now, now),
    env.DB.prepare(
      `UPDATE financial_acquisition_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'failed' END,
      error=?,updated_at=? WHERE status IN ('running','cancel_requested') AND (lease_until<? OR deadline<?)`,
    ).bind(
      JSON.stringify({
        code: "ACQUISITION_LEASE_EXPIRED",
        message: "获取任务租约到期；已发出但无回执的请求须人工核对",
      }),
      now,
      now,
      now,
    ),
  ]);
}
export async function leasedJob(env, jobId, token, { terminal = false } = {}) {
  const row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_jobs WHERE id=? AND lease_token=?",
  )
    .bind(id(jobId), id(token))
    .first();
  if (!row) fail("STALE_LEASE", "获取任务租约无效", 409);
  if (terminal && ["completed", "failed", "cancelled"].includes(row.status))
    return row;
  if (
    !["running", "cancel_requested"].includes(row.status) ||
    row.lease_until < NOW() ||
    row.deadline < NOW()
  ) {
    await expireAcquisitions(env);
    fail("STALE_LEASE", "获取任务租约已停止", 409);
  }
  return row;
}

/** Re-check a live plan's entitlement before reading cache or exposing new inputs.
 * Receipt retention and terminal ACK/status recovery deliberately do not use it. */
export function requireJobAuthorization(env, job) {
  requireAcquisition(env, job.owner);
  if (
    parse(job.spec).requests.some(
      (request) => request.authorizationScope !== authorizationScope(env),
    )
  )
    fail(
      "ACQUISITION_SCOPE_CHANGED",
      "来源授权范围已经改变，当前任务不能继续读取或发布",
      409,
    );
}
