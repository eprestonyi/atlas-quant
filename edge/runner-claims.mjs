import {
  assertRunMarket,
  MARKET_RESEARCH_PROFILES,
  supportsMarket,
  researchEnabled as marketEnabled
} from './market-preparation/research.mjs';
import { readScope, bindWholeScope } from './market-preparation/scope.mjs';
import { scopeRef } from './market-preparation/common.mjs';
import { acceptedFinancialProfiles, assertRunDataset } from './datasets/research.mjs';
/** Durable claim identity: request retries recover the same job and lease. */
import { random, parse } from './runtime.mjs';
import { ApiError } from './validation.mjs';
import { validateStoredStatisticalQuant } from './statistical-quant/validation.mjs';
import { claimMetadata } from './statistical-quant/persistence.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INTERRUPTED = {
  code: 'RUNNER_INTERRUPTED',
  message: '计算服务中断，此次实验未完成。请确认后重新运行。'
};
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

function acceptsForecasts(engineVersion) {
  const version = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(String(engineVersion ?? ''));
  return !!version && (+version[1] > 0 || +version[2] >= 4);
}

async function jobPayload(env, row, supportsBundle) {
  const market = row.data_source === 'ready_market' ? await assertRunMarket(env, row) : null;
  const financial = row.data_source === 'ready_dataset' ? await assertRunDataset(env, row) : null;
  const dataset = row.dataset_key ? await env.ARTIFACTS.get(row.dataset_key) : null;
  const strategy = parse(row.spec);
  const scopeRow = await env.DB.prepare(
    'SELECT scope_id id,scope_root FROM quant_run_scopes WHERE job_id=? AND owner=?'
  )
    .bind(row.id, row.owner)
    .first();
  const wholeScope = scopeRow ? await readScope(env, row.owner, scopeRef(scopeRow)) : null;
  if (wholeScope) bindWholeScope(strategy, wholeScope.scope);
  return {
    id: row.id,
    workspaceId: row.owner,
    ...(wholeScope
      ? {
          universeScopeRef: wholeScope.scopeRef,
          universeScope: wholeScope.scope
        }
      : {}),
    leaseToken: row.lease_token,
    ...(await claimMetadata(env, row)),
    ...(market
      ? {
          marketDatasetRef: market.marketDatasetRef,
          admissionProfile: market.admissionProfile,
          sourceEvidence: market.sourceEvidence,
          marketInputUrl: `/quant/api/runner/research-markets/${row.id}/input`
        }
      : {}),
    ...(financial
      ? {
          datasetRef: financial.datasetRef,
          admissionProfile: financial.admissionProfile,
          sourceEvidence: financial.sourceEvidence,
          datasetInputUrl: `/quant/api/runner/research-datasets/${row.id}/input`,
          resultTransport: {
            format: 'atlas.quant.financial_bundle',
            version: financial.datasetRef.version === 3 ? 2 : 1
          }
        }
      : {}),
    ...(!financial && strategy?.schemaVersion === 2 && supportsBundle
      ? { resultTransport: { format: 'atlas.quant.bundle', version: 1 } }
      : {}),
    strategy:
      strategy?.schemaVersion === 2
        ? validateStoredStatisticalQuant(strategy, market ? { scopeSymbolLimit: 1000 } : {})
        : strategy,
    dataSource: row.data_source,
    dataset: dataset ? await dataset.json() : null
  };
}

