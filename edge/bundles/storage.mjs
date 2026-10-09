import { ApiError } from '../errors.mjs';
import { NOW, parse, random, sha } from '../runtime.mjs';
import { validateStoredStatisticalQuant } from '../statistical-quant/validation.mjs';
import { BUNDLE_PROFILE, HASH } from './profile.mjs';
import { byteLength } from './json.mjs';
import { validateManifest, validateChunk } from './manifest.mjs';
import { validateContextChunk } from './context-sources.mjs';
import { FINANCIAL_FORMAT, validateFinancialManifest } from '../financial-bundles/manifest.mjs';
import { validateFinancialGraphManifest } from '../financial-graph-bundles/manifest.mjs';
import {
  assertMarketBundle,
  storedMarketAdmission,
  validateMarketBundle
} from '../market-preparation/bundle.mjs';
import { assertRunMarket } from '../market-preparation/research.mjs';
import { marketAssetTargets } from '../market-preparation/hedge-index.mjs';
import { indexStatements, recordIndex } from './records.mjs';
import {
  INDEXED_SNAPSHOT,
  SORTED_SNAPSHOT,
  requestedSnapshotStrategy,
  snapshotValidation,
  stageMetadata,
  summarizeSnapshot,
  snapshotReceiptStatement,
  receiptPath,
  assertSnapshotReceipt
} from './snapshot-index.mjs';

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
  if (storedMarketAdmission(stage))
    return validateMarketBundle(stage.manifest_text, stage.bundle_id);
  const { format, version } = parse(stage.manifest_text) || {};
  if (format === FINANCIAL_FORMAT) {
    if (version === 1) return validateFinancialManifest(stage.manifest_text, stage.bundle_id);
    if (version === 2) return validateFinancialGraphManifest(stage.manifest_text, stage.bundle_id);
    throw new ApiError('BUNDLE_FORMAT', '未登记金融传输版本', 409);
  }
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
  const stage = await loadStage(env, source.stage_id);
  if (storedMarketAdmission(stage))
    throw new ApiError('MARKET_EXECUTION_DISABLED', '完整市场研究目前仅支持预测', 409);
  if ((await parsedStage(stage)).manifest.format !== 'atlas.quant.bundle')
    throw new ApiError('FINANCIAL_EXECUTION_DISABLED', '金融来源尚不支持独立执行', 409);
  return stage;
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
  const options = job.data_source === 'ready_market' ? { scopeSymbolLimit: 1000 } : {};
  const strategy = validateStoredStatisticalQuant(parse(job.spec), options);
  const reported = validateStoredStatisticalQuant(metadata.report.strategy, options);
  const source = validateStoredStatisticalQuant(metadata.forecast.sourceStrategy, options);
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

