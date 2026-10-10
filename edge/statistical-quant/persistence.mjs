import { readPrivateObject } from '../private-objects.mjs';
export { readPrivateObject } from '../private-objects.mjs';
import { ownedForecastStage, streamDocumentResponse, transportView } from '../bundles/user-api.mjs';
import { parsedStage } from '../bundles/storage.mjs';
import { pageRecords, pageQuery } from '../bundles/pages.mjs';
import { reportSummary } from '../bundles/publication.mjs';
import { ApiError } from '../errors.mjs';
import { validateStoredStatisticalQuant } from './validation.mjs';
import { NOW, json, parse, sha } from '../runtime.mjs';
import { diagnosticSummary, universeSummary, forecastMetadataSummary } from './summaries.mjs';

export const MAX_ARTIFACT_BYTES = 24 * 1024 * 1024;
export const isHash = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export const notFound = () => new ApiError('NOT_FOUND', '研究产物不存在', 404);
export const experimentView = (row) => ({
  id: row.id,
  name: row.name,
  version: row.version,
  // Stored views are not admission: large drafts still require an owner-bound scope.
  strategy: validateStoredStatisticalQuant(parse(row.spec), {scopeSymbolLimit:10000}),
  parentId: row.parent_id,
  archived: !!row.archived,
  createdAt: row.created_at,
  updatedAt: row.updated_at
});
export const forecastView = (row) => ({
  id: row.id,
  forecastArtifactId: row.id,
  jobId: row.job_id,
  experimentId: row.experiment_id,
  modelVersionId: row.model_version_id,
  predictionConfigHash: row.prediction_config_hash,
  dataFingerprint: row.data_fingerprint,
  rowCount: row.row_count,
  targetCount: row.target_count,
  modelFitCount: row.model_fit_count,
  metadata: forecastMetadataSummary(parse(row.metadata)),
  createdAt: row.created_at
});

export async function ownedExperiment(env, owner, id) {
  const row = await env.DB.prepare('SELECT * FROM quant_experiments WHERE id=? AND owner=?')
    .bind(id, owner)
    .first();
  if (!row) throw notFound();
  return row;
}
export async function ownedForecast(env, owner, id) {
  const row = await env.DB.prepare('SELECT * FROM quant_forecast_artifacts WHERE id=? AND owner=?')
    .bind(id, owner)
    .first();
  if (!row) throw notFound();
  return row;
}
export async function quantRun(env, jobId) {
  return env.DB.prepare('SELECT * FROM quant_runs WHERE job_id=?').bind(jobId).first();
}
export async function claimMetadata(env, job) {
  const link = await quantRun(env, job.id);
  if (!link) return {};
  const metadata = {
    jobKind: link.kind,
    experimentId: link.experiment_id,
    experimentVersion: link.experiment_version
  };
  if (link.kind === 'execution') {
    const artifact = await ownedForecast(env, job.owner, link.source_forecast_id);
    const original = await env.DB.prepare('SELECT spec FROM jobs WHERE id=? AND owner=?')
      .bind(artifact.job_id, job.owner)
      .first();
    if (!original) throw new ApiError('ARTIFACT_UNAVAILABLE', '来源研究配置暂不可读取', 503);
    const bundle = await env.DB.prepare(
      "SELECT s.bundle_id FROM quant_bundle_forecasts b JOIN quant_bundle_stages s ON s.id=b.stage_id WHERE b.owner=? AND b.forecast_id=? AND s.owner=b.owner AND s.status='committed'"
    )
      .bind(job.owner, artifact.id)
      .first();
    return {
      ...metadata,
      ...(bundle
        ? {
            sourceTransport: {
              format: 'atlas.quant.bundle',
              version: 1,
              bundleId: bundle.bundle_id
            }
          }
        : {}),
      forecastArtifactId: artifact.id,
      originalStrategy: validateStoredStatisticalQuant(parse(original.spec))
    };
  }
  return metadata;
}

