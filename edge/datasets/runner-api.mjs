import { LEGACY_DATASET } from './context.mjs';
import {
  beginPublication,
  publicationStatus,
  putPart,
  completePublication
} from './publication.mjs';
import { body } from '../runtime.mjs';
import { json, object, LIMITS, fail, parse } from './common.mjs';
import { claim, heartbeat, leased, failJob } from './jobs.mjs';
import { inputEnvelope, registryPage, registryResponse, sourceResponse } from './transport.mjs';
/** Numerical publication handlers are integrated only with the locked v2 codec. */
export async function datasetRunnerApi(req, env, path, context = LEGACY_DATASET) {
  if (!path.startsWith(context.runnerBase + '/')) return null;
  const route = path.slice(context.runnerBase.length),
    url = new URL(req.url);
  if (route === '/claim' && req.method === 'POST')
    return json(await claim(env, await body(req, 4096), context));
  if (route === '/heartbeat' && req.method === 'POST')
    return json(await heartbeat(env, await body(req, 4096), context));
  let publicationRoute =
    /^\/jobs\/([a-f0-9-]+)\/(publication|complete)(?:\/([a-f0-9-]+)\/parts\/([a-z][A-Za-z0-9]{0,39})\/(\d+))?$/.exec(
      route
    );
  if (publicationRoute) {
    const [, jobId, action, publicationId, componentId, ordinal] = publicationRoute;
    if (action === 'complete' && !publicationId && req.method === 'POST')
      return json(await completePublication(env, jobId, await body(req, 4096), context));
    if (action === 'publication' && !publicationId && req.method === 'POST')
      return json(
        await beginPublication(env, jobId, await body(req, LIMITS.manifestBytes * 7), context)
      );
    if (action === 'publication' && !publicationId && req.method === 'GET')
      return json(
        await publicationStatus(
          env,
          jobId,
          req.headers.get('X-Dataset-Lease'),
          url.searchParams.get('datasetRoot'),
          context
        )
      );
    if (action === 'publication' && publicationId && req.method === 'PUT')
      return json(
        await putPart(
          env,
          req,
          jobId,
          publicationId,
          componentId,
          Number(ordinal),
          url.searchParams.get('datasetRoot'),
          context
        )
      );
    fail('METHOD', '发布接口方法不支持', 405);
  }
  const m =
    /^\/jobs\/([a-f0-9-]+)\/(input|registry|sources|status|fail)(?:\/([a-zA-Z0-9-]+))?(?:\/(manifest|parts))?(?:\/(\d+))?$/.exec(
      route
    );
  if (!m) fail('NOT_FOUND', '数据集任务接口不存在', 404);
  const [, jobId, action, ref, operation, ordinal] = m;
  if (action === 'fail' && req.method === 'POST') {
    const value = object(await body(req, 4096), ['leaseToken', 'error']),
      job = await leased(env, jobId, value.leaseToken, {
        terminal: true,
        cancel: true,
        context
      });
    return json(await failJob(env, job, value.error));
  }
  if (req.method !== 'GET') fail('METHOD', '接口需要GET', 405);
  const job = await leased(env, jobId, req.headers.get('X-Dataset-Lease'), {
    terminal: action === 'status',
    cancel: action === 'status',
    context
  });
  if (action === 'status') {
    const d = job.dataset_id
      ? await env.DB.prepare(
          'SELECT id,dataset_root FROM quant_research_datasets WHERE id=? AND owner=?'
        )
          .bind(job.dataset_id, job.owner)
          .first()
      : null;
    return json({
      job: { id: job.id, status: job.status, error: parse(job.error) },
      datasetRef: d
        ? {
            datasetId: d.id,
            datasetRoot: d.dataset_root,
            format: 'atlas.quant.research_dataset',
            version: context.version
          }
        : null
    });
  }
  if (action === 'input' && !ref) return json(await inputEnvelope(env, job, context));
  if (action === 'registry')
    return ref
      ? registryResponse(env, job, ref, context)
      : json(await registryPage(env, job, url, context));
  if (action === 'sources')
    return sourceResponse(
      env,
      job,
      ref,
      operation,
      ordinal === undefined ? null : Number(ordinal),
      context
    );
  fail('NOT_FOUND', '来源接口不存在', 404);
}
