import { ApiError } from '../errors.mjs';
import { NOW, sha } from '../runtime.mjs';
import { diagnosticSummary, universeSummary } from '../statistical-quant/summaries.mjs';
import { BUNDLE_PROFILE } from './profile.mjs';
import { byteLength } from './json.mjs';
import {
  leasedJob,
  loadStage,
  parsedStage,
  researchLink,
  terminalDiscard,
  assertTransport
} from './storage.mjs';

function removePath(value, path) {
  const parts = path.slice(1).split('/');
  let node = value;
  for (const key of parts.slice(0, -1)) {
    if (!node || typeof node !== 'object') return;
    node = node[key];
  }
  if (node && typeof node === 'object') delete node[parts.at(-1)];
}

export function reportSummary(parsed) {
  const report = structuredClone(parsed.metadata.report);
  const forecast = structuredClone(parsed.metadata.forecast);
  for (const collection of parsed.collections.values()) {
    if (collection.document === 'forecast') removePath(forecast, collection.path);
    if (collection.document === 'report') removePath(report, collection.path);
  }
  forecast.artifactId = parsed.manifest.forecastArtifactId;
  forecast.diagnostics = diagnosticSummary(forecast.diagnostics);
  // The original sourceStrategy remains immutable in the full forecast document.
  // A copy here would repeat the already present research configuration.
  delete forecast.sourceStrategy;
  report.forecasts = forecast;
  report.validation = diagnosticSummary(report.validation);
  report.reportSummary = true;
  if (byteLength(JSON.stringify(report)) > BUNDLE_PROFILE.summaryBytes) {
    throw new ApiError('BUNDLE_BUDGET', '报告摘要超过固定预算', 413);
  }
  return report;
}