/** Completion is recoverable: repeated exact completion repairs missing indexes. */
export async function validateForecastCompletion(env, job, result) {
  const link = await quantRun(env, job.id);
  if (!link) return null;
  if (
    result?.schemaVersion !== 2 ||
    result?.research?.mode !== 'statistical_quant' ||
    !result.selection ||
    !result.provenance ||
    !Array.isArray(result.equity) ||
    !Array.isArray(result.trades)
  )
    throw new ApiError('INVALID_RESULT', '新研究缺少预测协议证据');
  const artifact = result.forecasts;
  if (result.strategy?.research?.returnStudy) {
    if (artifact?.studyProtocol !== 'asset-return-study/1' || result.metrics !== null ||
        result.strategy.execution?.enabled !== false || result.execution?.enabled !== false ||
        result.equity.length || result.trades.length)
      throw new ApiError('INVALID_RESULT', '收益研究必须保留独立研究协议，不包含执行绩效');
  }
  if (
    !artifact ||
    artifact.schemaVersion !== 1 ||
    !isHash(artifact.artifactId) ||
    !isHash(artifact.predictionConfigHash) ||
    !isHash(artifact.dataFingerprint) ||
    !Array.isArray(artifact.rows) ||
    artifact.rows.length !== artifact.totalRows ||
    artifact.truncated !== false ||
    !Array.isArray(artifact.targetDefinitions) ||
    !Array.isArray(artifact.modelFits) ||
    artifact.sourceStrategy?.schemaVersion !== 2
  )
    throw new ApiError('INVALID_FORECAST_ARTIFACT', '预测产物必须完整、有版本、来源策略和数据指纹');
  if (
    artifact.rows.length > 25000 ||
    artifact.targetDefinitions.length > 110000 ||
    artifact.modelFits.length > 110000
  )
    throw new ApiError('FORECAST_BUDGET', '预测产物超过资源上限', 413);
  const forecastIds = new Set();
  for (const row of artifact.rows) {
    if (
      !row ||
      typeof row.forecastId !== 'string' ||
      !row.forecastId ||
      forecastIds.has(row.forecastId) ||
      !['valid', 'invalid'].includes(row.status)
    )
      throw new ApiError('INVALID_FORECAST_ARTIFACT', '预测记录身份或状态不完整');
    forecastIds.add(row.forecastId);
  }
  for (const trade of result.trades)
    if (!trade?.forecastId || !forecastIds.has(trade.forecastId))
      throw new ApiError('INVALID_FORECAST_REFERENCE', '每笔交易必须引用本产物的预测');
  if (
    result.metrics === null &&
    (result.equity.length || result.trades.length || result.strategy?.execution?.enabled !== false)
  )
    throw new ApiError('INVALID_RESULT', '纯预测完成不能伪造执行结果');
  const artifactText = JSON.stringify(artifact);
  if (new TextEncoder().encode(artifactText).byteLength > MAX_ARTIFACT_BYTES)
    throw new ApiError('FORECAST_BUDGET', '完整预测产物超过24MiB', 413);
  const artifactHash = await sha(artifactText);
  if (link.kind === 'execution') {
    const source = await ownedForecast(env, job.owner, link.source_forecast_id);
    if (source.id !== artifact.artifactId || source.artifact_hash !== artifactHash)
      throw new ApiError('FORECAST_ARTIFACT_MISMATCH', '执行任务不得改变来源预测产物');
    return { kind: 'execution', link, artifact };
  }
  if (!link.snapshot_key || link.snapshot_fingerprint !== artifact.dataFingerprint)
    throw new ApiError('FORECAST_SNAPSHOT_REQUIRED', '完成预测前须上传同指纹冻结行情', 409);
  const prior = await env.DB.prepare(
    'SELECT artifact_hash FROM quant_forecast_artifacts WHERE owner=? AND id=?'
  )
    .bind(job.owner, artifact.artifactId)
    .first();
  if (prior && prior.artifact_hash !== artifactHash)
    throw new ApiError('FORECAST_ARTIFACT_CONFLICT', '同一产物ID不能改变内容', 409);
  return { kind: 'forecast', link, artifact, artifactText, artifactHash };
}

export async function persistForecastCompletion(env, job, result, prepared) {
  if (!prepared) return;
  if (prepared.kind === 'execution') return;
  const { artifact, artifactText, artifactHash, link } = prepared;
  const artifactKey = `forecasts/${job.owner}/${artifact.artifactId}/${artifactHash}.json`;
  await env.ARTIFACTS.put(artifactKey, artifactText, {
    httpMetadata: { contentType: 'application/json' }
  });
  const modelVersionId = await sha(
    JSON.stringify({
      predictionConfigHash: artifact.predictionConfigHash,
      modelFits: artifact.modelFits
    })
  );
  const metadata = {
    diagnostics: diagnosticSummary(artifact.diagnostics),
    validation: diagnosticSummary(result.validation),
    target: artifact.sourceStrategy.target,
    universe: universeSummary(artifact.sourceStrategy.universe),
    model: artifact.sourceStrategy.model,
    synthetic: !!result.provenance.synthetic,
    independentlyValidatedAlpha: false
  };
  const time = NOW();
  await env.DB.batch([
    env.DB.prepare(
      'INSERT OR IGNORE INTO quant_forecast_artifacts(id,owner,job_id,experiment_id,model_version_id,artifact_key,artifact_hash,dataset_key,dataset_hash,data_fingerprint,prediction_config_hash,row_count,target_count,model_fit_count,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    ).bind(
      artifact.artifactId,
      job.owner,
      job.id,
      link.experiment_id,
      modelVersionId,
      artifactKey,
      artifactHash,
      link.snapshot_key,
      link.snapshot_hash,
      artifact.dataFingerprint,
      artifact.predictionConfigHash,
      artifact.rows.length,
      artifact.targetDefinitions.length,
      artifact.modelFits.length,
      JSON.stringify(metadata),
      time
    ),
    env.DB.prepare(
      'INSERT OR IGNORE INTO quant_model_versions(id,owner,experiment_id,artifact_id,family,config_hash,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)'
    ).bind(
      modelVersionId,
      job.owner,
      link.experiment_id,
      artifact.artifactId,
      artifact.sourceStrategy.model.family,
      artifact.predictionConfigHash,
      JSON.stringify({
        model: artifact.sourceStrategy.model,
        fitCount: artifact.modelFits.length,
        engineVersion: result.engineVersion,
        selection: result.selection
      }),
      time
    ),
    env.DB.prepare('UPDATE quant_runs SET forecast_artifact_id=? WHERE job_id=? AND owner=?').bind(
      artifact.artifactId,
      job.id,
      job.owner
    )
  ]);
}

