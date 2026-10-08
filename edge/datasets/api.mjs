import { datasetArchiveResponse } from './archive.mjs';
import {
  researchEnabled,
  supportsFinancialDatasets,
  supportsFinancialResearchProfile,
  FINANCIAL_AUTO_PROFILE
} from './research.mjs';
import {
  financialResearchAdmissions,
  financialGraphResearchAdmissions,
  FINANCIAL_GRAPH_PROFILE
} from './research-profile.mjs';
import { LEGACY_DATASET, GRAPH_DATASET, assertContext, assertPlanContext } from './context.mjs';
import { parseStrictJson } from '../bundles/json.mjs';
/** Owner-scoped control plane; result bytes remain private, bounded R2 parts. */
import { body } from '../runtime.mjs';
import { pageQuery } from '../financial/common.mjs';
import {
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
  hashBytes
} from './common.mjs';
import { createPlan, startPlan, planDTO } from './plans.mjs';
import { expire, cancelJob } from './jobs.mjs';
async function financialResearchAvailability(env) {
  const runner = await env.DB.prepare(
    "SELECT value,updated_at FROM meta WHERE key='runner'"
  ).first();
  const fresh = !!runner && Date.now() - Date.parse(runner.updated_at) < 120000;
  const capability = parse(runner?.value, {});
  return {
    ridge: fresh && supportsFinancialDatasets(capability),
    auto: fresh && supportsFinancialResearchProfile(capability, FINANCIAL_AUTO_PROFILE),
    graph: fresh && supportsFinancialResearchProfile(capability, FINANCIAL_GRAPH_PROFILE)
  };
}

function researchAdmissions(env, context, availability, scope, ready = true) {
  const allowed = researchEnabled(env, context.version) && ready;
  return context === GRAPH_DATASET
    ? financialGraphResearchAdmissions(allowed, availability.graph, scope)
    : financialResearchAdmissions(allowed, availability, scope);
}

/** A stored ID is insufficient: both immutable plan tags and manifest identity
 * must belong to the server-selected route. Ownership is joined at every link. */
async function datasetStage(env, dataset, owner, context) {
  const stage = await env.DB.prepare(
    "SELECT s.*,p.spec plan_spec FROM quant_dataset_stages s JOIN quant_dataset_jobs j ON j.id=s.job_id AND j.owner=s.owner JOIN quant_dataset_plans p ON p.id=j.plan_id AND p.owner=j.owner WHERE s.id=? AND s.owner=? AND s.status='committed'"
  )
    .bind(dataset.stage_id, owner)
    .first();
  if (!stage) fail('NOT_FOUND', '数据集闭包不存在', 404);
  assertPlanContext(parse(stage.plan_spec), context);
  if (
    stage.dataset_id !== dataset.id ||
    stage.dataset_root !== dataset.dataset_root ||
    typeof stage.manifest_text !== 'string' ||
    bytes(stage.manifest_text).length > context.protocol.limits.manifestBytes ||
    (await hashBytes(bytes(stage.manifest_text))) !== dataset.dataset_root
  )
    fail('DATASET_INTEGRITY', '数据集清单与冻结版本不匹配', 409);
  const manifest = parseStrictJson(stage.manifest_text);
  if (
    manifest.format !== context.protocol.datasetFormat ||
    manifest.version !== context.version ||
    manifest.profile !== context.profile
  )
    fail('DATASET_PROFILE', '数据集清单不属于该版本入口', 409);
  return stage;
}