/** Publish job, forecast, model and frozen-input references in one D1 transaction. */
export async function completeBundle(
  env,
  input,
  { expectedFormat = 'atlas.quant.bundle', authorize = null } = {}
) {
  const job = await leasedJob(env, input);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  const stage = await loadStage(env, input.stageId, job);
  if (stage.bundle_id !== input.bundleId)
    throw new ApiError('BUNDLE_CONFLICT', '完成内容身份不一致', 409);
  const parsed = await parsedStage(stage);
  assertTransport(parsed, expectedFormat);
  if (stage.status === 'committed' && job.status === 'completed')
    return { ok: true, status: 'completed', idempotent: true };
  if (stage.status !== 'verified' || job.status !== 'running')
    throw new ApiError('BUNDLE_NOT_VERIFIED', '完整验证后才能发布研究', 409);
  if (authorize) await authorize(env, job, parsed);
  const link = await researchLink(env, job);
  const report = reportSummary(parsed),
    forecast = parsed.metadata.forecast,
    manifest = parsed.manifest;
  const modelVersionId = await sha('atlas.bundle.model.v1\0' + manifest.forecastArtifactId);
  const summary = {
    ...(report.metrics ?? {}),
    winner: report.selection.winner,
    evidenceStatus: report.selection.evidenceStatus,
    engineVersion: report.engineVersion
  };
  const time = NOW();
  const gate = `EXISTS(SELECT 1 FROM quant_bundle_stages s JOIN jobs j ON j.id=s.job_id
    WHERE s.id=? AND s.status='committed' AND j.status='running' AND j.owner=s.owner AND j.lease_token=s.lease_token AND j.lease_until>?)`;
  const statements = [
    env.DB.prepare(
      `UPDATE quant_bundle_stages SET status='committed',updated_at=?
    WHERE id=? AND status='verified' AND EXISTS(SELECT 1 FROM jobs WHERE id=? AND owner=? AND lease_token=? AND status='running' AND lease_until>?)`
    ).bind(time, stage.id, job.id, job.owner, job.lease_token, time)
  ];
  if (link.kind === 'forecast') {
    const metadata = {
      diagnostics: diagnosticSummary(forecast.diagnostics),
      validation: diagnosticSummary(report.validation),
      target: forecast.sourceStrategy.target,
      universe: universeSummary(forecast.sourceStrategy.universe),
      model: forecast.sourceStrategy.model,
      synthetic: !!report.provenance.synthetic,
      independentlyValidatedAlpha: false,
      ...(manifest.sourceEvidence
        ? {
            sourceEvidence: manifest.sourceEvidence,
            transportFormat: manifest.format,
            executionEligible: false
          }
        : {})
    };
    statements.push(
      env.DB.prepare(
        `INSERT OR IGNORE INTO quant_forecast_artifacts
      (id,owner,job_id,experiment_id,model_version_id,artifact_key,artifact_hash,dataset_key,dataset_hash,data_fingerprint,prediction_config_hash,row_count,target_count,model_fit_count,metadata,created_at)
      SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE ${gate}`
      ).bind(
        manifest.forecastArtifactId,
        job.owner,
        job.id,
        link.experiment_id,
        modelVersionId,
        stage.manifest_key,
        manifest.forecastArtifactId,
        stage.manifest_key,
        manifest.documents.snapshot.sha256,
        manifest.dataFingerprint,
        manifest.predictionConfigHash,
        parsed.collections.get('forecasts').rowCount,
        parsed.collections.get('targets').rowCount,
        parsed.collections.get('modelFits').rowCount,
        JSON.stringify(metadata),
        time,
        stage.id,
        time
      )
    );
    statements.push(
      env.DB.prepare(
        `INSERT OR IGNORE INTO quant_bundle_forecasts(owner,forecast_id,stage_id,dataset_stage_id)
      SELECT ?,?,?,? WHERE ${gate}`
      ).bind(job.owner, manifest.forecastArtifactId, stage.id, stage.id, stage.id, time)
    );
    statements.push(
      env.DB.prepare(
        `INSERT OR IGNORE INTO quant_model_versions
      (id,owner,experiment_id,artifact_id,family,config_hash,metadata,created_at)
      SELECT ?,?,?,?,?,?,?,? WHERE ${gate}`
      ).bind(
        modelVersionId,
        job.owner,
        link.experiment_id,
        manifest.forecastArtifactId,
        forecast.sourceStrategy.model.family,
        manifest.predictionConfigHash,
        JSON.stringify({
          model: forecast.sourceStrategy.model,
          fitCount: parsed.collections.get('modelFits').rowCount,
          engineVersion: report.engineVersion,
          selection: report.selection
        }),
        time,
        stage.id,
        time
      )
    );
  }
  statements.push(
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_bundle_runs(job_id,owner,stage_id)
    SELECT ?,?,? WHERE ${gate}`
    ).bind(job.id, job.owner, stage.id, stage.id, time)
  );
  statements.push(
    env.DB.prepare(
      `UPDATE quant_runs SET forecast_artifact_id=? WHERE job_id=? AND owner=? AND ${gate}`
    ).bind(manifest.forecastArtifactId, job.id, job.owner, stage.id, time)
  );
  statements.push(
    env.DB.prepare(
      `UPDATE jobs SET status='completed',result_key=?,summary=?,updated_at=?
    WHERE id=? AND owner=? AND lease_token=? AND status='running' AND lease_until>?
      AND EXISTS(SELECT 1 FROM quant_bundle_stages WHERE id=? AND status='committed')`
    ).bind(
      stage.manifest_key,
      JSON.stringify(summary),
      time,
      job.id,
      job.owner,
      job.lease_token,
      time,
      stage.id
    )
  );
  const outcomes = await env.DB.batch(statements);
  if (!outcomes.at(-1).meta.changes) {
    const current = await leasedJob(env, input);
    if (['failed', 'cancelled'].includes(current.status)) return terminalDiscard(current);
    if (current.status === 'completed' && current.result_key === stage.manifest_key)
      return { ok: true, status: 'completed', idempotent: true };
    throw new ApiError('BUNDLE_CONFLICT', '发布租约或内容已变化', 409);
  }
  return { ok: true, status: 'completed', bundleId: stage.bundle_id };
}
