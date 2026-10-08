import { datasetArchiveResponse } from './archive.mjs';
import { researchEnabled, supportsFinancialDatasets, supportsFinancialResearchProfile, FINANCIAL_AUTO_PROFILE } from './research.mjs';
import { financialResearchAdmissions } from './research-profile.mjs';
/** Owner-scoped control plane; result bytes remain private, bounded R2 parts. */
import { body } from '../runtime.mjs';
import { pageQuery } from '../financial/common.mjs';
import {
  PROFILE,
  CAPABILITY,
  LIMITS,
  enabled,
  requireEnabled,
  json,
  parse,
  fail,
  owned,
  jobDTO,
  datasetRef,
  runnerInfo,
  hash,
  bytes,
} from './common.mjs';
import { createPlan, startPlan, planDTO } from './plans.mjs';
import { expire, cancelJob } from './jobs.mjs';
async function financialResearchAvailability(env) {
  const runner = await env.DB.prepare("SELECT value,updated_at FROM meta WHERE key='runner'").first();
  const fresh = !!runner && Date.now()-Date.parse(runner.updated_at) < 120000;
  const capability = parse(runner?.value, {});
  return {ridge: fresh && supportsFinancialDatasets(capability),
    auto: fresh && supportsFinancialResearchProfile(capability, FINANCIAL_AUTO_PROFILE)};
}

