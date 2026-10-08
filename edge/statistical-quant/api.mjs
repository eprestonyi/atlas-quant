import { ownedForecastStage, streamDocumentResponse } from '../bundles/user-api.mjs';
import { ownedStageForRun, parsedStage } from '../bundles/storage.mjs';
import { ApiError } from '../errors.mjs';
import { NOW, random, json, parse, body, jobItem, audit } from '../runtime.mjs';
import {
  validateStatisticalQuant,
  validateStoredStatisticalQuant,
  validateExecution,
  validatePortfolio,
  validateCosts,
  keys
} from './validation.mjs';
import {
  experimentView,
  forecastView,
  ownedExperiment,
  ownedForecast,
  forecastResponse,
  readPrivateObject,
  notFound
} from './persistence.mjs';

const BASE = '/statistical-quant';
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const pagination = (req) => {
  const p = new URL(req.url).searchParams,
    page = Number(p.get('page') ?? 1),
    pageSize = Number(p.get('pageSize') ?? 30);
  if (
    !Number.isInteger(page) ||
    page < 1 ||
    page > 10000 ||
    !Number.isInteger(pageSize) ||
    pageSize < 1 ||
    pageSize > 100
  )
    throw new ApiError('INVALID_PAGE', '页码须≥1，每页1–100项');
  return { page, pageSize, offset: (page - 1) * pageSize };
};

export async function createExperiment(env, owner, strategy, parentId = null) {
  const count = await env.DB.prepare(
    'SELECT count(*) n FROM quant_experiments WHERE owner=? AND archived=0'
  )
    .bind(owner)
    .first();
  if (count.n >= 200) throw new ApiError('LIMIT', '最多保存200个当前研究');
  const id = random(),
    time = NOW(),
    spec = JSON.stringify(strategy);
  await env.DB.batch([
    env.DB.prepare(
      'INSERT INTO quant_experiments(id,owner,name,spec,parent_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)'
    ).bind(id, owner, strategy.name, spec, parentId, time, time),
    env.DB.prepare(
      'INSERT INTO quant_experiment_versions(experiment_id,version,spec,created_at) VALUES(?,1,?,?)'
    ).bind(id, spec, time)
  ]);
  return experimentView(await ownedExperiment(env, owner, id));
}

async function experimentDetail(env, owner, id) {
  const row = await ownedExperiment(env, owner, id);
  const [runs, forecasts, executions] = await Promise.all([
    env.DB.prepare(
      'SELECT j.*,q.kind,q.experiment_version,q.forecast_artifact_id,q.source_forecast_id FROM jobs j JOIN quant_runs q ON q.job_id=j.id WHERE q.owner=? AND q.experiment_id=? ORDER BY j.created_at DESC LIMIT 100'
    )
      .bind(owner, id)
      .all(),
    env.DB.prepare(
      'SELECT f.* FROM quant_forecast_artifacts f WHERE f.owner=? AND EXISTS(SELECT 1 FROM quant_runs q WHERE q.owner=f.owner AND q.experiment_id=? AND q.forecast_artifact_id=f.id) ORDER BY f.created_at DESC LIMIT 100'
    )
      .bind(owner, id)
      .all(),
    env.DB.prepare(
      'SELECT e.*,j.status,j.summary,j.error FROM quant_executions e JOIN jobs j ON j.id=e.id WHERE e.owner=? AND e.experiment_id=? ORDER BY e.created_at DESC LIMIT 100'
    )
      .bind(owner, id)
      .all()
  ]);
  return {
    experiment: experimentView(row),
    runs: runs.results.map((r) => ({
      ...jobItem(r),
      jobKind: r.kind,
      experimentVersion: r.experiment_version,
      forecastArtifactId: r.forecast_artifact_id ?? r.source_forecast_id
    })),
    forecasts: forecasts.results.map((f) => ({
      ...forecastView(f),
      experimentId: id,
      sourceExperimentId: f.experiment_id
    })),
    executions: executions.results.map(executionView)
  };
}
const executionView = (r) => ({
  id: r.id,
  experimentId: r.experiment_id,
  forecastArtifactId: r.forecast_artifact_id,
  configuration: parse(r.spec),
  status: r.status,
  summary: parse(r.summary),
  error: parse(r.error),
  createdAt: r.created_at
});

