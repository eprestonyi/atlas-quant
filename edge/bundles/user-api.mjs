import { ApiError } from '../errors.mjs';
import { json, jobItem } from '../runtime.mjs';
import { ownedStageForRun, parsedStage, readChunk } from './storage.mjs';
import { documentStream } from './streams.mjs';
import { reportSummary } from './publication.mjs';
import { pageQuery, pageRecords, detailRecord, chartRecords } from './pages.mjs';
import { readPrivateObject } from '../private-objects.mjs';
import { bundleArchiveResponse } from './archive.mjs';

export function transportView(stage, parsed, runId) {
  return {
    format: parsed.manifest.format,
    version: parsed.manifest.version,
    ...(parsed.manifest.sourceEvidence
      ? {
          sourceEvidence: parsed.manifest.sourceEvidence,
          sourceEvidenceClosure:
            'separate_research_dataset_v' + parsed.manifest.sourceEvidence.datasetRef.version,
          executionEligible: false
        }
      : {}),
    bundleId: stage.bundle_id,
    complete: true,
    logicalArtifactId: parsed.manifest.forecastArtifactId,
    hasFrozenInputs: Object.hasOwn(parsed.manifest.documents, 'snapshot'),
    collections: Object.fromEntries(
      [...parsed.collections.values()]
        .filter((c) => !['snapshotRows', 'snapshotColumns', 'snapshotContextSources'].includes(c.id))
        .map((c) => [c.id, { total: c.rowCount }])
    ),
    downloadUrl: `/quant/api/runs/${runId}/report/download?bundleId=${stage.bundle_id}`,
    bundleDownloadUrl: `/quant/api/runs/${runId}/report/bundle?bundleId=${stage.bundle_id}`
  };
}
export function streamDocumentResponse(env, stage, parsed, name, options = {}) {
  return new Response(
    documentStream(
      parsed,
      name,
      (collection, descriptor) => readChunk(env, stage, collection, descriptor),
      options
    ),
    {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
        ...(options.filename
          ? {
              'content-disposition': `attachment; filename="${options.filename}"`
            }
          : {})
      }
    }
  );
}
export async function ownedForecastStage(env, owner, forecastId) {
  return env.DB.prepare(
    `SELECT s.* FROM quant_bundle_forecasts b JOIN quant_bundle_stages s ON s.id=b.stage_id
    WHERE b.owner=? AND b.forecast_id=? AND s.owner=b.owner AND s.status='committed'`
  )
    .bind(owner, forecastId)
    .first();
}

export async function bundleUserApi(req, env, path, owner) {
  const match =
    /^\/runs\/([^/]+)(?:\/(report)(?:\/(pages|detail|chart|download|bundle))?|\/(export))?$/.exec(
      path
    );
  if (!match || req.method !== 'GET') return null;
  const [, id, reportRoute, operation, oldExport] = match;
  const job = await env.DB.prepare('SELECT * FROM jobs WHERE id=? AND owner=?')
    .bind(id, owner)
    .first();
  if (!job) throw new ApiError('NOT_FOUND', '实验不存在', 404);
  const stage = await ownedStageForRun(env, owner, id);
  if (!stage) {
    if (!reportRoute) return null;
    if (operation && operation !== 'download')
      throw new ApiError('LEGACY_REPORT', '历史单包报告通过完整报告入口读取', 409);
    const result = job.result_key ? await readPrivateObject(env, job.result_key) : null;
    return json(
      { job: jobItem(job), report: result, transport: null },
      200,
      operation === 'download'
        ? {
            'content-disposition': `attachment; filename="atlas-quant-${id}.json"`
          }
        : {}
    );
  }
  const params = new URL(req.url).searchParams;
  if (operation && params.get('bundleId') !== stage.bundle_id)
    throw new ApiError('BUNDLE_VERSION_CHANGED', '请重新读取报告摘要后再请求同版本页面', 409);
  const parsed = await parsedStage(stage);
  if (!reportRoute)
    return streamDocumentResponse(env, stage, parsed, 'report', {
      prefix: JSON.stringify({ job: jobItem(job) }).slice(0, -1) + ',"result":',
      suffix: '}',
      ...(oldExport ? { filename: `atlas-quant-${id}.json` } : {})
    });
  if (!operation)
    return json({
      job: jobItem(job),
      report: reportSummary(parsed),
      transport: transportView(stage, parsed, id)
    });
  if (operation === 'pages')
    return json(await pageRecords(env, stage, parsed, pageQuery(params, parsed)));
  if (operation === 'detail') return json(await detailRecord(env, stage, parsed, params));
  if (operation === 'chart') return json(await chartRecords(env, stage, parsed, params));
  if (operation === 'bundle') return bundleArchiveResponse(env, stage, parsed, id);
  return streamDocumentResponse(env, stage, parsed, 'report', {
    filename: `atlas-quant-${id}.json`
  });
}