export async function forecastResponse(req, env, owner, id, download = false) {
  const record = await ownedForecast(env, owner, id);
  const bundleStage = await ownedForecastStage(env, owner, id);
  if (bundleStage) {
    const parsed = await parsedStage(bundleStage);
    if (download)
      return streamDocumentResponse(env, bundleStage, parsed, 'forecast', {
        prefix:
          JSON.stringify({ forecast: forecastView(record) }).slice(0, -1) +
          ',"artifact":{"artifactId":"' +
          id +
          '",',
        suffix: '}}',
        unwrap: true,
        filename: `atlas-forecast-${id}.json`
      });
    const params = new URL(req.url).searchParams;
    if (params.has('bundleId') && params.get('bundleId') !== bundleStage.bundle_id)
      throw new ApiError('BUNDLE_VERSION_CHANGED', '预测版本不一致', 409);
    params.set('collection', 'forecasts');
    const page = await pageRecords(env, bundleStage, parsed, pageQuery(params, parsed));
    return json({
      forecast: forecastView(record),
      artifact: {
        ...reportSummary(parsed).forecasts,
        sourceStrategy: parsed.metadata.forecast.sourceStrategy,
        rows: page.items,
        targetDefinitions: page.related.targets,
        modelFits: page.related.modelFits,
        totalRows: record.row_count,
        truncated: page.items.length !== record.row_count
      },
      offset: page.offset,
      limit: page.limit,
      preview: true,
      transport: transportView(bundleStage, parsed, record.job_id)
    });
  }
  const artifact = await readPrivateObject(env, record.artifact_key, record.artifact_hash);
  if (download)
    return json({ forecast: forecastView(record), artifact }, 200, {
      'content-disposition': `attachment; filename="atlas-forecast-${id}.json"`
    });
  const params = new URL(req.url).searchParams;
  const offset = Number(params.get('offset') ?? 0),
    limit = Number(params.get('limit') ?? 50);
  if (
    !Number.isInteger(offset) ||
    offset < 0 ||
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 200
  )
    throw new ApiError('INVALID_PAGE', '预测预览offset≥0，limit为1–200');
  const { hedgeFits = [], ...previewArtifact } = artifact;
  const rows = artifact.rows.slice(offset, offset + limit),
    targetIds = new Set(rows.map((r) => r.targetId)),
    fitIds = new Set(rows.map((r) => r.modelFitId));
  return json({
    forecast: forecastView(record),
    artifact: {
      ...previewArtifact,
      diagnostics: diagnosticSummary(artifact.diagnostics),
      hedgeFitCount: hedgeFits.length,
      rows,
      targetDefinitions: artifact.targetDefinitions.filter((t) => targetIds.has(t.id)),
      modelFits: artifact.modelFits.filter((f) => fitIds.has(f.id)),
      totalRows: artifact.totalRows,
      truncated: rows.length !== artifact.totalRows
    },
    offset,
    limit,
    preview: true
  });
}

/** Preserve referenced research datasets; prune only old failed/canceled inputs. */
export async function cleanupFailedForecastSnapshots(env, cutoff) {
  const rows = await env.DB.prepare(
    "SELECT q.job_id,q.snapshot_key FROM quant_runs q JOIN jobs j ON j.id=q.job_id WHERE j.status IN ('failed','cancelled') AND j.created_at<? AND q.snapshot_key IS NOT NULL AND NOT EXISTS(SELECT 1 FROM quant_forecast_artifacts f WHERE f.dataset_key=q.snapshot_key) LIMIT 100"
  )
    .bind(cutoff)
    .all();
  for (const row of rows.results) {
    await env.ARTIFACTS.delete(row.snapshot_key);
    await env.DB.prepare(
      'UPDATE quant_runs SET snapshot_key=NULL WHERE job_id=? AND snapshot_key=?'
    )
      .bind(row.job_id, row.snapshot_key)
      .run();
  }
  return rows.results.length;
}
