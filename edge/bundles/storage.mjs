import { ApiError } from '../errors.mjs';
import { NOW, parse, random, sha } from '../runtime.mjs';
import { validateStoredStatisticalQuant } from '../statistical-quant/validation.mjs';
import { BUNDLE_PROFILE, HASH } from './profile.mjs';
import { byteLength } from './json.mjs';
import { validateManifest, validateChunk } from './manifest.mjs';
import { indexStatements, recordIndex } from './records.mjs';

export const terminalDiscard = (job) => ({
  ok: true,
  terminalDiscard: true,
  ignored: true,
  status: job.status
});
export const notFound = () => new ApiError('NOT_FOUND', '研究分片不存在', 404);
const conflict = (message) => {
  throw new ApiError('BUNDLE_CONFLICT', message, 409);
};
function sorted(value) {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sorted(value[key])])
    );
  return value;
}
const same = (left, right) => JSON.stringify(sorted(left)) === JSON.stringify(sorted(right));
const prediction = (strategy) =>
  Object.fromEntries(
    Object.entries(strategy).filter(
      ([key]) => !['execution', 'portfolio', 'costs', 'name', 'graph'].includes(key)
    )
  );

export async function leasedJob(env, input) {
  let job = await env.DB.prepare('SELECT * FROM jobs WHERE id=? AND lease_token=?')
    .bind(String(input.id ?? ''), String(input.leaseToken ?? ''))
    .first();
  if (!job) throw new ApiError('STALE_LEASE', '研究租约不匹配', 409);
  if (job.status === 'running' && job.lease_until <= NOW()) {
    await env.DB.prepare(
      "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND lease_token=? AND status='running' AND lease_until<=?"
    )
      .bind(
        JSON.stringify({
          code: 'RUNNER_INTERRUPTED',
          message: '运行租约已过期，此次研究未完成。'
        }),
        NOW(),
        job.id,
        job.lease_token,
        NOW()
      )
      .run();
    job = await env.DB.prepare('SELECT * FROM jobs WHERE id=?').bind(job.id).first();
  }
  return job;
}
export async function researchLink(env, job) {
  const link = await env.DB.prepare('SELECT * FROM quant_runs WHERE job_id=? AND owner=?')
    .bind(job.id, job.owner)
    .first();
  if (!link) throw new ApiError('INVALID_RESEARCH_JOB', '分片仅适用于版本化预测研究');
  return link;
}
export async function loadStage(env, stageId, job = null) {
  const stage = await env.DB.prepare('SELECT * FROM quant_bundle_stages WHERE id=?')
    .bind(String(stageId ?? ''))
    .first();
  if (
    !stage ||
    (job &&
      (stage.job_id !== job.id ||
        stage.owner !== job.owner ||
        stage.lease_token !== job.lease_token))
  )
    throw new ApiError('STALE_LEASE', '分片上传身份不匹配', 409);
  return stage;
}
export async function parsedStage(stage) {
  return validateManifest(stage.manifest_text, stage.bundle_id);
}
export async function sourceStage(env, job) {
  const link = await researchLink(env, job);
  if (link.kind !== 'execution')
    throw new ApiError('INVALID_RESEARCH_JOB', '仅执行研究可以读取来源分片');
  const source = await env.DB.prepare(
    `SELECT b.*,s.bundle_id,s.status FROM quant_bundle_forecasts b
    JOIN quant_bundle_stages s ON s.id=b.stage_id WHERE b.owner=? AND b.forecast_id=? AND s.owner=b.owner AND s.status='committed'`
  )
    .bind(job.owner, link.source_forecast_id)
    .first();
  if (!source) throw new ApiError('BUNDLE_SOURCE_UNAVAILABLE', '来源不是已提交的分片产物', 409);
  return loadStage(env, source.stage_id);
}
export async function ownedStageForRun(env, owner, jobId) {
  const stage = await env.DB.prepare(
    `SELECT s.* FROM quant_bundle_runs r JOIN quant_bundle_stages s ON s.id=r.stage_id
    JOIN jobs j ON j.id=r.job_id WHERE r.job_id=? AND r.owner=? AND s.owner=r.owner AND j.owner=r.owner
      AND s.status='committed' AND j.status='completed'`
  )
    .bind(jobId, owner)
    .first();
  return stage;
}

