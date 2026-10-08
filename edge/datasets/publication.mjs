/** Immutable, resumable publication. No joined 64MiB JSON is parsed by the Worker. */
import { parseStrictJson } from '../bundles/json.mjs';
import {
  object,
  id,
  hash,
  fail,
  bytes,
  hashBytes,
  readBytes,
  LIMITS,
  NOW,
  random,
  parse,
  json,
  canonical,
  requireEnabled,
  datasetRef,
} from './common.mjs';
import { leased } from './jobs.mjs';
import { taskPlan, assertRegistry } from './transport.mjs';
import { validateDatasetManifest } from './manifest.mjs';
import { readObject } from './common.mjs';
const hex = (x) =>
  [...new Uint8Array(x)].map((n) => n.toString(16).padStart(2, '0')).join('');
async function stageFor(env, job, root) {
  hash(root);
  const stage = await env.DB.prepare(
    'SELECT * FROM quant_dataset_stages WHERE job_id=? AND owner=? AND lease_token=? AND dataset_root=?',
  )
    .bind(job.id, job.owner, job.lease_token, root)
    .first();
  if (!stage) fail('DATASET_PUBLICATION_CONFLICT', '组成产物身份不匹配', 409);
  return stage;
}
async function receipt(env, stage, parsed) {
  const rows = await env.DB.prepare(
      'SELECT component_id,ordinal,sha256,byte_length FROM quant_dataset_parts WHERE stage_id=?',
    )
      .bind(stage.id)
      .all(),
    map = new Map(
      rows.results.map((r) => [r.component_id + '/' + r.ordinal, r]),
    );
  return {
    publicationId: stage.id,
    datasetRoot: stage.dataset_root,
    status: stage.status,
    missing: [...parsed.components.values()]
      .map((c) => ({
        componentId: c.componentId,
        ordinals: c.parts
          .filter((p) => {
            const r = map.get(c.componentId + '/' + p.ordinal);
            return (
              !r || r.sha256 !== p.sha256 || r.byte_length !== p.byteLength
            );
          })
          .map((p) => p.ordinal),
      }))
      .filter((x) => x.ordinals.length),
  };
}
export async function beginPublication(env, jobId, value) {
  object(value, ['leaseToken', 'datasetRoot', 'manifestText']);
  const job = await leased(env, jobId, value.leaseToken, { terminal: true }),
    { spec } = await taskPlan(env, job),
    parsed = await validateDatasetManifest(
      value.manifestText,
      value.datasetRoot,
      spec,
    );
  const old = await env.DB.prepare(
    'SELECT * FROM quant_dataset_stages WHERE job_id=? AND owner=? AND lease_token=?',
  )
    .bind(job.id, job.owner, job.lease_token)
    .first();
  if (old) {
    if (
      old.dataset_root !== value.datasetRoot ||
      old.manifest_text !== value.manifestText
    )
      fail('DATASET_PUBLICATION_CONFLICT', '同一任务不能替换冻结产物', 409);
    if (job.status === 'completed' && old.status === 'committed')
      return receipt(env, old, parsed);
  }
  await leased(env, jobId, value.leaseToken);
  requireEnabled(env);
  await assertRegistry(env, job.owner, spec.sources.registry);
  const time = NOW(),
    stageId = random(),
    datasetId = random();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,created_at,updated_at)
    SELECT ?,?,?,?,?,?,?,?,'staging',?,? WHERE EXISTS(SELECT 1 FROM quant_dataset_jobs WHERE id=? AND owner=? AND lease_token=? AND status='running' AND lease_until>? AND deadline>?)`,
  )
    .bind(
      stageId,
      job.id,
      job.owner,
      job.lease_token,
      datasetId,
      value.datasetRoot,
      value.manifestText,
      parsed.totalBytes,
      time,
      time,
      job.id,
      job.owner,
      job.lease_token,
      time,
      time,
    )
    .run();
  const stage = await stageFor(env, job, value.datasetRoot);
  if (stage.manifest_text !== value.manifestText)
    fail('DATASET_PUBLICATION_CONFLICT', '产物并发版本不同', 409);
  return receipt(env, stage, parsed);
}
export async function publicationStatus(env, jobId, token, root) {
  const job = await leased(env, jobId, token, { terminal: true }),
    stage = await stageFor(env, job, root),
    { spec } = await taskPlan(env, job);
  if (stage.status !== 'committed') {
    requireEnabled(env);
    await assertRegistry(env, job.owner, spec.sources.registry);
  }
  return receipt(
    env,
    stage,
    await validateDatasetManifest(stage.manifest_text, root, spec),
  );
}
export async function putPart(
  env,
  req,
  jobId,
  publicationId,
  componentId,
  ordinal,
  root,
) {
  const job = await leased(env, jobId, req.headers.get('X-Dataset-Lease'));
  requireEnabled(env);
  const stage = await stageFor(env, job, root),
    { spec } = await taskPlan(env, job);
  await assertRegistry(env, job.owner, spec.sources.registry);
  if (stage.id !== id(publicationId) || stage.status !== 'staging')
    fail('DATASET_PUBLICATION_CONFLICT', '产物不能新增分片', 409);
  const parsed = await validateDatasetManifest(stage.manifest_text, root, spec),
    component = parsed.components.get(componentId),
    descriptor = component?.parts[ordinal];
  if (!descriptor || descriptor.ordinal !== ordinal)
    fail('DATASET_PART', '分片不属于该清单');
  const raw = await readBytes(req, LIMITS.partBytes);
  if (
    raw.length !== descriptor.byteLength ||
    (await hashBytes(raw)) !== descriptor.sha256
  )
    fail('DATASET_PART', '原始分片与声明哈希或长度不一致');
  const key = `research-datasets/${job.owner}/${stage.id}/${componentId}/${ordinal}-${descriptor.sha256}`;
  // A same-key concurrent writer has identical verified bytes. Never delete a
  // winning immutable object after a lost D1 CAS; abandoned staging is retained.
  await env.ARTIFACTS.put(key, raw, {
    httpMetadata: { contentType: 'application/octet-stream' },
  });
  const now = NOW();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_dataset_parts(stage_id,component_id,ordinal,sha256,byte_length,object_key)
    SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_dataset_stages s JOIN quant_dataset_jobs j ON j.id=s.job_id AND j.owner=s.owner WHERE s.id=? AND s.status='staging' AND j.status='running' AND j.lease_token=? AND j.lease_until>? AND j.deadline>?)`,
  )
    .bind(
      stage.id,
      componentId,
      ordinal,
      descriptor.sha256,
      descriptor.byteLength,
      key,
      stage.id,
      job.lease_token,
      now,
      now,
    )
    .run();
  const actual = await env.DB.prepare(
    'SELECT * FROM quant_dataset_parts WHERE stage_id=? AND component_id=? AND ordinal=?',
  )
    .bind(stage.id, componentId, ordinal)
    .first();
  if (
    !actual ||
    actual.sha256 !== descriptor.sha256 ||
    actual.byte_length !== descriptor.byteLength
  )
    fail('STALE_LEASE', '任务已停止，分片未提交', 409);
  return {
    ok: true,
    componentId,
    ordinal,
    sha256: descriptor.sha256,
    byteLength: descriptor.byteLength,
  };
}
export async function* componentPieces(env, stage, component, partRows) {
  const digest = new crypto.DigestStream('SHA-256'),
    writer = digest.getWriter();
  let length = 0,
    closed = false;
  try {
    for (const d of component.parts) {
      const row = partRows.get(component.componentId + '/' + d.ordinal);
      if (!row || row.sha256 !== d.sha256 || row.byte_length !== d.byteLength)
        fail('DATASET_INCOMPLETE', '组成分片不完整', 409);
      const raw = await readObject(
        env,
        row.object_key,
        d.sha256,
        d.byteLength,
        LIMITS.partBytes,
      );
      length += raw.length;
      await writer.write(raw);
      yield raw;
    }
    await writer.close();
    closed = true;
    if (
      length !== component.byteLength ||
      hex(await digest.digest) !== component.payloadSha256
    )
      fail('DATASET_INTEGRITY', '组件完整哈希不匹配', 409);
  } finally {
    if (!closed) {
      digest.digest.catch(() => {});
      try {
        await writer.abort();
      } catch {}
    }
    writer.releaseLock();
  }
}
/** Parse only one bounded registry wrapper at a time, retaining raw JSON inside
 * rawText as a string. Exact SHA validates it against the authorized source. */
