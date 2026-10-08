import { LEGACY_DATASET, assertPlanContext, assertContext } from './context.mjs';
import {
  CAPABILITY,
  LIMITS,
  enabled,
  requireEnabled,
  object,
  id,
  string,
  fail,
  NOW,
  random,
  parse,
  owned,
  jobDTO
} from './common.mjs';
const eligible = (context) => {
  assertContext(context);
  return `status='queued' AND NOT EXISTS(SELECT 1 FROM meta WHERE key IN ('dataset_maintenance','${context.maintenanceKey}') AND value='paused') AND EXISTS(SELECT 1 FROM quant_dataset_plans p WHERE p.id=quant_dataset_jobs.plan_id AND p.owner=quant_dataset_jobs.owner AND json_extract(p.spec,'$.profile')='${context.profile}' AND json_extract(p.spec,'$.request.profile')='${context.profile}')`;
};
async function assertJobContext(env, row, context) {
  const plan = await owned(env, 'quant_dataset_plans', row.owner, row.plan_id);
  assertPlanContext(parse(plan.spec), context);
  return row;
}
export async function expire(env) {
  const now = NOW();
  await env.DB.prepare(
    "UPDATE quant_dataset_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'failed' END,error=?,updated_at=? WHERE status IN ('running','cancel_requested') AND (lease_until<? OR deadline<?)"
  )
    .bind(
      JSON.stringify({
        code: 'DATASET_LEASE_EXPIRED',
        message: '组成任务租约或固定期限已到；未重新计算'
      }),
      now,
      now,
      now
    )
    .run();
}
export async function leased(
  env,
  jobId,
  token,
  { terminal = false, cancel = false, context = LEGACY_DATASET } = {}
) {
  id(jobId);
  id(token);
  const row = await env.DB.prepare('SELECT * FROM quant_dataset_jobs WHERE id=? AND lease_token=?')
    .bind(jobId, token)
    .first();
  if (!row) fail('STALE_LEASE', '组成任务租约不匹配', 409);
  await assertJobContext(env, row, context);
  if (terminal && ['completed', 'failed', 'cancelled'].includes(row.status)) return row;
  if (
    !['running', ...(cancel ? ['cancel_requested'] : [])].includes(row.status) ||
    row.lease_until < NOW() ||
    row.deadline < NOW()
  )
    fail('STALE_LEASE', '组成任务已停止或租约过期', 409);
  return row;
}
export async function claim(env, v, context = LEGACY_DATASET) {
  object(v, ['requestId', 'capability', 'engineVersion']);
  id(v.requestId);
  string(v.engineVersion, 80);
  if (v.capability !== context.capability)
    fail('DATASET_CAPABILITY_UNAVAILABLE', '服务未声明数据集协议', 409);
  await expire(env);
  const old = await env.DB.prepare('SELECT 1 FROM quant_dataset_claims WHERE request_id=?')
    .bind(v.requestId)
    .first();
  if (!old) requireEnabled(env, context);
  const now = NOW(),
    lease = random(),
    until = new Date(Date.now() + LIMITS.leaseSeconds * 1000).toISOString(),
    deadline = new Date(Date.now() + LIMITS.deadlineSeconds * 1000).toISOString();
  const rows = await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_dataset_claims(request_id,job_id,created_at) VALUES(?,(SELECT id FROM quant_dataset_jobs WHERE ${eligible(context)} ORDER BY created_at,id LIMIT 1),?)`
    ).bind(v.requestId, now),
    env.DB.prepare(
      "UPDATE quant_dataset_jobs SET status='running',phase='checking_sources',lease_token=?,lease_until=?,deadline=?,updated_at=? WHERE id=(SELECT job_id FROM quant_dataset_claims WHERE request_id=?) AND status='queued' AND changes()=1"
    ).bind(lease, until, deadline, now, v.requestId),
    env.DB.prepare(
      'SELECT j.* FROM quant_dataset_claims c LEFT JOIN quant_dataset_jobs j ON j.id=c.job_id WHERE c.request_id=?'
    ).bind(v.requestId)
  ]);
  const r = rows[2].results[0],
    receipt = {
      requestId: v.requestId,
      jobId: r?.id || null,
      status: r?.status || 'empty'
    };
  if (r?.id) await assertJobContext(env, r, context);
  return {
    claim: receipt,
    job:
      r?.id && r.status === 'running'
        ? {
            id: r.id,
            kind: context.jobKind,
            planId: r.plan_id,
            leaseToken: r.lease_token,
            leaseUntil: r.lease_until,
            deadline: r.deadline,
            inputUrl: `/quant/api${context.runnerBase}/jobs/${r.id}/input`
          }
        : null
  };
}
export async function heartbeat(env, v, context = LEGACY_DATASET) {
  object(
    v,
    ['capability', 'engineVersion', 'state', 'jobId', 'leaseToken', 'phase'],
    ['capability', 'engineVersion', 'state']
  );
  if (v.capability !== context.capability)
    fail('DATASET_CAPABILITY_UNAVAILABLE', '不支持的数据集协议', 409);
  string(v.engineVersion, 80);
  string(v.state, 40);
  const now = NOW();
  await env.DB.prepare(
    'INSERT INTO meta(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at'
  )
    .bind(
      context.metaKey,
      JSON.stringify({
        capability: context.capability,
        engineVersion: v.engineVersion,
        state: v.state
      }),
      now
    )
    .run();
  if (v.jobId === undefined)
    return {
      ok: true,
      canClaim:
        enabled(env, context) &&
        !!(await env.DB.prepare(
          `SELECT 1 FROM quant_dataset_jobs WHERE ${eligible(context)} LIMIT 1`
        ).first())
    };
  const row = await leased(env, v.jobId, v.leaseToken, {
    cancel: true,
    context
  });
  if (
    !['checking_sources', 'deriving_scope', 'composing_states', 'writing_evidence'].includes(
      v.phase
    )
  )
    fail('INVALID_INPUT', '组成阶段无效');
  const until = new Date(
      Math.min(Date.now() + LIMITS.leaseSeconds * 1000, Date.parse(row.deadline))
    ).toISOString(),
    changed = await env.DB.prepare(
      "UPDATE quant_dataset_jobs SET lease_until=?,phase=?,updated_at=? WHERE id=? AND lease_token=? AND status IN ('running','cancel_requested') AND lease_until>=? AND deadline>=? RETURNING status"
    )
      .bind(until, v.phase, now, row.id, row.lease_token, now, now)
      .first();
  return {
    ok: true,
    leaseValid: !!changed,
    cancelRequested: changed?.status === 'cancel_requested',
    leaseUntil: until
  };
}
export async function failJob(env, row, error) {
  object(error, ['code', 'message']);
  string(error.code, 100);
  string(error.message, 1200);
  if (['completed', 'failed', 'cancelled'].includes(row.status)) {
    if (row.status === 'completed' || row.error !== JSON.stringify(error))
      fail('COMPLETION_CONFLICT', '任务已有其他终态', 409);
    return { ok: true, status: row.status, idempotent: true };
  }
  const now = NOW(),
    r = await env.DB.prepare(
      "UPDATE quant_dataset_jobs SET status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'failed' END,error=?,updated_at=? WHERE id=? AND lease_token=? AND status IN ('running','cancel_requested') AND lease_until>=? AND deadline>=? RETURNING status"
    )
      .bind(JSON.stringify(error), now, row.id, row.lease_token, now, now)
      .first();
  if (!r) fail('STALE_LEASE', '任务已停止', 409);
  return { ok: true, status: r.status };
}
export async function cancelJob(env, owner, jobId, context = LEGACY_DATASET) {
  const row = await owned(env, 'quant_dataset_jobs', owner, jobId);
  await assertJobContext(env, row, context);
  await env.DB.prepare(
    "UPDATE quant_dataset_jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated_at=? WHERE id=? AND owner=? AND status IN ('queued','running')"
  )
    .bind(NOW(), row.id, owner)
    .run();
  return {
    preparation: jobDTO(await owned(env, 'quant_dataset_jobs', owner, jobId))
  };
}
