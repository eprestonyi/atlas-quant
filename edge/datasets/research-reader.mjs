/** Bounded provider-free source delivery to the exact live research lease. */
import { assertRunDataset } from './research.mjs';
import { datasetContext } from './context.mjs';
import { LIMITS, bytes, hashBytes, fail, id, integer, json, readObject } from './common.mjs';
import { registryPage, registryResponse, protectedResponse } from './transport.mjs';
export async function researchDatasetApi(req, env, path) {
  if (!path.startsWith('/runner/research-datasets/')) return null;
  const m =
    /^\/runner\/research-datasets\/([a-f0-9-]+)\/(input|manifest|parts|registry)(?:\/([a-zA-Z0-9-]+))?(?:\/(\d+))?$/.exec(
      path
    );
  if (!m) fail('NOT_FOUND', '研究数据集读取接口不存在', 404);
  if (req.method !== 'GET') fail('METHOD', '冻结数据集只允许GET', 405);
  const [, jobId, action, ref, ordinal] = m,
    url = new URL(req.url),
    token = req.headers.get('X-Dataset-Lease');
  id(jobId);
  id(token);
  const job = await env.DB.prepare(
    "SELECT * FROM jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>?"
  )
    .bind(jobId, token, new Date().toISOString())
    .first();
  if (!job) fail('STALE_LEASE', '研究租约已停止或不匹配', 409);
  const closure = await assertRunDataset(env, job),
    root = closure.datasetRef.datasetRoot,
    base = `/quant/api/runner/research-datasets/${job.id}`;
  for (const key of url.searchParams.keys())
    if (key !== 'datasetRoot' && !(key === 'offset' && action === 'registry' && !ref))
      fail('UNKNOWN_PROPERTY', '读取参数不支持');
  if (action !== 'input' && url.searchParams.get('datasetRoot') !== root)
    fail('DATASET_ROOT', '读取须固定同一数据集根', 409);
  const composition = await env.DB.prepare(
    'SELECT * FROM quant_dataset_jobs WHERE id=? AND owner=?'
  )
    .bind(closure.stage.job_id, job.owner)
    .first();
  if (!composition) fail('DATASET_SOURCE_INTEGRITY', '缺少数据集组成来源', 409);
  if (action === 'input' && !ref)
    return json({
      job: { id: job.id, kind: 'forecast' },
      datasetRef: closure.datasetRef,
      admissionProfile: closure.admissionProfile,
      sourceEvidence: closure.sourceEvidence,
      manifest: {
        sha256: root,
        byteLength: bytes(closure.manifestText).length,
        url: `${base}/manifest?datasetRoot=${root}`
      },
      partUrlTemplate: `${base}/parts/{componentId}/{ordinal}?datasetRoot=${root}`,
      registry: {
        count: closure.plan.sources.registry.length,
        totalBytes: closure.plan.sources.registry.reduce((n, x) => n + x.byteLength, 0),
        listUrl: `${base}/registry?datasetRoot=${root}&offset=0`
      },
      limits: LIMITS
    });
  if (action === 'manifest' && !ref) return protectedResponse(bytes(closure.manifestText), root);
  if (action === 'parts' && ref && ordinal !== undefined) {
    const component = closure.manifest.components.find((x) => x.componentId === ref);
    if (!component) fail('NOT_FOUND', '该组件不属于数据集', 404);
    const n = Number(ordinal);
    integer(n, 0, component.parts.length - 1);
    const descriptor = component.parts[n],
      stored = await env.DB.prepare(
        'SELECT * FROM quant_dataset_parts WHERE stage_id=? AND component_id=? AND ordinal=?'
      )
        .bind(closure.stage.id, ref, n)
        .first();
    if (
      !stored ||
      stored.sha256 !== descriptor.sha256 ||
      stored.byte_length !== descriptor.byteLength
    )
      fail('DATASET_SOURCE_INTEGRITY', '数据集分片索引不匹配', 409);
    return protectedResponse(
      await readObject(
        env,
        stored.object_key,
        descriptor.sha256,
        descriptor.byteLength,
        LIMITS.partBytes
      ),
      descriptor.sha256
    );
  }
  if (action === 'registry' && ref)
    return registryResponse(env, composition, ref, datasetContext(closure.datasetRef.version));
  if (action === 'registry' && !ref) {
    const clean = new URL(url);
    clean.searchParams.delete('datasetRoot');
    const page = await registryPage(
      env,
      composition,
      clean,
      datasetContext(closure.datasetRef.version)
    );
    page.items = page.items.map((x) => ({
      ...x,
      url: `${base}/registry/${x.ref}?datasetRoot=${root}`
    }));
    return json(page);
  }
  fail('NOT_FOUND', '冻结数据集读取接口不存在', 404);
}