export function assertTransport(
  parsed,
  expectedFormat = 'atlas.quant.bundle',
  expectedVersion = 1
) {
  if (parsed.manifest.format !== expectedFormat || parsed.manifest.version !== expectedVersion)
    throw new ApiError('BUNDLE_FORMAT', '此入口不接受该传输格式', 409);
}
export async function beginBundle(
  env,
  input,
  { validate = validateManifest, authorize = null } = {}
) {
  const requestedStrategy = requestedSnapshotStrategy(input);
  const job = await leasedJob(env, input),
    link = await researchLink(env, job);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  let stage = await env.DB.prepare('SELECT * FROM quant_bundle_stages WHERE job_id=?')
    .bind(job.id)
    .first();
  // Only the persisted owner/job binding may select the larger numerical gate.
  const market = job.data_source === 'ready_market';
  if (market && job.status === 'running') await assertRunMarket(env, job);
  const parsed = await (market ? validateMarketBundle : validate)(
    input.manifestText,
    input.bundleId
  );
  const marketAdmission = market
    ? job.status === 'running'
      ? await assertMarketBundle(env, job, parsed)
      : stage?.status === 'committed'
        ? storedMarketAdmission(stage)
        : null
    : null;
  if (market && !marketAdmission) conflict('终态市场任务没有已提交的来源准入');
  if (marketAdmission) parsed.metadata._marketAdmission = marketAdmission;
  await assertJobContent(env, job, link, parsed);
  if (
    stage &&
    requestedStrategy !== null &&
    requestedStrategy !== snapshotValidation(stage).strategy
  )
    conflict('同一上传阶段不能更改冻结行情索引策略');
  if (
    stage &&
    (stage.bundle_id !== parsed.bundleId ||
      stage.manifest_text !== parsed.manifestText ||
      stage.lease_token !== job.lease_token ||
      stage.owner !== job.owner)
  )
    conflict('同一任务的分片内容不可更换');
  if (job.status === 'running' && authorize) await authorize(env, job, parsed);
  if (!stage) {
    if (job.status !== 'running') conflict('终态任务不能创建新分片');
    const id = random(),
      now = NOW(),
      key = `bundle/${job.owner}/${id}/manifest.json`;
    const strategy = requestedStrategy ?? (market ? SORTED_SNAPSHOT : INDEXED_SNAPSHOT);
    if (market && strategy !== SORTED_SNAPSHOT) conflict('完整市场研究必须使用有序行情校验');
    if (strategy === SORTED_SNAPSHOT && env.BUNDLE_SNAPSHOT_SORTED_V1 !== 'true')
      throw new ApiError('BUNDLE_POLICY_UNAVAILABLE', '服务端尚未启用有序行情索引策略', 409);
    const metadata = stageMetadata(parsed, strategy);
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
      stage.lease_token !== job.lease_token ||
      snapshotValidation(stage).strategy !== strategy
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
    snapshotIndexStrategy: snapshotValidation(stage).strategy,
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

export async function uploadChunk(
  env,
  input,
  collectionId,
  ordinal,
  text,
  {
    expectedFormat = 'atlas.quant.bundle',
    expectedVersion = 1,
    authorize = null,
    validateRows = null
  } = {}
) {
  const job = await leasedJob(env, input);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  const stage = await loadStage(env, input.stageId, job);
  if (stage.bundle_id !== input.bundleId) conflict('传输身份不匹配');
  const parsed = await parsedStage(stage),
    collection = parsed.collections.get(collectionId),
    descriptor = collection?.chunks[ordinal];
  if (!descriptor || !Number.isInteger(ordinal) || ordinal < 0)
    throw new ApiError('BUNDLE_CHUNK', '分片位置未在 manifest 声明');
  assertTransport(parsed, expectedFormat, expectedVersion);
  if (job.status === 'running' && authorize) await authorize(env, job, parsed);
  const codec = parsed.manifest.documents[collection.document].codec ?? 'forecast_json_v1';
  const rows = await validateChunk(text, descriptor, codec);
  if (collectionId === 'snapshotContextSources')
    await validateContextChunk(text, rows, descriptor.start, parsed.metadata.report.provenance.contextSources);
  if (validateRows) validateRows(collectionId, rows, parsed);
  const sortedSnapshot =
    collectionId === 'snapshotRows' && snapshotValidation(stage).strategy === SORTED_SNAPSHOT;
  const snapshotReceipt = sortedSnapshot ? summarizeSnapshot(rows, descriptor) : null;
  const verifyPriorSnapshot = async () => {
    if (!snapshotReceipt) return;
    const fresh = await loadStage(env, stage.id, job);
    const receipt = snapshotValidation(fresh).receipts[String(ordinal)];
    assertSnapshotReceipt(receipt, descriptor);
    if (!same(receipt, snapshotReceipt)) conflict('行情边界与已保存分片不一致');
  };
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
    await verifyPriorSnapshot();
    return reply(true);
  }
  if (stage.status !== 'staging' || job.status !== 'running')
    conflict('已验证或终态传输不能新增分片');
  // Existing exact receipts remain acknowledgeable after revocation; new
  // market writes require the current server-side scope and feature gate.
  if (job.data_source === 'ready_market')
    await assertMarketBundle(env, job, parsed, storedMarketAdmission(stage));
  // The strategy was just compared to the owner-scoped immutable source above.
  // Only this admitted market route can use compact all-asset hedge references.
  const marketHedgeTargets =
    job.data_source === 'ready_market' && collectionId === 'hedgeFits'
      ? (await marketAssetTargets(parsed.metadata.report.strategy.universe.symbols)).map(
          (t) => t.id
        )
      : null;
  const indexes = [];
  if (!sortedSnapshot)
    for (let index = 0; index < rows.length; index++)
      indexes.push(
        await recordIndex(collectionId, rows[index], descriptor.start + index, ordinal, index, {
          marketHedgeTargets
        })
      );
  const key = `bundle/${job.owner}/${stage.id}/${collectionId}/${ordinal}-${descriptor.sha256}.json`;
  await env.ARTIFACTS.put(key, text, {
    sha256: descriptor.sha256,
    httpMetadata: { contentType: 'application/json' }
  });
  try {
    const statements = indexStatements(env, stage, collectionId, indexes);
    if (snapshotReceipt)
      statements.push(snapshotReceiptStatement(env, stage, ordinal, snapshotReceipt, NOW()));
    const receiptGuard = snapshotReceipt ? ' AND json_extract(s.metadata,?)=?' : '';
    const guardValues = snapshotReceipt
      ? [receiptPath(ordinal) + '.sha256', descriptor.sha256]
      : [];
    statements.push(
      env.DB.prepare(
        `INSERT INTO quant_bundle_chunks(stage_id,collection,ordinal,start_row,row_count,sha256,byte_length,object_key,created_at)
      SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_bundle_stages s JOIN jobs j ON j.id=s.job_id
      WHERE s.id=? AND s.status='staging' AND j.status='running' AND j.lease_token=s.lease_token AND j.lease_until>?${receiptGuard})`
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
        NOW(),
        ...guardValues
      )
    );
    await env.DB.batch(statements);
  } catch (error) {
    const winner = await env.DB.prepare(
      'SELECT sha256 FROM quant_bundle_chunks WHERE stage_id=? AND collection=? AND ordinal=?'
    )
      .bind(stage.id, collectionId, ordinal)
      .first();
    if (winner?.sha256 === descriptor.sha256) {
      await verifyPriorSnapshot();
      return reply(true);
    }
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