async function createComparison(env, owner, input) {
  keys(input, ['name', 'kind', 'members'], '比较');
  if (
    !['forecast', 'execution'].includes(input.kind) ||
    !Array.isArray(input.members) ||
    input.members.length < 2 ||
    input.members.length > 8 ||
    new Set(input.members).size !== input.members.length ||
    input.members.some((x) => typeof x !== 'string' || x.length > 100)
  )
    throw new ApiError('INVALID_COMPARISON', '比较需要2–8个不同的同类研究产物');
  const name = input.name ?? '研究比较';
  if (typeof name !== 'string' || !name.trim() || name.length > 80)
    throw new ApiError('INVALID_COMPARISON', '比较名称无效');
  const items = [];
  for (const id of input.members) {
    if (input.kind === 'forecast') {
      const f = await ownedForecast(env, owner, id);
      const metadata = forecastView(f).metadata;
      items.push({
        id,
        forecastArtifactId: id,
        dataFingerprint: f.data_fingerprint,
        target: metadata.target,
        universe: metadata.universe,
        diagnostics: metadata.diagnostics,
        model: metadata.model
      });
    } else {
      const r = await env.DB.prepare(
        'SELECT e.*,j.status,j.summary,j.result_key FROM quant_executions e JOIN jobs j ON j.id=e.id WHERE e.id=? AND e.owner=?'
      )
        .bind(id, owner)
        .first();
      if (!r) throw notFound();
      if (r.status !== 'completed')
        throw new ApiError('COMPARISON_NOT_READY', '只可比较已经完成的执行', 409);
      items.push({
        id,
        forecastArtifactId: r.forecast_artifact_id,
        configuration: parse(r.spec),
        metrics: parse(r.summary)
      });
    }
  }
  const reference = items[0];
  const compatibility =
    input.kind === 'execution'
      ? {
          sameForecastArtifact: items.every(
            (x) => x.forecastArtifactId === reference.forecastArtifactId
          ),
          refittingPerformed: false
        }
      : {
          sameDataSnapshot: items.every((x) => x.dataFingerprint === reference.dataFingerprint),
          sameTarget: items.every((x) => same(x.target, reference.target)),
          sameUniverseAndWindow: items.every((x) => same(x.universe, reference.universe))
        };
  const controlledComparison =
    input.kind === 'execution'
      ? compatibility.sameForecastArtifact
      : Object.values(compatibility).every(Boolean);
  const metadata = {
    items,
    compatibility,
    controlledComparison,
    independentlyValidatedAlpha: false
  };
  const id = random(),
    time = NOW();
  await env.DB.prepare(
    'INSERT INTO quant_comparisons(id,owner,name,kind,members,metadata,created_at) VALUES(?,?,?,?,?,?,?)'
  )
    .bind(
      id,
      owner,
      name,
      input.kind,
      JSON.stringify(input.members),
      JSON.stringify(metadata),
      time
    )
    .run();
  return {
    id,
    name,
    kind: input.kind,
    members: input.members,
    ...metadata,
    createdAt: time
  };
}

