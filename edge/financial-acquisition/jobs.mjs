import { claimEligibility } from "./admission.mjs";
/** Durable acquisition claims are isolated from both research and preparation. */
import { string, random } from "../financial/common.mjs";
import {
  CAPABILITY,
  acquisitionEnabled,
  authorizationScope,
  requireAcquisition,
  object,
  id,
  fail,
  parse,
  NOW,
  expireAcquisitions,
  leasedJob,
  ownedJob,
  jobDTO,
} from "./common.mjs";
const phases = [
  "checking_plan",
  "fetching_sources",
  "normalizing",
  "writing_evidence",
];
export async function heartbeat(env, value) {
  object(
    value,
    ["capability", "engineVersion", "state", "jobId", "leaseToken", "phase"],
    ["capability", "engineVersion", "state"],
  );
  if (value.capability !== CAPABILITY)
    fail("ACQUISITION_CAPABILITY", "获取服务协议不匹配", 409);
  string(value.engineVersion, 80);
  string(value.state, 40);
  const now = NOW(),
    eligible = claimEligibility(env, authorizationScope(env));
  await env.DB.prepare(
    "INSERT INTO meta(key,value,updated_at) VALUES('financial_acquisition_runner',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
  )
    .bind(
      JSON.stringify({
        capability: CAPABILITY,
        engineVersion: value.engineVersion,
        state: value.state,
      }),
      now,
    )
    .run();
  if (!value.jobId)
    return {
      ok: true,
      canClaim:
        acquisitionEnabled(env) &&
        !!(await env.DB.prepare(
          `SELECT 1 FROM financial_acquisition_jobs WHERE ${eligible.sql} LIMIT 1`,
        )
          .bind(...eligible.bindings)
          .first()),
    };
  const row = await leasedJob(env, value.jobId, value.leaseToken);
  if (!phases.includes(value.phase)) fail("INVALID_INPUT", "获取阶段无效");
  const until = new Date(
    Math.min(Date.now() + 120000, Date.parse(row.deadline)),
  ).toISOString();
  const updated = await env.DB.prepare(
    "UPDATE financial_acquisition_jobs SET lease_until=?,phase=?,updated_at=? WHERE id=? AND lease_token=? AND status IN ('running','cancel_requested') AND lease_until>=? AND deadline>=? RETURNING status",
  )
    .bind(until, value.phase, now, row.id, row.lease_token, now, now)
    .first();
  return {
    ok: true,
    leaseValid: !!updated,
    cancelRequested: updated?.status === "cancel_requested",
    leaseUntil: until,
  };
}
export async function claim(env, value) {
  object(value, ["requestId", "capability", "engineVersion"]);
  id(value.requestId);
  string(value.engineVersion, 80);
  if (value.capability !== CAPABILITY)
    fail("ACQUISITION_CAPABILITY", "获取服务协议不匹配", 409);
  const old = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_claims WHERE request_id=?",
  )
    .bind(value.requestId)
    .first();
  if (!old) requireAcquisition(env);
  await expireAcquisitions(env);
  const now = NOW(),
    eligible = claimEligibility(env, authorizationScope(env)),
    lease = random(),
    until = new Date(Date.now() + 120000).toISOString();
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO financial_acquisition_claims(request_id,job_id,created_at) VALUES(?,(SELECT id FROM financial_acquisition_jobs WHERE ${eligible.sql} ORDER BY created_at,id LIMIT 1),?)`,
    ).bind(value.requestId, ...eligible.bindings, now),
    env.DB.prepare(
      "UPDATE financial_acquisition_jobs SET status='running',phase='checking_plan',lease_token=?,lease_until=?,deadline=strftime('%Y-%m-%dT%H:%M:%fZ',?,'+600 seconds'),updated_at=? WHERE id=(SELECT job_id FROM financial_acquisition_claims WHERE request_id=?) AND status='queued' AND changes()=1",
    ).bind(lease, until, now, now, value.requestId),
    env.DB.prepare(
      "SELECT j.* FROM financial_acquisition_claims c LEFT JOIN financial_acquisition_jobs j ON j.id=c.job_id WHERE c.request_id=?",
    ).bind(value.requestId),
  ]);
  const row = results[2].results[0],
    receipt = {
      requestId: value.requestId,
      status: row?.status || "empty",
      jobId: row?.id || null,
    };
  return {
    claim: receipt,
    job:
      !row?.id || ["failed", "completed", "cancelled"].includes(row.status)
        ? null
        : {
            id: row.id,
            kind: "financial_acquire",
            planId: row.plan_id,
            leaseToken: row.lease_token,
            leaseUntil: row.lease_until,
            deadline: row.deadline,
            inputUrl: `/quant/api/runner/financial-acquire/jobs/${row.id}/input`,
          },
  };
}
export async function cancelJob(env, owner, jobId) {
  await ownedJob(env, owner, jobId);
  await env.DB.prepare(
    "UPDATE financial_acquisition_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated_at=? WHERE id=? AND owner=? AND status IN ('queued','running')",
  )
    .bind(NOW(), jobId, owner)
    .run();
  return { job: jobDTO(await ownedJob(env, owner, jobId)) };
}
export async function failJob(env, jobId, value) {
  object(value, ["leaseToken", "error"]);
  object(value.error, ["code", "message"]);
  string(value.error.code, 80);
  string(value.error.message, 700);
  const job = await leasedJob(env, jobId, value.leaseToken, { terminal: true });
  if (["failed", "cancelled"].includes(job.status))
    return { job: jobDTO(job), idempotent: true };
  if (job.status === "completed")
    fail("COMPLETION_CONFLICT", "获取任务已完成", 409);
  const now = NOW(),
    status = job.status === "cancel_requested" ? "cancelled" : "failed";
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE financial_acquisition_jobs SET status=?,error=?,updated_at=? WHERE id=? AND lease_token=? AND status IN ('running','cancel_requested') AND lease_until>=? AND deadline>=?",
    ).bind(
      status,
      JSON.stringify(value.error),
      now,
      jobId,
      job.lease_token,
      now,
      now,
    ),
    env.DB.prepare(
      "UPDATE financial_acquisition_requests SET state='outcome_unknown',error=?,updated_at=? WHERE job_id=? AND state='intent' AND EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND status IN ('failed','cancelled'))",
    ).bind(JSON.stringify(value.error), now, jobId, jobId),
  ]);
  return { job: jobDTO(await ownedJob(env, job.owner, jobId)) };
}