async function assertJobContent(env, job, link, parsed) {
  const { manifest, metadata } = parsed;
  if (manifest.kind !== link.kind) conflict('研究任务与传输种类不一致');
  const strategy = validateStoredStatisticalQuant(parse(job.spec));
  const reported = validateStoredStatisticalQuant(metadata.report.strategy);
  const source = validateStoredStatisticalQuant(metadata.forecast.sourceStrategy);
  if (!same(strategy, reported) || !same(prediction(strategy), prediction(source)))
    conflict('报告配置或预测来源与领取任务不一致');
  if (metadata.report.execution?.forecastArtifactId !== manifest.forecastArtifactId)
    conflict('执行台账未引用同一预测');
  if (link.kind === 'forecast') {
    if (
      metadata.coverage.source !== 'samples_before_model_fitting' ||
      metadata.report.research.executionOnly === true
    )
      conflict('新预测需要拟合前独立覆盖计划');
  } else {
    const original = await env.DB.prepare(
      'SELECT * FROM quant_forecast_artifacts WHERE owner=? AND id=?'
    )
      .bind(job.owner, link.source_forecast_id)
      .first();
    if (
      !original ||
      original.id !== manifest.forecastArtifactId ||
      original.data_fingerprint !== manifest.dataFingerprint ||
      original.prediction_config_hash !== manifest.predictionConfigHash ||
      metadata.report.research.executionOnly !== true ||
      metadata.report.research.predictionRefitPerformed !== false
    )
      conflict('执行必须复用原预测与冻结来源');
    const bundleSource = await env.DB.prepare(
      `SELECT s.manifest_text,s.bundle_id FROM quant_bundle_forecasts b JOIN quant_bundle_stages s ON s.id=b.stage_id WHERE b.owner=? AND b.forecast_id=? AND s.owner=b.owner AND s.status='committed'`
    )
      .bind(job.owner, original.id)
      .first();
    if (bundleSource) {
      const sourceManifest = await validateManifest(
        bundleSource.manifest_text,
        bundleSource.bundle_id
      );
      if (sourceManifest.manifest.documents.coverage.sha256 !== manifest.documents.coverage.sha256)
        conflict('执行必须逐字复用来源覆盖计划');
    }
  }
  if (
    metadata.report.metrics === null &&
    (reported.execution.enabled !== false ||
      parsed.collections.get('equity').rowCount ||
      parsed.collections.get('trades').rowCount)
  )
    conflict('纯预测不能返回执行曲线或成交');
}

export async function beginBundle(env, input) {
  const job = await leasedJob(env, input),
    link = await researchLink(env, job);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  const parsed = await validateManifest(input.manifestText, input.bundleId);
  await assertJobContent(env, job, link, parsed);
  let stage = await env.DB.prepare('SELECT * FROM quant_bundle_stages WHERE job_id=?')
    .bind(job.id)
    .first();
  if (
    stage &&
    (stage.bundle_id !== parsed.bundleId ||
      stage.manifest_text !== parsed.manifestText ||
      stage.lease_token !== job.lease_token ||
      stage.owner !== job.owner)
  )
    conflict('同一任务的分片内容不可更换');
  if (!stage) {
    if (job.status !== 'running') conflict('终态任务不能创建新分片');
    const id = random(),
      now = NOW(),
      key = `bundle/${job.owner}/${id}/manifest.json`;
    const metadata = JSON.stringify(parsed.metadata);
    if (byteLength(metadata) > BUNDLE_PROFILE.manifestBytes)
      throw new ApiError('BUNDLE_BUDGET', '元数据骨架超过预算', 413);
    await env.DB.prepare(
      `INSERT OR IGNORE INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at)
      SELECT ?,?,?,?,?,?,?,?,'staging',?,? WHERE EXISTS(SELECT 1 FROM jobs WHERE id=? AND owner=? AND lease_token=? AND status='running' AND lease_until>?)`
    )
      .bind(
        id,
        job.owner,
        job.id,
        job.lease_token,
        parsed.bundleId,
        parsed.manifestText,
        key,
        metadata,
        now,
        now,
        job.id,
        job.owner,
        job.lease_token,
        now
      )
      .run();
    stage = await env.DB.prepare('SELECT * FROM quant_bundle_stages WHERE job_id=?')
      .bind(job.id)
      .first();
    if (!stage) return terminalDiscard(await leasedJob(env, input));
    if (
      stage.bundle_id !== parsed.bundleId ||
      stage.manifest_text !== parsed.manifestText ||
      stage.lease_token !== job.lease_token
    )
      conflict('并发开始请求内容不一致');
  }
  if (stage.status === 'aborted') return terminalDiscard(await leasedJob(env, input));
  const received = await env.DB.prepare(
    'SELECT collection,ordinal FROM quant_bundle_chunks WHERE stage_id=?'
  )
    .bind(stage.id)
    .all();
  const present = new Set(received.results.map((row) => row.collection + ':' + row.ordinal));
  const missing = [];
  for (const collection of parsed.collections.values())
    for (const descriptor of collection.chunks) {
      if (!present.has(collection.id + ':' + descriptor.ordinal))
        missing.push({
          collection: collection.id,
          ordinal: descriptor.ordinal
        });
    }
  return {
    ok: true,
    stageId: stage.id,
    bundleId: stage.bundle_id,
    status: stage.status,
    missing
  };
}