/** All routes are behind a server-derived workspace owner and same-origin gate. */
export async function statisticalQuantPrivate(req, env, path, owner, { enqueue }) {
  if (!path.startsWith(BASE + '/')) return null;
  const segments = path
    .slice(BASE.length + 1)
    .split('/')
    .map(decodeURIComponent);
  const [resource, id, action] = segments;
  if (resource === 'summary' && !id && req.method === 'GET') {
    const counts = await env.DB.batch([
      env.DB.prepare('SELECT count(*) n FROM quant_experiments WHERE owner=? AND archived=0').bind(
        owner
      ),
      env.DB.prepare('SELECT count(*) n FROM quant_forecast_artifacts WHERE owner=?').bind(owner),
      env.DB.prepare(
        "SELECT count(*) n FROM quant_executions e JOIN jobs j ON j.id=e.id WHERE e.owner=? AND j.status='completed'"
      ).bind(owner),
      env.DB.prepare('SELECT count(*) n FROM quant_comparisons WHERE owner=?').bind(owner)
    ]);
    return json({
      experiments: counts[0].results[0].n,
      forecastArtifacts: counts[1].results[0].n,
      completedExecutions: counts[2].results[0].n,
      comparisons: counts[3].results[0].n,
      scope: 'current_workspace',
      independentlyValidatedAlpha: 0
    });
  }
  if (resource === 'experiments') {
    if (!id && req.method === 'GET') {
      const p = pagination(req),
        count = await env.DB.prepare(
          'SELECT count(*) n FROM quant_experiments WHERE owner=? AND archived=0'
        )
          .bind(owner)
          .first();
      const rows = await env.DB.prepare(
        `SELECT e.*,
          (SELECT json_object(
            'id',j.id,'status',j.status,'jobKind',q.kind,
            'experimentVersion',q.experiment_version,
            'createdAt',j.created_at,'updatedAt',j.updated_at
          ) FROM quant_runs q JOIN jobs j ON j.id=q.job_id
          WHERE q.owner=e.owner AND j.owner=e.owner AND q.experiment_id=e.id
          ORDER BY j.created_at DESC,j.id DESC LIMIT 1) AS latest_run
         FROM quant_experiments e WHERE e.owner=? AND e.archived=0
         ORDER BY e.updated_at DESC,e.id DESC LIMIT ? OFFSET ?`
      )
        .bind(owner, p.pageSize, p.offset)
        .all();
      return json({
        items: rows.results.map((row) => ({
          ...experimentView(row),
          latestRun: parse(row.latest_run)
        })),
        total: count.n,
        page: p.page,
        pageSize: p.pageSize
      });
    }
    if (!id && req.method === 'POST') {
      const input = await body(req, 200000);
      keys(input, ['strategy'], '研究请求');
      return json(
        {
          experiment: await createExperiment(env, owner, validateStatisticalQuant(input.strategy))
        },
        201
      );
    }
    if (!id) return null;
    const row = await ownedExperiment(env, owner, id);
    if (action === 'run' && req.method === 'POST') {
      const input = await body(req, 26 * 1024 * 1024);
      keys(input, ['version', 'dataSource', 'dataset'], '运行请求');
      if (input.version !== row.version)
        throw new ApiError('REVISION_CONFLICT', '研究已修改，请重新加载后运行', 409);
      if (row.archived) throw new ApiError('ARCHIVED_RESEARCH', '已归档研究需复制后运行', 409);
      const job = await enqueue(
        env,
        owner,
        {
          strategy: validateStoredStatisticalQuant(parse(row.spec)),
          dataSource: input.dataSource,
          ...(input.dataset === undefined ? {} : { dataset: input.dataset })
        },
        false,
        { experimentId: id, experimentVersion: row.version, kind: 'forecast' }
      );
      return json({ experiment: experimentView(row), job }, 202);
    }
    if (action === 'copy' && req.method === 'POST') {
      const input = await body(req, 1000);
      keys(input, ['name'], '复制请求');
      const strategy = validateStoredStatisticalQuant(parse(row.spec));
      strategy.name = input.name ?? strategy.name.slice(0, 70) + ' · 副本';
      return json(
        {
          experiment: await createExperiment(env, owner, validateStatisticalQuant(strategy), id)
        },
        201
      );
    }
    if (action === 'export' && req.method === 'GET')
      return json(await experimentDetail(env, owner, id), 200, {
        'content-disposition': `attachment; filename="atlas-research-${id}.json"`
      });
    if (!action && req.method === 'GET') return json(await experimentDetail(env, owner, id));
    if (!action && req.method === 'PUT') {
      const input = await body(req, 200000);
      keys(input, ['strategy', 'version'], '保存请求');
      const s = validateStatisticalQuant(input.strategy),
        time = NOW(),
        spec = JSON.stringify(s);
      if (input.version !== row.version)
        throw new ApiError('REVISION_CONFLICT', '研究已被其他窗口修改', 409);
      // D1 batch is transactional: the mutable head and immutable revision
      // either advance together, or neither change. changes() refers to the
      // immediately preceding CAS, so a losing writer cannot add a revision.
      const operations = await env.DB.batch([
        env.DB.prepare(
          'UPDATE quant_experiments SET spec=?,name=?,version=version+1,updated_at=? WHERE id=? AND owner=? AND version=? RETURNING *'
        ).bind(spec, s.name, time, id, owner, input.version),
        env.DB.prepare(
          'INSERT INTO quant_experiment_versions(experiment_id,version,spec,created_at) SELECT id,version,spec,updated_at FROM quant_experiments WHERE id=? AND owner=? AND changes()=1'
        ).bind(id, owner)
      ]);
      const updated = operations[0].results[0];
      if (!updated) throw new ApiError('REVISION_CONFLICT', '研究版本冲突', 409);
      return json({ experiment: experimentView(updated) });
    }
    if (!action && req.method === 'DELETE') {
      await env.DB.prepare(
        'UPDATE quant_experiments SET archived=1,updated_at=? WHERE id=? AND owner=?'
      )
        .bind(NOW(), id, owner)
        .run();
      return json({ ok: true, archived: true, historyPreserved: true });
    }
  }
  if (resource === 'forecasts' && !id && req.method === 'GET') {
    const p = pagination(req),
      count = await env.DB.prepare('SELECT count(*) n FROM quant_forecast_artifacts WHERE owner=?')
        .bind(owner)
        .first();
    const rows = await env.DB.prepare(
      'SELECT * FROM quant_forecast_artifacts WHERE owner=? ORDER BY created_at DESC LIMIT ? OFFSET ?'
    )
      .bind(owner, p.pageSize, p.offset)
      .all();
    return json({
      items: rows.results.map(forecastView),
      total: count.n,
      page: p.page,
      pageSize: p.pageSize
    });
  }
  if (resource === 'forecasts' && id && req.method === 'GET' && (!action || action === 'download'))
    return forecastResponse(req, env, owner, id, action === 'download');
  if (resource === 'executions') {
    if (!id && req.method === 'GET') {
      const p = pagination(req),
        count = await env.DB.prepare('SELECT count(*) n FROM quant_executions WHERE owner=?')
          .bind(owner)
          .first();
      const rows = await env.DB.prepare(
        'SELECT e.*,j.status,j.summary,j.error FROM quant_executions e JOIN jobs j ON j.id=e.id WHERE e.owner=? ORDER BY e.created_at DESC LIMIT ? OFFSET ?'
      )
        .bind(owner, p.pageSize, p.offset)
        .all();
      return json({
        items: rows.results.map(executionView),
        total: count.n,
        page: p.page,
        pageSize: p.pageSize
      });
    }
    if (!id && req.method === 'POST') {
      const input = await body(req, 12000);
      keys(input, ['forecastArtifactId', 'execution', 'portfolio', 'costs'], '执行复用');
      const source = await ownedForecast(env, owner, String(input.forecastArtifactId ?? ''));
      const bundleStage = await ownedForecastStage(env, owner, source.id);
      const artifact = bundleStage
        ? (await parsedStage(bundleStage)).metadata.forecast
        : await readPrivateObject(env, source.artifact_key, source.artifact_hash);
      if (input.execution !== undefined)
        keys(
          input.execution,
          ['enabled', 'side', 'shorting', 'minEdgeBps', 'maxPositions'],
          '执行覆盖'
        );
      if (input.portfolio !== undefined)
        keys(
          input.portfolio,
          [
            'initialCapital',
            'grossExposure',
            'maxWeight',
            'rebalanceDays',
            'rebalanceThresholdBps',
            'netExposureLimit',
            'sizingMode',
            'targetAnnualVolatility',
            'volatilityLookback',
            'factorExposureLimits'
          ],
          '组合覆盖'
        );
      if (input.costs !== undefined)
        keys(
          input.costs,
          [
            'commissionBps',
            'slippageBps',
            'sellTaxBps',
            'transferBps',
            'minCommission',
            'borrowAnnualBps'
          ],
          '成本覆盖'
        );
      const strategy = {
        ...artifact.sourceStrategy,
        execution: validateExecution({
          ...artifact.sourceStrategy.execution,
          ...input.execution,
          enabled: true
        }),
        portfolio: validatePortfolio({
          ...artifact.sourceStrategy.portfolio,
          ...input.portfolio
        }),
        costs: validateCosts({
          ...artifact.sourceStrategy.costs,
          ...input.costs
        })
      };
      validateStatisticalQuant(strategy);
      const experiment = await ownedExperiment(env, owner, source.experiment_id);
      const job = await enqueue(env, owner, { strategy, dataSource: 'replay' }, true, {
        experimentId: experiment.id,
        experimentVersion: experiment.version,
        kind: 'execution',
        sourceForecastId: source.id
      });
      return json(
        {
          execution: {
            id: job.id,
            experimentId: experiment.id,
            forecastArtifactId: source.id,
            configuration: {
              execution: strategy.execution,
              portfolio: strategy.portfolio,
              costs: strategy.costs
            },
            status: job.status
          },
          job
        },
        202
      );
    }
    if (id && req.method === 'GET' && (!action || action === 'download')) {
      const row = await env.DB.prepare(
        'SELECT e.*,j.status,j.summary,j.error,j.result_key FROM quant_executions e JOIN jobs j ON j.id=e.id WHERE e.id=? AND e.owner=?'
      )
        .bind(id, owner)
        .first();
      if (!row) throw notFound();
      const bundleStage = await ownedStageForRun(env, owner, id);
      if (bundleStage) {
        const parsed = await parsedStage(bundleStage);
        return streamDocumentResponse(env, bundleStage, parsed, 'report', {
          prefix: JSON.stringify({ execution: executionView(row) }).slice(0, -1) + ',\"result\":',
          suffix: '}',
          ...(action === 'download' ? { filename: `atlas-execution-${id}.json` } : {})
        });
      }
      const result = row.result_key ? await readPrivateObject(env, row.result_key) : null;
      return json(
        { execution: executionView(row), result },
        200,
        action === 'download'
          ? {
              'content-disposition': `attachment; filename="atlas-execution-${id}.json"`
            }
          : {}
      );
    }
  }
  if (resource === 'model-versions' && req.method === 'GET') {
    const view = (r) => ({
      id: r.id,
      experimentId: r.experiment_id,
      forecastArtifactId: r.artifact_id,
      family: r.family,
      configHash: r.config_hash,
      metadata: parse(r.metadata),
      createdAt: r.created_at
    });
    if (id) {
      const row = await env.DB.prepare('SELECT * FROM quant_model_versions WHERE id=? AND owner=?')
        .bind(id, owner)
        .first();
      if (!row) throw notFound();
      return json({ modelVersion: view(row) });
    }
    const rows = await env.DB.prepare(
      'SELECT * FROM quant_model_versions WHERE owner=? ORDER BY created_at DESC LIMIT 100'
    )
      .bind(owner)
      .all();
    return json({ items: rows.results.map(view) });
  }
  if (resource === 'comparisons') {
    if (!id && req.method === 'GET') {
      const p = pagination(req),
        count = await env.DB.prepare('SELECT count(*) n FROM quant_comparisons WHERE owner=?')
          .bind(owner)
          .first();
      const rows = await env.DB.prepare(
        'SELECT * FROM quant_comparisons WHERE owner=? ORDER BY created_at DESC LIMIT ? OFFSET ?'
      )
        .bind(owner, p.pageSize, p.offset)
        .all();
      return json({
        items: rows.results.map((r) => ({
          id: r.id,
          name: r.name,
          kind: r.kind,
          members: parse(r.members),
          createdAt: r.created_at
        })),
        total: count.n,
        page: p.page,
        pageSize: p.pageSize
      });
    }
    if (!id && req.method === 'POST')
      return json(
        {
          comparison: await createComparison(env, owner, await body(req, 12000))
        },
        201
      );
    if (id && req.method === 'GET') {
      const row = await env.DB.prepare('SELECT * FROM quant_comparisons WHERE id=? AND owner=?')
        .bind(id, owner)
        .first();
      if (!row) throw notFound();
      return json(
        {
          comparison: {
            id: row.id,
            name: row.name,
            kind: row.kind,
            members: parse(row.members),
            ...parse(row.metadata),
            createdAt: row.created_at
          }
        },
        200,
        action === 'download'
          ? {
              'content-disposition': `attachment; filename="atlas-comparison-${id}.json"`
            }
          : {}
      );
    }
  }
  return null;
}
