import { ApiError } from '../errors.mjs';
import { NOW, body, json, sha } from '../runtime.mjs';
import {
  isHash,
  MAX_ARTIFACT_BYTES,
  ownedForecast,
  quantRun,
  readPrivateObject
} from './persistence.mjs';

async function leasedJob(env, input) {
  const row = await env.DB.prepare(
    "SELECT * FROM jobs WHERE id=? AND lease_token=? AND status IN ('running','completed','failed','cancelled')"
  )
    .bind(String(input.id ?? ''), String(input.leaseToken ?? ''))
    .first();
  if (!row) throw new ApiError('STALE_LEASE', '任务租约无效', 409);
  return row;
}

/** Caller has already authenticated the operator bearer; no browser access. */
export async function statisticalRunnerApi(req, env, path) {
  if (!['/runner/snapshot', '/runner/replay'].includes(path)) return null;
  const input = await body(req, path === '/runner/snapshot' ? 26 * 1024 * 1024 : 12000);
  const job = await leasedJob(env, input),
    link = await quantRun(env, job.id);
  if (job.data_source === 'ready_market') throw new ApiError('MARKET_TRANSPORT_REQUIRED', '完整市场来源仅允许分片预测，不支持旧快照与执行入口', 409);
  if (job.data_source === 'ready_dataset') throw new ApiError('FINANCIAL_TRANSPORT_REQUIRED', '财务数据集需要独立金融结果协议，不支持旧快照或执行入口', 409);
  if (!link) throw new ApiError('INVALID_RESEARCH_JOB', '此任务不属于预测研究');
  if (path === '/runner/replay') {
    if (job.status !== 'running' || job.lease_until < NOW() || link.kind !== 'execution')
      throw new ApiError('STALE_LEASE', '仅有效执行租约可以读取来源产物', 409);
    const artifact = await ownedForecast(env, job.owner, link.source_forecast_id);
    if (input.kind === 'dataset')
      return json({
        snapshot: await readPrivateObject(env, artifact.dataset_key, artifact.dataset_hash)
      });
    if (input.kind === 'forecast')
      return json({
        artifact: await readPrivateObject(env, artifact.artifact_key, artifact.artifact_hash)
      });
    throw new ApiError('INVALID_REPLAY_KIND', '请选择dataset或forecast');
  }
  if (link.kind !== 'forecast')
    throw new ApiError('INVALID_RESEARCH_JOB', '执行复用不能替换冻结行情');
  if (job.status === 'cancelled' || job.status === 'failed')
    return json({ ok: true, ignored: true, terminalDiscard: true, status: job.status });
  const snapshot = input.snapshot;
  if (
    !snapshot ||
    snapshot.schemaVersion !== 1 ||
    !Array.isArray(snapshot.rows) ||
    snapshot.rows.length < 1 ||
    snapshot.rows.length > 110000 ||
    !snapshot.provenance ||
    typeof snapshot.provenance !== 'object' ||
    !isHash(snapshot.dataFingerprint)
  )
    throw new ApiError('INVALID_SNAPSHOT', '冻结行情需要完整行、来源和数据指纹');
  const text = JSON.stringify(snapshot);
  if (new TextEncoder().encode(text).byteLength > MAX_ARTIFACT_BYTES)
    throw new ApiError('SNAPSHOT_TOO_LARGE', '冻结行情超过24MiB', 413);
  if (/"(?:token|serviceToken|api_key|password|authorization)"\s*:/i.test(text))
    throw new ApiError('SENSITIVE_SNAPSHOT', '冻结数据含不允许的凭据字段');
  const hash = await sha(text);
  if (link.snapshot_hash) {
    if (link.snapshot_hash !== hash || link.snapshot_fingerprint !== snapshot.dataFingerprint)
      throw new ApiError('SNAPSHOT_CONFLICT', '冻结行情不可改写', 409);
    return json({ ok: true, idempotent: true, dataFingerprint: snapshot.dataFingerprint });
  }
  if (job.status === 'running' && job.lease_until < NOW()) {
    await env.DB.prepare(
      "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND lease_token=? AND status='running'"
    )
      .bind(
        JSON.stringify({ code: 'RUNNER_INTERRUPTED', message: '运行租约已过期，本次实验未完成。' }),
        NOW(),
        job.id,
        job.lease_token
      )
      .run();
    return json({ ok: true, ignored: true, terminalDiscard: true, status: 'failed' });
  }
  if (job.status !== 'running')
    throw new ApiError('STALE_LEASE', '已终止的任务不能新增行情快照', 409);
  const key = `forecast-data/${job.owner}/${job.id}/${hash}.json`;
  await env.ARTIFACTS.put(key, text, { httpMetadata: { contentType: 'application/json' } });
  const updated = await env.DB.prepare(
    "UPDATE quant_runs SET snapshot_key=?,snapshot_hash=?,snapshot_fingerprint=? WHERE job_id=? AND owner=? AND snapshot_key IS NULL AND EXISTS(SELECT 1 FROM jobs WHERE id=? AND lease_token=? AND status='running')"
  )
    .bind(key, hash, snapshot.dataFingerprint, job.id, job.owner, job.id, job.lease_token)
    .run();
  if (!updated.meta.changes) {
    const current = await quantRun(env, job.id);
    if (current?.snapshot_hash === hash)
      return json({ ok: true, idempotent: true, dataFingerprint: snapshot.dataFingerprint });
    if (current?.snapshot_key !== key) await env.ARTIFACTS.delete(key);
    const latest = await env.DB.prepare('SELECT status FROM jobs WHERE id=? AND lease_token=?')
      .bind(job.id, job.lease_token)
      .first();
    if (latest && ['cancelled', 'failed'].includes(latest.status))
      return json({ ok: true, ignored: true, terminalDiscard: true, status: latest.status });
    throw new ApiError('SNAPSHOT_CONFLICT', '快照租约或内容已变化', 409);
  }
  return json({ ok: true, dataFingerprint: snapshot.dataFingerprint });
}
