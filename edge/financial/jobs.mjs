import {
  CAPABILITY,
  NOW,
  fail,
  object,
  string,
  id,
  parse,
  random,
  enabled,
  jobDTO,
} from './common.mjs';

// Shared with the atomic claim: the heartbeat is only a read-only advisory and
// never reserves a job, expires a lease or creates a durable claim receipt.
const CLAIMABLE_JOB =
  "status='queued' AND NOT EXISTS(SELECT 1 FROM meta WHERE key='financial_maintenance' AND value='paused')";

export async function expireJobs(env) {
  const rows = await env.DB.prepare(
    "SELECT * FROM financial_jobs WHERE status IN ('running','cancel_requested') AND (lease_until<? OR deadline<?) LIMIT 100"
  )
    .bind(NOW(), NOW())
    .all();
  for (const row of rows.results)
    await finishFailure(
      env,
      row,
      {
        code: row.status === 'cancel_requested' ? 'CANCELLED' : 'LEASE_EXPIRED',
        message: row.status === 'cancel_requested' ? '任务已取消' : '财务计算租约已过期',
      },
      true
    );
}
export async function leasedJob(env, jobId, token, { terminal = false, cancel = false } = {}) {
  id(jobId);
  id(token);
  const row = await env.DB.prepare('SELECT * FROM financial_jobs WHERE id=? AND lease_token=?')
    .bind(jobId, token)
    .first();
  if (!row) fail('STALE_LEASE', '任务租约无效', 409);
  if (terminal && ['completed', 'failed', 'cancelled'].includes(row.status)) return row;
  if (!['running', ...(cancel ? ['cancel_requested'] : [])].includes(row.status))
    fail('STALE_LEASE', '任务已停止或取消', 409);
  if (row.lease_until < NOW() || row.deadline < NOW()) {
    await expireJobs(env);
    fail('STALE_LEASE', '任务租约已过期', 409);
  }
  return row;
}
export async function claim(env, value) {
  object(value, ['requestId', 'capability', 'engineVersion']);
  id(value.requestId);
  string(value.engineVersion, 80);
  if (value.capability !== CAPABILITY)
    fail('FINANCIAL_CAPABILITY_UNAVAILABLE', '计算服务未声明财务协议能力', 409);
  await expireJobs(env);
  const old = await env.DB.prepare('SELECT * FROM financial_claims WHERE request_id=?')
    .bind(value.requestId)
    .first();
  if (!old && !enabled(env)) fail('FINANCIAL_CAPABILITY_UNAVAILABLE', '财务队列未启用', 503);
  const now = NOW(),
    lease = random(),
    leaseUntil = new Date(Date.now() + 120000).toISOString();
  const result = await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO financial_claims(request_id,job_id,created_at) VALUES(?,(SELECT id FROM financial_jobs WHERE ${CLAIMABLE_JOB} ORDER BY created_at,id LIMIT 1),?)`
    ).bind(value.requestId, now),
    env.DB.prepare(
      "UPDATE financial_jobs SET status='running',lease_token=?,lease_until=?,deadline=strftime('%Y-%m-%dT%H:%M:%fZ',?,CASE WHEN kind='financial_prepare' THEN '+600 seconds' ELSE '+180 seconds' END),phase='checking_inputs',updated_at=? WHERE id=(SELECT job_id FROM financial_claims WHERE request_id=?) AND status='queued' AND changes()=1"
    ).bind(lease, leaseUntil, now, now, value.requestId),
    env.DB.prepare(
      'SELECT j.* FROM financial_claims c LEFT JOIN financial_jobs j ON j.id=c.job_id WHERE c.request_id=?'
    ).bind(value.requestId),
  ]);
  const row = result[2].results[0];
  if (!row?.id)
    return {
      claim: { requestId: value.requestId, status: 'empty', jobId: null },
      job: null,
    };
  const receipt = {
    requestId: value.requestId,
    status: row.status,
    jobId: row.id,
  };
  if (['completed', 'failed', 'cancelled'].includes(row.status))
    return { claim: receipt, job: null };
  return {
    claim: receipt,
    job: {
      id: row.id,
      kind: row.kind,
      inputId: row.input_id,
      leaseToken: row.lease_token,
      leaseUntil: row.lease_until,
      deadline: row.deadline,
      inputUrl: `/quant/api/runner/financial/jobs/${row.id}/input`,
    },
  };
}
export async function heartbeat(env, value) {
  object(
    value,
    ['capability', 'engineVersion', 'state', 'jobId', 'leaseToken', 'phase'],
    ['capability', 'engineVersion', 'state']
  );
  if (value.capability !== CAPABILITY)
    fail('FINANCIAL_CAPABILITY_UNAVAILABLE', '不支持的财务协议', 409);
  string(value.engineVersion, 80);
  string(value.state, 40);
  const now = NOW();
  await env.DB.prepare(
    "INSERT INTO meta(key,value,updated_at) VALUES('financial_runner',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
  )
    .bind(
      JSON.stringify({
        capability: CAPABILITY,
        engineVersion: value.engineVersion,
        state: value.state,
      }),
      now
    )
    .run();
  if (value.jobId === undefined) {
    const canClaim = enabled(env)
      ? !!(await env.DB.prepare(
          `SELECT 1 FROM financial_jobs WHERE ${CLAIMABLE_JOB} LIMIT 1`
        ).first())
      : false;
    return { ok: true, canClaim };
  }
  const row = await leasedJob(env, value.jobId, value.leaseToken, {
    cancel: true,
  });
  if (!['checking_inputs', 'preparing_states', 'writing_evidence'].includes(value.phase))
    fail('INVALID_INPUT', '任务阶段无效');
  const until = new Date(Math.min(Date.now() + 120000, Date.parse(row.deadline))).toISOString();
  const changed = await env.DB.prepare(
    "UPDATE financial_jobs SET lease_until=?,phase=?,updated_at=? WHERE id=? AND lease_token=? AND status IN ('running','cancel_requested') AND lease_until>=? AND deadline>=? RETURNING status"
  )
    .bind(until, value.phase, NOW(), row.id, row.lease_token, NOW(), NOW())
    .first();
  return {
    ok: true,
    leaseValid: !!changed,
    cancelRequested: changed?.status === 'cancel_requested',
    leaseUntil: until,
  };
}
export async function finishFailure(env, row, error, expiration = false) {
  const terminal = error.code === 'CANCELLED' ? 'cancelled' : 'failed',
    errorText = JSON.stringify(error),
    now = NOW();
  if (['failed', 'cancelled'].includes(row.status)) {
    if (row.error !== errorText) fail('COMPLETION_CONFLICT', '任务已存在其他终态', 409);
    return { ok: true, status: row.status, idempotent: true };
  }
  if (row.status === 'completed') fail('COMPLETION_CONFLICT', '任务已完成', 409);
  if (terminal === 'cancelled' && row.status !== 'cancel_requested' && !expiration)
    fail('INVALID_CANCELLATION', '尚未请求取消', 409);
  const structural = !['CANCELLED', 'LEASE_EXPIRED', 'TRANSPORT_ERROR', 'RUNNER_ERROR'].includes(
    error.code
  );
  const expiryGuard = expiration ? " AND (status='queued' OR lease_until<? OR deadline<?)" : '';
  const params = [
    terminal,
    errorText,
    now,
    row.id,
    row.lease_token || null,
    row.lease_token || null,
  ];
  if (expiration) params.push(now, now);
  const statements = [
    env.DB.prepare(
      "UPDATE financial_jobs SET status=?,error=?,updated_at=? WHERE id=? AND status IN ('queued','running','cancel_requested') AND (lease_token=? OR (lease_token IS NULL AND ? IS NULL))" +
        expiryGuard
    ).bind(...params),
    env.DB.prepare(
      `UPDATE financial_inputs SET status=CASE WHEN ?='financial_revise' THEN 'blocked' WHEN EXISTS(SELECT 1 FROM financial_preparations WHERE input_id=financial_inputs.id) THEN 'prepared' WHEN canonical_publication_id IS NOT NULL THEN 'ready_to_prepare' WHEN ? THEN 'blocked' ELSE 'uploaded' END,error=?,updated_at=? WHERE id=? AND changes()=1`
    ).bind(row.kind, Number(structural), errorText, now, row.input_id),
  ];
  await env.DB.batch(statements);
  const actual = await env.DB.prepare('SELECT status,error FROM financial_jobs WHERE id=?')
    .bind(row.id)
    .first();
  return {
    ok: ['failed', 'cancelled'].includes(actual.status),
    status: actual.status,
  };
}
export async function cancelJob(env, owner, jobId) {
  const row = await env.DB.prepare('SELECT * FROM financial_jobs WHERE id=? AND owner=?')
    .bind(id(jobId), owner)
    .first();
  if (!row) fail('NOT_FOUND', '任务不存在', 404);
  if (row.status === 'queued') {
    const result = await finishFailure(
      env,
      row,
      { code: 'CANCELLED', message: '任务已取消' },
      true
    );
    if (result.status === 'cancelled') return result;
  }
  const changed = await env.DB.prepare(
    "UPDATE financial_jobs SET status='cancel_requested',updated_at=? WHERE id=? AND owner=? AND status='running' RETURNING *"
  )
    .bind(NOW(), row.id, owner)
    .first();
  const actual =
    changed ||
    (await env.DB.prepare('SELECT status FROM financial_jobs WHERE id=? AND owner=?')
      .bind(row.id, owner)
      .first());
  return { ok: true, status: actual.status };
}