async function marketList(env, owner, url) {
  const { page, pageSize, offset } = pageQuery(url);
  const base = `FROM quant_bundle_runs b JOIN quant_bundle_stages s ON s.id=b.stage_id AND s.owner=b.owner AND s.status='committed' JOIN jobs j ON j.id=b.job_id AND j.owner=b.owner AND j.status='completed' JOIN quant_runs q ON q.job_id=j.id AND q.owner=j.owner AND q.kind='forecast' WHERE b.owner=?`;
  const r = await env.DB.batch([
    env.DB.prepare(
      `SELECT j.id,j.name,j.created_at,j.data_source,json_extract(j.spec,'$.universe') scope,s.bundle_id,json_extract(s.manifest_text,'$.documents.snapshot.sha256') snapshot_sha,json_extract(s.manifest_text,'$.documents.snapshot.byteLength') snapshot_bytes,json_extract(s.metadata,'$.snapshot.schemaVersion') snapshot_version,json_extract(s.metadata,'$.snapshot.fingerprintVersion') fingerprint_version,json_extract(s.metadata,'$.snapshot.provenance.financialDatasetRoot') financial_root,json_extract(s.metadata,'$.report.provenance.synthetic') synthetic,(SELECT json_extract(c.value,'$.rowCount') FROM json_each(s.manifest_text,'$.collections') c WHERE json_extract(c.value,'$.id')='snapshotRows') row_count ${base} ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?`,
    ).bind(owner, pageSize, offset),
    env.DB.prepare(`SELECT COUNT(*) n ${base}`).bind(owner),
  ]);
  return {
    items: r[0].results.map((x) => {
      const scope = parse(x.scope),
        reasons = [];
      if (
        x.snapshot_version !== 1 ||
        x.fingerprint_version !== 'research_input_v1' ||
        x.financial_root
      )
        reasons.push('SOURCE_NOT_NONFINANCIAL_V1');
      if (!x.snapshot_sha || !x.row_count)
        reasons.push('FROZEN_SNAPSHOT_MISSING');
      if (
        x.snapshot_bytes > LIMITS.sourceSnapshotBytes ||
        x.row_count > LIMITS.marketRows
      )
        reasons.push('DATASET_BUDGET');
      return {
        sourceRef: {
          kind: 'forecast_snapshot_view',
          runId: x.id,
          expectedBundleId: x.bundle_id,
          expectedSnapshotSha256: x.snapshot_sha,
        },
        name: x.name,
        scope: scope
          ? { symbols: scope.symbols, start: scope.start, end: scope.end }
          : null,
        rowCount: x.row_count,
        snapshotBytes: x.snapshot_bytes,
        sourceLabel: x.data_source,
        synthetic: !!x.synthetic,
        createdAt: x.created_at,
        eligibility: {
          status: reasons.length ? 'blocked' : 'eligible',
          reasonCodes: reasons,
          semanticValidation: 'pending',
        },
      };
    }),
    total: r[1].results[0].n,
    page,
    pageSize,
  };
}
async function financialList(env, owner, url) {
  const { page, pageSize, offset } = pageQuery(url),
    base =
      "FROM financial_preparations p JOIN financial_publications b ON b.id=p.publication_id AND b.owner=p.owner AND b.status='committed' JOIN financial_inputs i ON i.id=p.input_id AND i.owner=p.owner WHERE p.owner=?";
  const r = await env.DB.batch([
    env.DB.prepare(
      `SELECT p.id,p.input_id,p.roots,p.metadata,p.created_at,i.name,json_extract(b.manifest_text,'$.summary.input') input ${base} ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?`,
    ).bind(owner, pageSize, offset),
    env.DB.prepare(`SELECT COUNT(*) n ${base}`).bind(owner),
  ]);
  return {
    items: r[0].results.map((x) => ({
      financialRef: {
        inputId: x.input_id,
        preparationId: x.id,
        ...parse(x.roots),
      },
      name: x.name,
      ...parse(x.input),
      hasUsableStates: parse(x.metadata)?.hasUsableStates === true,
      createdAt: x.created_at,
    })),
    total: r[1].results[0].n,
    page,
    pageSize,
  };
}
export async function datasetApi(req, env, path, owner) {
  if (
    !/^\/(datasets|dataset-capabilities|dataset-plans|dataset-preparations)(\/|$)/.test(
      path,
    )
  )
    return null;
  const url = new URL(req.url);
  if (path === '/dataset-capabilities' && req.method === 'GET') {
    const availability = await financialResearchAvailability(env), online = availability.ridge;
    return json({
      enabled: enabled(env),
      profile: PROFILE,
      composition: { ...(await runnerInfo(env)), capability: CAPABILITY },
      forecast: { online, admissionProfile: PROFILE, researchAdmissions: financialResearchAdmissions(researchEnabled(env), availability) },
      datasetFormats: ['atlas.quant.research_dataset/2'],
      financialResultFormats: ['atlas.quant.financial_bundle/1'],
      researchBindingEnabled: researchEnabled(env) && online,
      providerRequired: false,
      limits: LIMITS,
    });
  }
  if (path === '/datasets/sources/markets' && req.method === 'GET') {
    requireEnabled(env);
    return json(await marketList(env, owner, url));
  }
  if (path === '/datasets/sources/financial' && req.method === 'GET') {
    requireEnabled(env);
    return json(await financialList(env, owner, url));
  }
  if (path === '/dataset-plans' && req.method === 'POST')
    return json(await createPlan(env, owner, await body(req, 16384)), 201);
  if (path === '/datasets' && req.method === 'GET') {
    const { page, pageSize, offset } = pageQuery(url),
      r = await env.DB.batch([
        env.DB.prepare(
          'SELECT * FROM quant_research_datasets WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
        ).bind(owner, pageSize, offset),
        env.DB.prepare(
          'SELECT COUNT(*) n FROM quant_research_datasets WHERE owner=?',
        ).bind(owner),
      ]);
    return json({
      items: r[0].results.map((x) => ({
        datasetRef: datasetRef(x),
        name: x.name,
        status: x.status,
        scope: parse(x.scope),
        summary: parse(x.summary),
        createdAt: x.created_at,
      })),
      total: r[1].results[0].n,
      page,
      pageSize,
    });
  }
  let m = /^\/dataset-plans\/([a-f0-9-]+)(?:\/(start))?$/.exec(path);
  if (m) {
    if (!m[2] && req.method === 'GET')
      return json({
        plan: planDTO(await owned(env, 'quant_dataset_plans', owner, m[1])),
      });
    if (m[2] === 'start' && req.method === 'POST')
      return json(
        await startPlan(env, owner, m[1], await body(req, 4096)),
        202,
      );
  }
  m = /^\/dataset-preparations\/([a-f0-9-]+)(?:\/(cancel))?$/.exec(path);
  if (m) {
    if (m[2] === 'cancel' && req.method === 'POST')
      return json(await cancelJob(env, owner, m[1]));
    if (!m[2] && req.method === 'GET') {
      await owned(env, 'quant_dataset_jobs', owner, m[1]);
      await expire(env);
      const r = await env.DB.batch([
          env.DB.prepare(
            'SELECT * FROM quant_dataset_jobs WHERE id=? AND owner=?',
          ).bind(m[1], owner),
          env.DB.prepare(
            'SELECT d.* FROM quant_research_datasets d JOIN quant_dataset_jobs j ON j.dataset_id=d.id AND j.owner=d.owner WHERE j.id=? AND d.owner=?',
          ).bind(m[1], owner),
        ]),
        job = r[0].results[0],
        d = r[1].results[0];
      return json({
        preparation: jobDTO(job),
        activeJob: ['queued', 'running', 'cancel_requested'].includes(
          job.status,
        )
          ? jobDTO(job)
          : null,
        latestJob: jobDTO(job),
        datasetRef: d ? datasetRef(d) : null,
      });
    }
  }
  m = /^\/datasets\/([a-f0-9-]+)(?:\/(manifest|coverage|archive))?$/.exec(path);
  if (m && req.method === 'GET') {
    const d = await owned(env, 'quant_research_datasets', owner, m[1]);
    if (hash(url.searchParams.get('datasetRoot')) !== d.dataset_root)
      fail('ROOT_MISMATCH', '数据集版本不匹配', 409);
    if (m[2] === 'archive') {
      const stage = await env.DB.prepare(
        "SELECT * FROM quant_dataset_stages WHERE id=? AND owner=? AND status='committed'",
      )
        .bind(d.stage_id, owner)
        .first();
      if (!stage) fail('NOT_FOUND', '数据集闭包不存在', 404);
      return datasetArchiveResponse(env, d, stage);
    }
    if (m[2] === 'manifest') {
      const s = await env.DB.prepare(
        "SELECT manifest_text FROM quant_dataset_stages WHERE id=? AND owner=? AND status='committed'",
      )
        .bind(d.stage_id, owner)
        .first();
      if (!s) fail('NOT_FOUND', '数据集清单不存在', 404);
      return new Response(s.manifest_text, {
        encodeBody: 'manual',
        headers: {
          'content-type': 'application/json',
          'content-encoding': 'identity',
          'cache-control': 'no-store, no-transform',
          'x-content-sha256': d.dataset_root,
        },
      });
    }
    if (m[2] === 'coverage') {
      const { page, pageSize, offset } = pageQuery(url),
        r = await env.DB.batch([
          env.DB.prepare(
            'SELECT symbol,metadata FROM quant_dataset_coverage WHERE dataset_id=? ORDER BY ordinal LIMIT ? OFFSET ?',
          ).bind(d.id, pageSize, offset),
          env.DB.prepare(
            'SELECT COUNT(*) n FROM quant_dataset_coverage WHERE dataset_id=?',
          ).bind(d.id),
        ]);
      const value = {
        items: r[0].results.map((x) => ({
          symbol: x.symbol,
          ...parse(x.metadata),
        })),
        total: r[1].results[0].n,
        page,
        pageSize,
        datasetRoot: d.dataset_root,
      };
      if (bytes(value).length > 262144)
        fail('DATASET_PAGE_BUDGET', '覆盖页超过大小限制', 413);
      return json(value);
    }
    const availability = await financialResearchAvailability(env);
    const researchAdmissions = financialResearchAdmissions(researchEnabled(env) && d.status === 'ready', availability, parse(d.scope));
    return json({
      datasetRef: datasetRef(d),
      name: d.name,
      status: d.status,
      scope: parse(d.scope),
      summary: parse(d.summary),
      researchAdmission: {
        profile: PROFILE,
        configurationEligible: researchEnabled(env) && d.status === 'ready',
        sampleStatus: 'not_checked',
      },
      researchAdmissions,
      preferredResearchAdmission: researchAdmissions.find(x => x.estimator === 'auto' && x.configurationEligible && x.runnerAvailable) || null,
      researchBindingEnabled: researchEnabled(env) && d.status === 'ready',
      archiveUrl: `/quant/api/datasets/${d.id}/archive?datasetRoot=${d.dataset_root}`,
      sourceEvidenceClosure: 'separate_research_dataset_v2',
    });
  }
  fail('NOT_FOUND', '数据集接口不存在', 404);
}
