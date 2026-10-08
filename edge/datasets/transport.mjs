import { LEGACY_DATASET, assertPlanContext } from './context.mjs';
/** Small descriptors first; private source reads are pinned to the leased plan. */
import {
  LIMITS,
  PROFILE,
  protocol,
  owned,
  parse,
  fail,
  bytes,
  hashBytes,
  json,
  readObject,
  integer,
  id,
  requireEnabled
} from './common.mjs';
import { registryBytes } from '../financial/registry.mjs';
export function protectedResponse(raw, sha256) {
  return new Response(raw, {
    encodeBody: 'manual',
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'content-encoding': 'identity',
      'cache-control': 'no-store, no-transform',
      'content-length': String(raw.byteLength),
      'x-content-sha256': sha256
    }
  });
}
export async function taskPlan(env, job, context = LEGACY_DATASET) {
  const row = await owned(env, 'quant_dataset_plans', job.owner, job.plan_id);
  const spec = parse(row.spec);
  assertPlanContext(spec, context);
  return { row, spec };
}
export async function assertRegistry(env, owner, descriptors) {
  // One bounded JSON binding avoids O(number of grants) round trips on each part.
  // Each occurrence must match the same owner's current immutable descriptor.
  const result = await env.DB.prepare(
    `SELECT COUNT(*) n FROM json_each(?) d
    JOIN financial_registry_entries r ON r.id=json_extract(d.value,'$.ref')
    AND r.owner=? AND r.status='active'
    AND r.kind=json_extract(d.value,'$.kind')
    AND r.sha256=json_extract(d.value,'$.sha256')
    AND r.byte_length=json_extract(d.value,'$.byteLength')`
  )
    .bind(JSON.stringify(descriptors), owner)
    .first();
  if (result.n !== descriptors.length)
    fail('DATASET_REGISTRY_REVOKED', '组成所需的精确授权已失效或改变', 409);
}
export async function inputEnvelope(env, job, context = LEGACY_DATASET) {
  requireEnabled(env, context);
  const { row, spec } = await taskPlan(env, job, context);
  await assertRegistry(env, job.owner, spec.sources.registry);
  const base = `/quant/api${context.runnerBase}/jobs/${job.id}`,
    market = spec.sources.market,
    sources = {
      market: {
        runId: market.runId,
        bundleId: market.bundleId,
        originalScope: market.originalScope,
        manifest: {
          ...market.manifest,
          url: base + '/sources/market/manifest'
        },
        snapshot: {
          ...market.snapshot,
          parts: market.snapshot.parts.map((p) => ({
            ...p,
            url: base + '/sources/market/parts/' + p.ordinal
          }))
        }
      },
      financial: spec.sources.financial.map((f) => ({
        sourceId: f.sourceId,
        inputId: f.inputId,
        preparationId: f.preparationId,
        roots: f.roots,
        calendarRef: f.calendarRef,
        proofRefs: f.proofRefs,
        package: {
          ...f.package,
          parts: f.package.parts.map((p) => ({
            ...p,
            url: base + '/sources/' + f.sourceId + '/parts/' + p.ordinal
          }))
        }
      }))
    };
  const value = {
    job: {
      id: job.id,
      kind: context.jobKind,
      planId: job.plan_id,
      deadline: job.deadline
    },
    planRoot: row.plan_root,
    plan: {
      profile: context.profile,
      marketSource: spec.request.marketSource,
      financialInputs: spec.request.financialInputs,
      marketCalendarRef: spec.sources.marketCalendarRef,
      scope: market.scope
    },
    sources,
    registry: {
      count: spec.sources.registry.length,
      totalBytes: spec.sources.registry.reduce((n, r) => n + r.byteLength, 0),
      listUrl: base + '/registry'
    },
    limits: LIMITS
  };
  if (bytes(value).length > LIMITS.inputMetadataBytes)
    fail('DATASET_BUDGET', '组成任务描述超过256KiB', 413);
  return value;
}
export async function registryPage(env, job, url, context = LEGACY_DATASET) {
  requireEnabled(env, context);
  const { spec } = await taskPlan(env, job, context);
  const offset = Number(url.searchParams.get('offset') || 0);
  integer(offset, 0, spec.sources.registry.length);
  for (const k of url.searchParams.keys())
    if (k !== 'offset') fail('UNKNOWN_PROPERTY', '不支持的证据分页参数');
  const items = spec.sources.registry.slice(offset, offset + 64);
  await assertRegistry(env, job.owner, items);
  return {
    items: items.map((d) => ({
      ...d,
      url: `/quant/api${context.runnerBase}/jobs/${job.id}/registry/${d.ref}`
    })),
    total: spec.sources.registry.length,
    offset,
    nextOffset: offset + items.length < spec.sources.registry.length ? offset + items.length : null
  };
}
export async function registryResponse(env, job, ref, context = LEGACY_DATASET) {
  requireEnabled(env, context);
  id(ref);
  const { spec } = await taskPlan(env, job, context),
    d = spec.sources.registry.find((x) => x.ref === ref);
  if (!d) fail('NOT_FOUND', '证据不属于该任务', 404);
  const row = await env.DB.prepare(
    "SELECT * FROM financial_registry_entries WHERE id=? AND owner=? AND status='active'"
  )
    .bind(ref, job.owner)
    .first();
  if (!row || row.sha256 !== d.sha256 || row.byte_length !== d.byteLength || row.kind !== d.kind)
    fail('DATASET_REGISTRY_REVOKED', '任务证据授权已改变', 409);
  return protectedResponse(await registryBytes(env, row), d.sha256);
}
export async function sourceResponse(
  env,
  job,
  sourceId,
  action,
  ordinal,
  context = LEGACY_DATASET
) {
  requireEnabled(env, context);
  const { spec } = await taskPlan(env, job, context);
  if (context.version === 3) await assertRegistry(env, job.owner, spec.sources.registry);
  if (sourceId === 'market') {
    const m = spec.sources.market,
      stage = await env.DB.prepare(
        "SELECT * FROM quant_bundle_stages WHERE id=? AND owner=? AND bundle_id=? AND status='committed'"
      )
        .bind(m.stageId, job.owner, m.bundleId)
        .first();
    if (!stage) fail('DATASET_SOURCE_CHANGED', '冻结市场来源不可读取', 409);
    if (action === 'manifest') {
      const raw = bytes(stage.manifest_text);
      if (raw.length !== m.manifest.byteLength || (await hashBytes(raw)) !== m.manifest.sha256)
        fail('DATASET_SOURCE_INTEGRITY', '冻结清单发生变化', 409);
      return protectedResponse(raw, m.manifest.sha256);
    }
    integer(ordinal, 0, m.snapshot.parts.length - 1);
    const d = m.snapshot.parts[ordinal],
      r = await env.DB.prepare(
        "SELECT * FROM quant_bundle_chunks WHERE stage_id=? AND collection='snapshotRows' AND ordinal=?"
      )
        .bind(m.stageId, ordinal)
        .first();
    if (!r || r.sha256 !== d.sha256 || r.byte_length !== d.byteLength)
      fail('DATASET_SOURCE_INTEGRITY', '冻结行情分片不匹配', 409);
    return protectedResponse(
      await readObject(env, r.object_key, d.sha256, d.byteLength, LIMITS.sourceChunkBytes),
      d.sha256
    );
  }
  const f = spec.sources.financial.find((x) => x.sourceId === sourceId);
  if (!f || action !== 'parts') fail('NOT_FOUND', '财务来源不属于该任务', 404);
  integer(ordinal, 0, f.package.parts.length - 1);
  const d = f.package.parts[ordinal],
    r = await env.DB.prepare(
      "SELECT c.* FROM financial_chunks c JOIN financial_publications p ON p.id=c.publication_id AND p.owner=? AND p.status='committed' WHERE p.id=? AND c.collection='package' AND c.ordinal=?"
    )
      .bind(job.owner, f.publicationId, ordinal)
      .first();
  if (!r || r.sha256 !== d.sha256 || r.byte_length !== d.byteLength)
    fail('DATASET_SOURCE_INTEGRITY', '冻结财务包分片不匹配', 409);
  return protectedResponse(
    await readObject(env, r.object_key, d.sha256, d.byteLength, LIMITS.partBytes),
    d.sha256
  );
}
