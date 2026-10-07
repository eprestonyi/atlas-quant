import { ApiError } from '../errors.mjs';
import { body, json } from '../runtime.mjs';
import { BUNDLE_PROFILE } from './profile.mjs';
import { readBoundedText } from './json.mjs';
import {
  beginBundle,
  leasedJob,
  parsedStage,
  readChunk,
  sourceStage,
  terminalDiscard,
  uploadChunk
} from './storage.mjs';
import { finalizeBundle } from './verify.mjs';

/** Called only after the shared runner bearer has been authenticated. */
export async function bundleRunnerApi(req, env, path) {
  if (path === '/runner/bundles/begin' && req.method === 'POST') {
    return json(await beginBundle(env, await body(req, BUNDLE_PROFILE.manifestBytes * 3)));
  }
  if (path === '/runner/bundles/finalize' && req.method === 'POST') {
    return json(await finalizeBundle(env, await body(req, 2000)));
  }
  const match =
    /^\/runner\/bundles\/([a-f0-9]{64})\/chunks\/([a-zA-Z]+)\/(0|[1-9][0-9]{0,3})$/.exec(path);
  if (match && ['GET', 'PUT'].includes(req.method)) {
    const [, bundleId, collection, number] = match;
    const input = {
      id: req.headers.get('X-Quant-Job'),
      leaseToken: req.headers.get('X-Quant-Lease'),
      stageId: req.headers.get('X-Quant-Stage'),
      bundleId
    };
    if (req.method === 'PUT')
      return json(
        await uploadChunk(
          env,
          input,
          collection,
          +number,
          await readBoundedText(req, BUNDLE_PROFILE.chunkBytes)
        )
      );
    const job = await leasedJob(env, input);
    if (job.status !== 'running') return json(terminalDiscard(job));
    const stage = await sourceStage(env, job);
    if (stage.bundle_id !== bundleId)
      throw new ApiError('BUNDLE_SOURCE_UNAVAILABLE', '只能读取本次执行绑定的原始分片', 404);
    const parsed = await parsedStage(stage),
      descriptor = parsed.collections.get(collection)?.chunks[+number];
    if (!descriptor) throw new ApiError('NOT_FOUND', '来源分片不存在', 404);
    const raw = await readChunk(env, stage, collection, descriptor);
    return new Response(raw, {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
        'x-content-sha256': descriptor.sha256
      }
    });
  }
  if (path === '/runner/replay' && req.method === 'POST') {
    const input = await body(req.clone(), 4000);
    if (input.kind !== 'bundle') return null;
    const job = await leasedJob(env, input);
    if (job.status !== 'running') return json(terminalDiscard(job));
    const stage = await sourceStage(env, job);
    await parsedStage(stage);
    return json({
      bundleId: stage.bundle_id,
      manifestText: stage.manifest_text
    });
  }
  return null;
}