export async function claimRunnerJob(env, input, now) {
  const requestId = input.requestId;
  const supportsBundle = Number(
    Array.isArray(input.transportFormats) && input.transportFormats.includes('atlas.quant.bundle/1')
  );
  const acceptedFinancial = acceptedFinancialProfiles(env, input);
  const acceptedMarket = MARKET_RESEARCH_PROFILES.filter(
    (p) => marketEnabled(env) && supportsMarket(input, p)
  );
  const marketGuard = `((data_source<>'ready_market' AND NOT EXISTS(SELECT 1 FROM quant_run_market_datasets rm WHERE rm.job_id=jobs.id)) OR (data_source='ready_market' AND EXISTS(SELECT 1 FROM quant_run_market_datasets rm WHERE rm.job_id=jobs.id AND rm.owner=jobs.owner AND rm.profile IN(SELECT value FROM json_each(?)))))`;
  const datasetGuard = `((data_source<>'ready_dataset' AND NOT EXISTS(SELECT 1 FROM quant_run_datasets rd WHERE rd.job_id=jobs.id)) OR (data_source='ready_dataset' AND EXISTS(SELECT 1 FROM quant_run_datasets rd WHERE rd.job_id=jobs.id AND rd.owner=jobs.owner AND rd.profile IN(SELECT value FROM json_each(?)))))`;
  const bundleGuard = `(?=1 OR NOT EXISTS(SELECT 1 FROM quant_runs qr JOIN quant_bundle_forecasts bf ON bf.owner=qr.owner AND bf.forecast_id=qr.source_forecast_id WHERE qr.job_id=jobs.id AND qr.kind='execution'))`;
  if (requestId !== undefined && (typeof requestId !== 'string' || !UUID.test(requestId))) {
    throw new ApiError('INVALID_CLAIM_REQUEST', '领取请求标识必须是小写 UUID');
  }
  // Expiration is terminal even for a retried request. Never revive an old lease.
  await env.DB.prepare(
    "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE status='running' AND lease_until<?"
  )
    .bind(JSON.stringify(INTERRUPTED), now, now)
    .run();
  const lease = random(),
    leaseUntil = new Date(Date.now() + 20 * 60000).toISOString();
  const supportsForecast = Number(acceptsForecasts(input.engineVersion));
  if (requestId === undefined) {
    // Compatibility for pre-0.4 runtimes; no durable request identity was sent.
    const row = await env.DB.prepare(
      `UPDATE jobs SET status='running',lease_token=?,lease_until=?,updated_at=? WHERE id=(SELECT id FROM jobs WHERE status='queued' AND NOT EXISTS(SELECT 1 FROM meta WHERE key='runner_maintenance' AND value='paused') AND (?=1 OR COALESCE(json_extract(spec,'$.schemaVersion'),1)<2) AND ${bundleGuard} AND ${datasetGuard} AND ${marketGuard} ORDER BY created_at,id LIMIT 1) AND status='queued' RETURNING *`
    )
      .bind(
        lease,
        leaseUntil,
        now,
        supportsForecast,
        supportsBundle,
        JSON.stringify(acceptedFinancial),
        JSON.stringify(acceptedMarket)
      )
      .first();
    return { job: row ? await jobPayload(env, row, supportsBundle) : null };
  }

  // D1 batch is a transaction. Reserve a queued job and change its state together.
  // changes() belongs to the INSERT immediately before UPDATE: an existing ID
  // never rotates its lease, including concurrent retries after a lost response.
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO runner_claims(request_id,job_id,created_at)
      SELECT ?,id,? FROM jobs
      WHERE status='queued'
        AND NOT EXISTS(SELECT 1 FROM runner_claims WHERE request_id=?)
        AND NOT EXISTS(SELECT 1 FROM meta WHERE key='runner_maintenance' AND value='paused')
        AND (?=1 OR COALESCE(json_extract(spec,'$.schemaVersion'),1)<2)
        AND ${bundleGuard} AND ${datasetGuard} AND ${marketGuard}
      ORDER BY created_at,id LIMIT 1`
    ).bind(
      requestId,
      now,
      requestId,
      supportsForecast,
      supportsBundle,
      JSON.stringify(acceptedFinancial),
      JSON.stringify(acceptedMarket)
    ),
    env.DB.prepare(
      `UPDATE jobs SET status='running',lease_token=?,lease_until=?,updated_at=?
      WHERE id=(SELECT job_id FROM runner_claims WHERE request_id=?)
        AND status='queued' AND changes()=1`
    ).bind(lease, leaseUntil, now, requestId),
    env.DB.prepare(
      'SELECT jobs.* FROM runner_claims JOIN jobs ON jobs.id=runner_claims.job_id WHERE request_id=?'
    ).bind(requestId)
  ]);
  const row = results[2].results[0];
  if (!row) return { job: null, claim: { requestId, status: 'empty' } };
  const claim = { requestId, status: row.status, jobId: row.id };
  if (TERMINAL.has(row.status)) return { job: null, claim };
  if (row.status !== 'running' || !row.lease_token) {
    throw new ApiError('CLAIM_STATE_CONFLICT', '领取记录状态不一致，停止领取并检查记录', 409);
  }
  if (row.data_source === 'ready_market') {
    const r = await env.DB.prepare(
      'SELECT profile FROM quant_run_market_datasets WHERE job_id=? AND owner=?'
    )
      .bind(row.id, row.owner)
      .first();
    if (!r || !acceptedMarket.includes(r.profile))
      throw new ApiError('RUNNER_UPGRADE_REQUIRED', '完整市场池需要显式来源与模型能力', 409);
  }
  const financialRelation = await env.DB.prepare(
    'SELECT owner,profile FROM quant_run_datasets WHERE job_id=?'
  )
    .bind(row.id)
    .first();
  if (row.data_source === 'ready_dataset' || financialRelation) {
    if (
      row.data_source !== 'ready_dataset' ||
      !financialRelation ||
      financialRelation.owner !== row.owner ||
      !acceptedFinancial.includes(financialRelation.profile)
    )
      throw new ApiError(
        'RUNNER_UPGRADE_REQUIRED',
        '此研究需要匹配的金融来源、快照、结果与模型能力',
        409
      );
  }
  if (!supportsBundle) {
    const source = await env.DB.prepare(
      `SELECT 1 FROM quant_runs q JOIN quant_bundle_forecasts b ON b.owner=q.owner AND b.forecast_id=q.source_forecast_id WHERE q.job_id=? AND q.kind='execution'`
    )
      .bind(row.id)
      .first();
    if (source)
      throw new ApiError('RUNNER_UPGRADE_REQUIRED', '此领取请求需要支持完整分片的计算服务', 409);
  }
  return { job: await jobPayload(env, row, supportsBundle), claim };
}