async function marketList(env, owner, url, context) {
  const LIMITS = context.protocol.limits;
  const { page, pageSize, offset } = pageQuery(url);
  const base = `FROM quant_bundle_runs b JOIN quant_bundle_stages s ON s.id=b.stage_id AND s.owner=b.owner AND s.status='committed' JOIN jobs j ON j.id=b.job_id AND j.owner=b.owner AND j.status='completed' JOIN quant_runs q ON q.job_id=j.id AND q.owner=j.owner AND q.kind='forecast' WHERE b.owner=?`;
  const r = await env.DB.batch([
    env.DB.prepare(
      `SELECT j.id,j.name,j.created_at,j.data_source,json_extract(j.spec,'$.universe') scope,s.bundle_id,json_extract(s.manifest_text,'$.documents.snapshot.sha256') snapshot_sha,json_extract(s.manifest_text,'$.documents.snapshot.byteLength') snapshot_bytes,json_extract(s.metadata,'$.snapshot.schemaVersion') snapshot_version,json_extract(s.metadata,'$.snapshot.fingerprintVersion') fingerprint_version,json_extract(s.metadata,'$.snapshot.provenance.financialDatasetRoot') financial_root,json_extract(s.metadata,'$.report.provenance.synthetic') synthetic,(SELECT json_extract(c.value,'$.rowCount') FROM json_each(s.manifest_text,'$.collections') c WHERE json_extract(c.value,'$.id')='snapshotRows') row_count ${base} ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?`
    ).bind(owner, pageSize, offset),
    env.DB.prepare(`SELECT COUNT(*) n ${base}`).bind(owner)
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
      if (!x.snapshot_sha || !x.row_count) reasons.push('FROZEN_SNAPSHOT_MISSING');
      if (Array.isArray(scope?.symbols) && scope.symbols.length > LIMITS.symbols)
        reasons.push('SOURCE_UNIVERSE_EXCEEDS_PROFILE');
      if (x.snapshot_bytes > LIMITS.sourceSnapshotBytes || x.row_count > LIMITS.marketRows)
        reasons.push('DATASET_BUDGET');
      return {
        sourceRef: {
          kind: 'forecast_snapshot_view',
          runId: x.id,
          expectedBundleId: x.bundle_id,
          expectedSnapshotSha256: x.snapshot_sha
        },
        name: x.name,
        scope: scope ? { symbols: scope.symbols, start: scope.start, end: scope.end } : null,
        rowCount: x.row_count,
        snapshotBytes: x.snapshot_bytes,
        sourceLabel: x.data_source,
        synthetic: !!x.synthetic,
        createdAt: x.created_at,
        eligibility: {
          status: reasons.length ? 'blocked' : 'eligible',
          reasonCodes: reasons,
          semanticValidation: 'pending'
        }
      };
    }),
    total: r[1].results[0].n,
    page,
    pageSize
  };
}
async function financialList(env, owner, url) {
  const { page, pageSize, offset } = pageQuery(url),
    base =
      "FROM financial_preparations p JOIN financial_publications b ON b.id=p.publication_id AND b.owner=p.owner AND b.status='committed' JOIN financial_inputs i ON i.id=p.input_id AND i.owner=p.owner WHERE p.owner=?";
  const r = await env.DB.batch([
    env.DB.prepare(
      `SELECT p.id,p.input_id,p.roots,p.metadata,p.created_at,i.name,json_extract(b.manifest_text,'$.summary.input') input ${base} ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?`
    ).bind(owner, pageSize, offset),
    env.DB.prepare(`SELECT COUNT(*) n ${base}`).bind(owner)
  ]);
  return {
    items: r[0].results.map((x) => {
      const roots = parse(x.roots, {});
      return {
        ...parse(x.input),
        financialRef: {
          inputId: x.input_id,
          preparationId: x.id,
          inputRoot: roots.inputRoot,
          packRoot: roots.packRoot,
          preparedRoot: roots.preparedRoot,
          calendarRoot: roots.calendarRoot
        },
        name: x.name,
        hasUsableStates: parse(x.metadata)?.hasUsableStates === true,
        createdAt: x.created_at
      };
    }),
    total: r[1].results[0].n,
    page,
    pageSize
  };
}
export async function datasetApi(req, env, path, owner, context = LEGACY_DATASET) {
  assertContext(context);
  const graph = context === GRAPH_DATASET;
  if (
    !(
      graph
        ? /^\/dataset-graphs(\/|$)/
        : /^\/(datasets|dataset-capabilities|dataset-plans|dataset-preparations)(\/|$)/
    ).test(path)
  )
    return null;
  const base = context.publicBase,
    capabilitiesPath = graph ? base + '/capabilities' : '/dataset-capabilities',
    plansPath = graph ? base + '/plans' : '/dataset-plans',
    jobsPath = graph ? base + '/jobs' : '/dataset-preparations';
  const url = new URL(req.url);
  if (path === capabilitiesPath && req.method === 'GET') {
    const availability = await financialResearchAvailability(env),
      online = graph ? availability.graph : availability.ridge;
    return json({
      enabled: enabled(env, context),
      profile: context.profile,
      composition: {
        ...(await runnerInfo(env, context)),
        capability: context.capability
      },
      forecast: {
        online,
        admissionProfile: graph ? FINANCIAL_GRAPH_PROFILE : context.profile,
        researchAdmissions: researchAdmissions(env, context, availability)
      },
      datasetFormats: [`${context.protocol.datasetFormat}/${context.version}`],
      financialResultFormats: [context.protocol.resultTransport],
      researchBindingEnabled: researchEnabled(env, context.version) && online,
      providerRequired: false,
      limits: context.protocol.limits
    });
  }
  if (path === base + '/sources/markets' && req.method === 'GET') {
    requireEnabled(env, context);
    return json(await marketList(env, owner, url, context));
  }
  if (path === base + '/sources/financial' && req.method === 'GET') {
    requireEnabled(env, context);
    return json(await financialList(env, owner, url));
  }
  if (path === plansPath && req.method === 'POST')
    return json(await createPlan(env, owner, await body(req, 16384), context), 201);
  if (path === base && req.method === 'GET') {
    const { page, pageSize, offset } = pageQuery(url),
      from =
        "FROM quant_research_datasets d JOIN quant_dataset_stages s ON s.id=d.stage_id AND s.owner=d.owner AND s.dataset_id=d.id AND s.dataset_root=d.dataset_root AND s.status='committed' JOIN quant_dataset_jobs j ON j.id=s.job_id AND j.owner=d.owner JOIN quant_dataset_plans p ON p.id=j.plan_id AND p.owner=d.owner WHERE d.owner=? AND json_extract(p.spec,'$.profile')=? AND json_extract(p.spec,'$.request.profile')=? AND json_extract(s.manifest_text,'$.format')=? AND json_type(s.manifest_text,'$.version')='integer' AND json_extract(s.manifest_text,'$.version')=? AND json_extract(s.manifest_text,'$.profile')=?",
      binding = [
        owner,
        context.profile,
        context.profile,
        context.protocol.datasetFormat,
        context.version,
        context.profile
      ],
      r = await env.DB.batch([
        env.DB.prepare(
          `SELECT d.* ${from} ORDER BY d.created_at DESC,d.id DESC LIMIT ? OFFSET ?`
        ).bind(...binding, pageSize, offset),
        env.DB.prepare(`SELECT COUNT(*) n ${from}`).bind(...binding)
      ]);
    return json({
      items: r[0].results.map((x) => ({
        datasetRef: datasetRef(x, context),
        name: x.name,
        status: x.status,
        scope: parse(x.scope),
        summary: parse(x.summary),
        createdAt: x.created_at
      })),
      total: r[1].results[0].n,
      page,
      pageSize
    });
  }
  let m = new RegExp('^' + plansPath + '/([a-f0-9-]+)(?:/(start))?$').exec(path);
  if (m) {
    if (!m[2] && req.method === 'GET')
      return json({
        plan: planDTO(await owned(env, 'quant_dataset_plans', owner, m[1]), context)
      });
    if (m[2] === 'start' && req.method === 'POST')
      return json(await startPlan(env, owner, m[1], await body(req, 4096), context), 202);
  }
  m = new RegExp('^' + jobsPath + '/([a-f0-9-]+)(?:/(cancel))?$').exec(path);
  if (m) {
    if (m[2] === 'cancel' && req.method === 'POST')
      return json(await cancelJob(env, owner, m[1], context));
    if (!m[2] && req.method === 'GET') {
      const existing = await owned(env, 'quant_dataset_jobs', owner, m[1]);
      const plan = await owned(env, 'quant_dataset_plans', owner, existing.plan_id);
      assertPlanContext(parse(plan.spec), context);
      await expire(env);
      const r = await env.DB.batch([
          env.DB.prepare('SELECT * FROM quant_dataset_jobs WHERE id=? AND owner=?').bind(
            m[1],
            owner
          ),
          env.DB.prepare(
            'SELECT d.* FROM quant_research_datasets d JOIN quant_dataset_jobs j ON j.dataset_id=d.id AND j.owner=d.owner WHERE j.id=? AND d.owner=?'
          ).bind(m[1], owner)
        ]),
        job = r[0].results[0],
        d = r[1].results[0];
      if (d) {
        const stage = await datasetStage(env, d, owner, context);
        if (stage.job_id !== job.id) fail('DATASET_INTEGRITY', '任务数据集不匹配', 409);
      }
      return json({
        preparation: jobDTO(job),
        activeJob: ['queued', 'running', 'cancel_requested'].includes(job.status)
          ? jobDTO(job)
          : null,
        latestJob: jobDTO(job),
        datasetRef: d ? datasetRef(d, context) : null
      });
    }
  }
  m = new RegExp(
    '^' + base + '/([a-f0-9-]+)(?:/(manifest|coverage|' + (graph ? 'download' : 'archive') + '))?$'
  ).exec(path);
  if (m && req.method === 'GET') {
    const d = await owned(env, 'quant_research_datasets', owner, m[1]);
    if (hash(url.searchParams.get('datasetRoot')) !== d.dataset_root)
      fail('ROOT_MISMATCH', '数据集版本不匹配', 409);
    const stage = await datasetStage(env, d, owner, context);
    if (m[2] === (graph ? 'download' : 'archive')) {
      return datasetArchiveResponse(env, d, stage);
    }
    if (m[2] === 'manifest') {
      return new Response(stage.manifest_text, {
        encodeBody: 'manual',
        headers: {
          'content-type': 'application/json',
          'content-encoding': 'identity',
          'cache-control': 'no-store, no-transform',
          'x-content-sha256': d.dataset_root,
          'content-length': String(bytes(stage.manifest_text).length)
        }
      });
    }
    if (m[2] === 'coverage') {
      const { page, pageSize, offset } = pageQuery(url),
        r = await env.DB.batch([
          env.DB.prepare(
            'SELECT symbol,metadata FROM quant_dataset_coverage WHERE dataset_id=? ORDER BY ordinal LIMIT ? OFFSET ?'
          ).bind(d.id, pageSize, offset),
          env.DB.prepare('SELECT COUNT(*) n FROM quant_dataset_coverage WHERE dataset_id=?').bind(
            d.id
          )
        ]);
      const value = {
        items: r[0].results.map((x) => ({
          symbol: x.symbol,
          ...parse(x.metadata)
        })),
        total: r[1].results[0].n,
        page,
        pageSize,
        datasetRoot: d.dataset_root
      };
      if (bytes(value).length > 262144) fail('DATASET_PAGE_BUDGET', '覆盖页超过大小限制', 413);
      return json(value);
    }
    const availability = await financialResearchAvailability(env);
    const admissions = researchAdmissions(
      env,
      context,
      availability,
      parse(d.scope),
      d.status === 'ready'
    );
    return json({
      datasetRef: datasetRef(d, context),
      name: d.name,
      status: d.status,
      scope: parse(d.scope),
      summary: parse(d.summary),
      researchAdmission: {
        profile: graph ? FINANCIAL_GRAPH_PROFILE : context.profile,
        configurationEligible: researchEnabled(env, context.version) && d.status === 'ready',
        sampleStatus: 'not_checked'
      },
      researchAdmissions: admissions,
      preferredResearchAdmission:
        admissions.find(
          (x) => x.estimator === 'auto' && x.configurationEligible && x.runnerAvailable
        ) || null,
      researchBindingEnabled: researchEnabled(env, context.version) && d.status === 'ready',
      archiveUrl: `/quant/api${base}/${d.id}/${graph ? 'download' : 'archive'}?datasetRoot=${d.dataset_root}`,
      sourceEvidenceClosure: `separate_research_dataset_v${context.version}`
    });
  }
  fail('NOT_FOUND', '数据集接口不存在', 404);
}