async function verifyRegistryPieces(pieces, expected) {
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '',
    prefix = false,
    separator = false,
    index = 0,
    depth = 0,
    inString = false,
    escaped = false,
    pos = 0,
    start = -1;
  for await (const raw of pieces) {
    buffer += decoder.decode(raw, { stream: true });
    if (!prefix) {
      if (buffer.length < 12) continue;
      if (!buffer.startsWith('{"entries":['))
        fail('DATASET_REGISTRY', '证据组件结构无效');
      buffer = buffer.slice(12);
      prefix = true;
    }
    for (; pos < buffer.length; pos++) {
      const c = buffer[pos];
      if (start < 0) {
        if (c === '{' && !separator) {
          start = pos;
          depth = 1;
          continue;
        }
        if (c === ',' && separator) {
          separator = false;
          continue;
        }
        if (c === ']' && (separator || index === 0)) break;
        fail('DATASET_REGISTRY', '证据组件排列无效');
      }
      if (inString) {
        if (escaped) escaped = false;
        else if (c === '\\') escaped = true;
        else if (c === '"') inString = false;
      } else if (c === '"') inString = true;
      else if (c === '{') depth++;
      else if (c === '}') depth--;
      if (depth === 0) {
        const entry = parseStrictJson(buffer.slice(start, pos + 1));
        object(entry, ['byteLength', 'rawText', 'ref', 'sha256']);
        const d = expected[index++];
        if (
          !d ||
          entry.ref !== d.ref ||
          entry.sha256 !== d.sha256 ||
          entry.byteLength !== d.byteLength ||
          bytes(entry.rawText).length !== d.byteLength ||
          (await hashBytes(bytes(entry.rawText))) !== d.sha256
        )
          fail('DATASET_REGISTRY', '闭包证据与精确授权原字节不一致');
        separator = true;
        buffer = buffer.slice(pos + 1);
        pos = -1;
        start = -1;
      }
    }
    if (buffer.length > LIMITS.registryEntryBytes * 7)
      fail('DATASET_BUDGET', '单条证据编码超限', 413);
  }
  buffer += decoder.decode();
  if (!prefix || index !== expected.length || buffer !== ']}')
    fail('DATASET_REGISTRY', '证据闭包缺项或尾部无效');
}
function coverageIndex(value, spec) {
  object(value, ['marketRows', 'observedSymbols', 'financial']);
  if (
    !Number.isInteger(value.marketRows) ||
    value.marketRows < 1 ||
    value.marketRows > LIMITS.marketRows ||
    canonical(value.observedSymbols) !==
      canonical(spec.sources.market.scope.symbols) ||
    !Array.isArray(value.financial) ||
    value.financial.length !== spec.sources.financial.length
  )
    fail('DATASET_COVERAGE', '市场覆盖范围或数量不完整');
  const entries = [],
    quality = new Set();
  value.financial.forEach((f, n) => {
    const expected = spec.sources.financial[n];
    for (const k of ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot'])
      if (f[k] !== expected.roots[k])
        fail('DATASET_COVERAGE', '覆盖证据根与来源不一致');
    if (
      f.originalAsPublishedVerified !== false ||
      f.revisionTimeVerified !== false ||
      f.completeHistoricalVersionsVerified !== false
    )
      fail('DATASET_COVERAGE', '不得提升历史披露证据');
    if (
      !Array.isArray(f.securities) ||
      canonical(f.securities.map((x) => x.symbol).sort()) !==
        canonical([...expected.selection.symbols].sort())
    )
      fail('DATASET_COVERAGE', '财务覆盖股票不完整');
    for (const flag of f.qualityFlags || []) quality.add(flag);
    for (const security of f.securities) {
      if (
        !Number.isInteger(security.rows) ||
        security.rows < 1 ||
        security.rows > LIMITS.marketRows ||
        !Array.isArray(security.states) ||
        canonical(security.states.map((x) => x.stateId).sort()) !==
          canonical([...expected.selection.selectedStateIds].sort())
      )
        fail('DATASET_COVERAGE', '财务状态覆盖不完整');
      for (const state of security.states) {
        if (
          !Number.isInteger(state.okRows) ||
          !Number.isInteger(state.missingRows) ||
          state.okRows < 0 ||
          state.missingRows < 0 ||
          state.okRows + state.missingRows !== security.rows ||
          state.status !== (state.okRows > 0 ? 'available' : 'missing') ||
          bytes(state).length > 32768
        )
          fail('DATASET_COVERAGE', '状态计数、可用性或索引大小无效');
        entries.push({
          symbol: security.symbol,
          stateId: state.stateId,
          status: state.status,
          metadata: {
            ...state,
            rows: security.rows,
            inputId: expected.inputId,
            preparationId: expected.preparationId,
            unitPolicy: expected.unitPolicy,
          },
        });
      }
    }
  });
  return {
    entries,
    summary: {
      marketRows: value.marketRows,
      symbols: value.observedSymbols.length,
      financialInputs: value.financial.length,
      selectedStateIds: [...new Set(entries.map((x) => x.stateId))].sort(),
      stateCoverage: entries.length,
      availableStateCoverage: entries.filter((x) => x.status === 'available')
        .length,
      qualityFlags: [...quality].sort(),
      originalAsPublishedVerified: false,
      revisionTimeVerified: false,
      completeHistoricalVersionsVerified: false,
      modelTrainingValidated: false,
    },
  };
}
export async function completePublication(env, jobId, value) {
  object(value, ['leaseToken', 'publicationId', 'datasetRoot']);
  const job = await leased(env, jobId, value.leaseToken, { terminal: true }),
    stage = await stageFor(env, job, value.datasetRoot);
  if (stage.id !== id(value.publicationId))
    fail('DATASET_PUBLICATION_CONFLICT', '完成回执身份不一致', 409);
  if (job.status === 'completed' && stage.status === 'committed')
    return {
      ok: true,
      status: 'completed',
      datasetRef: datasetRef({
        id: stage.dataset_id,
        dataset_root: stage.dataset_root,
      }),
      idempotent: true,
    };
  await leased(env, jobId, value.leaseToken);
  requireEnabled(env);
  const { spec, row: plan } = await taskPlan(env, job);
  await assertRegistry(env, job.owner, spec.sources.registry);
  const parsed = await validateDatasetManifest(
      stage.manifest_text,
      stage.dataset_root,
      spec,
    ),
    rows = await env.DB.prepare(
      'SELECT * FROM quant_dataset_parts WHERE stage_id=?',
    )
      .bind(stage.id)
      .all(),
    parts = new Map(
      rows.results.map((x) => [x.component_id + '/' + x.ordinal, x]),
    );
  if (rows.results.length !== parsed.partCount)
    fail('DATASET_INCOMPLETE', '组成分片数量不完整', 409);
  let coverage;
  for (const c of parsed.components.values()) {
    const pieces = componentPieces(env, stage, c, parts);
    if (c.componentId === 'registryEvidence')
      await verifyRegistryPieces(pieces, spec.sources.registry);
    else if (c.componentId === 'coverage') {
      const buffers = [];
      let size = 0;
      for await (const piece of pieces) {
        size += piece.length;
        buffers.push(piece);
      }
      const raw = new Uint8Array(size);
      let offset = 0;
      for (const p of buffers) {
        raw.set(p, offset);
        offset += p.length;
      }
      coverage = coverageIndex(
        parseStrictJson(new TextDecoder('utf-8', { fatal: true }).decode(raw)),
        spec,
      );
    } else
      for await (const piece of pieces) {
        void piece;
      }
  }
  await assertRegistry(env, job.owner, spec.sources.registry);
  const now = NOW(),
    summary = JSON.stringify(coverage.summary),
    registry = JSON.stringify(spec.sources.registry);
  // All identity links and coverage indexes appear with the terminal transition.
  const gate = `EXISTS(SELECT 1 FROM quant_dataset_jobs WHERE id=? AND owner=? AND status='running' AND lease_token=? AND lease_until>? AND deadline>?) AND NOT EXISTS(SELECT 1 FROM json_each(?) x WHERE NOT EXISTS(SELECT 1 FROM financial_registry_entries r WHERE r.id=json_extract(x.value,'$.ref') AND r.owner=? AND r.status='active' AND r.sha256=json_extract(x.value,'$.sha256') AND r.byte_length=json_extract(x.value,'$.byteLength') AND r.kind=json_extract(x.value,'$.kind')))`;
  await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_research_datasets(id,owner,name,dataset_root,stage_id,status,scope,summary,created_at) SELECT ?,?,?,?,?,'ready',?,?,? WHERE ${gate}`,
    ).bind(
      stage.dataset_id,
      job.owner,
      plan.name,
      stage.dataset_root,
      stage.id,
      JSON.stringify(parsed.manifest.scope),
      summary,
      now,
      job.id,
      job.owner,
      job.lease_token,
      now,
      now,
      registry,
      job.owner,
    ),
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_dataset_coverage(dataset_id,ordinal,symbol,state_id,status,metadata) SELECT ?,CAST(x.key AS INTEGER),json_extract(x.value,'$.symbol'),json_extract(x.value,'$.stateId'),json_extract(x.value,'$.status'),json_extract(x.value,'$.metadata') FROM json_each(?) x WHERE EXISTS(SELECT 1 FROM quant_research_datasets WHERE id=? AND stage_id=?)`,
    ).bind(
      stage.dataset_id,
      JSON.stringify(coverage.entries),
      stage.dataset_id,
      stage.id,
    ),
    env.DB.prepare(
      "UPDATE quant_dataset_stages SET status='committed',summary=?,updated_at=? WHERE id=? AND EXISTS(SELECT 1 FROM quant_research_datasets WHERE id=? AND stage_id=?)",
    ).bind(summary, now, stage.id, stage.dataset_id, stage.id),
    env.DB.prepare(
      "UPDATE quant_dataset_jobs SET status='completed',dataset_id=?,phase='writing_evidence',updated_at=? WHERE id=? AND lease_token=? AND status='running' AND EXISTS(SELECT 1 FROM quant_research_datasets WHERE id=? AND stage_id=?)",
    ).bind(
      stage.dataset_id,
      now,
      job.id,
      job.lease_token,
      stage.dataset_id,
      stage.id,
    ),
  ]);
  const actual = await env.DB.prepare(
    'SELECT status,dataset_id FROM quant_dataset_jobs WHERE id=?',
  )
    .bind(job.id)
    .first();
  if (actual.status !== 'completed' || actual.dataset_id !== stage.dataset_id)
    fail('STALE_LEASE', '任务已停止，数据集未提交', 409);
  return {
    ok: true,
    status: 'completed',
    datasetRef: datasetRef({
      id: stage.dataset_id,
      dataset_root: stage.dataset_root,
    }),
  };
}