export async function readChunk(env, stage, collection, descriptor, receipts = null) {
  const record = receipts
    ? receipts.get(collection + ':' + descriptor.ordinal)
    : await env.DB.prepare(
        'SELECT * FROM quant_bundle_chunks WHERE stage_id=? AND collection=? AND ordinal=?'
      )
        .bind(stage.id, collection, descriptor.ordinal)
        .first();
  if (
    !record ||
    record.sha256 !== descriptor.sha256 ||
    record.byte_length !== descriptor.byteLength
  )
    throw new ApiError('BUNDLE_INTEGRITY', '分片索引缺失或不一致', 503);
  const object = await env.ARTIFACTS.get(record.object_key);
  if (!object || object.size !== descriptor.byteLength || object.size > BUNDLE_PROFILE.chunkBytes)
    throw new ApiError('BUNDLE_INTEGRITY', '分片字节缺失或大小不一致', 503);
  const raw = new Uint8Array(await object.arrayBuffer());
  const actual = [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
  if (actual !== descriptor.sha256)
    throw new ApiError('BUNDLE_INTEGRITY', '分片内容完整性失败', 503);
  return raw;
}

export async function uploadChunk(env, input, collectionId, ordinal, text) {
  const job = await leasedJob(env, input);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  const stage = await loadStage(env, input.stageId, job);
  if (stage.bundle_id !== input.bundleId) conflict('传输身份不匹配');
  const parsed = await parsedStage(stage),
    collection = parsed.collections.get(collectionId),
    descriptor = collection?.chunks[ordinal];
  if (!descriptor || !Number.isInteger(ordinal) || ordinal < 0)
    throw new ApiError('BUNDLE_CHUNK', '分片位置未在 manifest 声明');
  const rows = await validateChunk(text, descriptor);
  const prior = await env.DB.prepare(
    'SELECT sha256 FROM quant_bundle_chunks WHERE stage_id=? AND collection=? AND ordinal=?'
  )
    .bind(stage.id, collectionId, ordinal)
    .first();
  const reply = (idempotent) => ({
    ok: true,
    bundleId: stage.bundle_id,
    collection: collectionId,
    ordinal,
    sha256: descriptor.sha256,
    idempotent
  });
  if (prior) {
    if (prior.sha256 !== descriptor.sha256) conflict('已保存分片与 manifest 不一致');
    await readChunk(env, stage, collectionId, descriptor);
    return reply(true);
  }
  if (stage.status !== 'staging' || job.status !== 'running')
    conflict('已验证或终态传输不能新增分片');
  const indexes = [];
  for (let index = 0; index < rows.length; index++)
    indexes.push(
      await recordIndex(collectionId, rows[index], descriptor.start + index, ordinal, index)
    );
  const key = `bundle/${job.owner}/${stage.id}/${collectionId}/${ordinal}-${descriptor.sha256}.json`;
  await env.ARTIFACTS.put(key, text, {
    sha256: descriptor.sha256,
    httpMetadata: { contentType: 'application/json' }
  });
  try {
    const statements = indexStatements(env, stage, collectionId, indexes);
    statements.push(
      env.DB.prepare(
        `INSERT INTO quant_bundle_chunks(stage_id,collection,ordinal,start_row,row_count,sha256,byte_length,object_key,created_at)
      SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_bundle_stages s JOIN jobs j ON j.id=s.job_id
      WHERE s.id=? AND s.status='staging' AND j.status='running' AND j.lease_token=s.lease_token AND j.lease_until>?)`
      ).bind(
        stage.id,
        collectionId,
        ordinal,
        descriptor.start,
        descriptor.count,
        descriptor.sha256,
        descriptor.byteLength,
        key,
        NOW(),
        stage.id,
        NOW()
      )
    );
    await env.DB.batch(statements);
  } catch (error) {
    const winner = await env.DB.prepare(
      'SELECT sha256 FROM quant_bundle_chunks WHERE stage_id=? AND collection=? AND ordinal=?'
    )
      .bind(stage.id, collectionId, ordinal)
      .first();
    if (winner?.sha256 === descriptor.sha256) return reply(true);
    throw error;
  }
  const receipt = await env.DB.prepare(
    'SELECT sha256 FROM quant_bundle_chunks WHERE stage_id=? AND collection=? AND ordinal=?'
  )
    .bind(stage.id, collectionId, ordinal)
    .first();
  if (!receipt) {
    const latest = await leasedJob(env, input);
    if (['failed', 'cancelled'].includes(latest.status)) {
      await env.ARTIFACTS.delete(key);
      return terminalDiscard(latest);
    }
    conflict('上传租约或阶段状态已变化');
  }
  return reply(false);
}
