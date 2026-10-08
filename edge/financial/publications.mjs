import { parseStrictJson } from '../bundles/json.mjs';
import {
  LIMITS,
  NOW,
  fail,
  object,
  string,
  integer,
  id,
  hash,
  parse,
  random,
  sha,
  hashBytes,
  bytes,
  utf8,
  storageUsage,
} from './common.mjs';
import { leasedJob } from './jobs.mjs';
import { validateSelection } from './inputs.mjs';
import { verifyPreparedClosure } from './integrity.mjs';
const COLLECTIONS = ['package', 'panel', 'events', 'dependencies', 'assignments', 'coverage'];

function manifest(value, job) {
  object(value, ['format', 'version', 'kind', 'inputId', 'roots', 'summary', 'collections']);
  if (
    value.format !== 'atlas.quant.financial-result' ||
    value.version !== 1 ||
    value.inputId !== job.input_id ||
    value.kind !==
      {
        financial_validate: 'validated',
        financial_revise: 'revised',
        financial_prepare: 'prepared',
      }[job.kind]
  )
    fail('INVALID_PUBLICATION', '产物与任务身份不匹配');
  object(value.roots, ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot']);
  for (const [key, x] of Object.entries(value.roots)) {
    if (key === 'preparedRoot' && job.kind !== 'financial_prepare') {
      if (x !== null) fail('INVALID_PUBLICATION', '校验结果不能声称已准备');
    } else hash(x);
  }
  const allowedSummary = [
    'input',
    'validation',
    ...(value.kind === 'prepared' ? ['preparation', 'hasUsableStates'] : []),
  ];
  object(value.summary, allowedSummary);
  if (value.kind === 'prepared' && typeof value.summary.hasUsableStates !== 'boolean')
    fail('INVALID_PUBLICATION', '整体可用性必须为布尔值');
  object(value.summary.input, ['source', 'selection', 'unitPolicy', 'counts', 'evidence']);
  const { selection } = value.summary.input;
  validateSelection(selection);
  object(selection, [
    'symbols',
    'start',
    'end',
    'announcementStart',
    'selectedStateIds',
    'scope',
    'flowBasis',
  ]);
  if (
    !Array.isArray(selection.symbols) ||
    !selection.symbols.length ||
    selection.symbols.length > 50 ||
    selection.symbols.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x)) ||
    !Array.isArray(selection.selectedStateIds) ||
    !selection.selectedStateIds.length ||
    selection.selectedStateIds.length > 16 ||
    selection.selectedStateIds.some((x) => !/^model_fin_[a-z_]{1,70}$/.test(x))
  )
    fail('INVALID_PUBLICATION', '产物研究范围无效');
  if (!['verified_only', 'allow_declared'].includes(value.summary.input.unitPolicy))
    fail('INVALID_PUBLICATION', '单位策略无效');
  if (
    value.summary.input.evidence.originalAsPublishedVerified !== false ||
    value.summary.input.evidence.revisionTimeVerified !== false
  )
    fail('INVALID_PUBLICATION', '不得提升供应商历史版本证据');
  if (value.summary.validation.status !== 'passed' || bytes(value.summary).length > 32768)
    fail('INVALID_PUBLICATION', '校验摘要无效或过大');
  object(value.collections, COLLECTIONS, value.kind === 'prepared' ? COLLECTIONS : ['package']);
  if (value.kind !== 'prepared' && Object.keys(value.collections).length !== 1)
    fail('INVALID_PUBLICATION', '尚未准备的产物不能含数值集合');
  let total = 0,
    chunkCount = 0;
  for (const [name, c] of Object.entries(value.collections)) {
    object(
      c,
      ['encoding', 'rowCount', 'byteLength', 'sha256', 'chunks'],
      name === 'package'
        ? ['encoding', 'rowCount', 'byteLength', 'sha256', 'chunks']
        : ['encoding', 'rowCount', 'byteLength', 'chunks']
    );
    if (c.encoding !== (name === 'package' ? 'bytes' : 'json_records') || !Array.isArray(c.chunks))
      fail('INVALID_PUBLICATION', '分片编码无效');
    integer(c.byteLength, 0, name === 'package' ? LIMITS.packageBytes : LIMITS.preparedBytes);
    if (name === 'package') {
      hash(c.sha256);
      if (c.rowCount !== null || c.byteLength < 1) fail('INVALID_PUBLICATION', '输入包描述无效');
    } else
      integer(
        c.rowCount,
        0,
        name === 'panel' ? LIMITS.panelRows : name === 'events' ? 10000 : 500000
      );
    let sum = 0,
      rows = 0;
    c.chunks.forEach((part, index) => {
      object(part, ['ordinal', 'startRow', 'rowCount', 'byteLength', 'sha256']);
      if (part.ordinal !== index) fail('INVALID_PUBLICATION', '分片编号不连续');
      integer(part.byteLength, 1, LIMITS.chunkBytes);
      hash(part.sha256);
      sum += part.byteLength;
      chunkCount++;
      if (name === 'package') {
        if (part.startRow !== null || part.rowCount !== null)
          fail('INVALID_PUBLICATION', '字节分片不能含行号');
      } else {
        if (part.startRow !== rows) fail('INVALID_PUBLICATION', '记录分片不连续');
        integer(part.rowCount, 1, 500);
        rows += part.rowCount;
      }
    });
    if (sum !== c.byteLength || (name !== 'package' && rows !== c.rowCount))
      fail('INVALID_PUBLICATION', '分片总量不匹配');
    total += sum;
  }
  if (
    total > LIMITS.preparedBytes ||
    chunkCount > 512 ||
    bytes(value).length > LIMITS.manifestBytes
  )
    fail('PREPARED_BYTE_BUDGET', '结果超过有界发布限制', 413);
  return { value, total };
}
export async function publication(env, pubId) {
  const row = await env.DB.prepare('SELECT * FROM financial_publications WHERE id=?')
    .bind(id(pubId))
    .first();
  if (!row) fail('NOT_FOUND', '发布记录不存在', 404);
  return row;
}
export async function publicationStatus(env, row) {
  const existing = await env.DB.prepare(
      'SELECT collection,ordinal FROM financial_chunks WHERE publication_id=?'
    )
      .bind(row.id)
      .all(),
    received = new Set(existing.results.map((r) => `${r.collection}/${r.ordinal}`)),
    m = parse(row.manifest_text);
  return {
    publicationId: row.id,
    manifestSha256: row.manifest_hash,
    status: row.status,
    missing: Object.entries(m.collections).flatMap(([collection, c]) =>
      c.chunks
        .filter((x) => !received.has(`${collection}/${x.ordinal}`))
        .map((x) => ({ collection, ordinal: x.ordinal }))
    ),
    received: existing.results.length,
  };
}
export async function beginPublication(env, input) {
  object(input, ['jobId', 'leaseToken', 'manifest']);
  const job = await leasedJob(env, input.jobId, input.leaseToken),
    { value, total } = manifest(input.manifest, job),
    text = JSON.stringify(value),
    digest = await sha(text);
  const old = await env.DB.prepare('SELECT * FROM financial_publications WHERE job_id=?')
    .bind(job.id)
    .first();
  if (old) {
    if (old.manifest_hash !== digest) fail('PUBLICATION_CONFLICT', '任务已有不同发布内容', 409);
    return publicationStatus(env, old);
  }
  const source = await env.DB.prepare('SELECT * FROM financial_inputs WHERE id=? AND owner=?')
    .bind(job.input_id, job.owner)
    .first();
  if (
    job.kind === 'financial_prepare' &&
    JSON.stringify(parse(source.roots)) !== JSON.stringify({ ...value.roots, preparedRoot: null })
  ) {
    const oldRoots = parse(source.roots, {});
    for (const k of ['inputRoot', 'packRoot', 'calendarRoot'])
      if (oldRoots[k] !== value.roots[k]) fail('ROOT_MISMATCH', '准备结果替换了已验证输入', 409);
  }
  const pubId = random(),
    now = NOW();
  const row = await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_publications(id,owner,job_id,manifest_text,manifest_hash,total_bytes,created_at,updated_at)
 SELECT ?,?,?,?,?,?,?,? WHERE COALESCE((SELECT SUM(declared_bytes) FROM financial_inputs WHERE owner=? AND parent_id IS NULL),0)+COALESCE((SELECT SUM(total_bytes) FROM financial_publications WHERE owner=?),0)+?<=?
 AND EXISTS(SELECT 1 FROM financial_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?) RETURNING *`
  )
    .bind(
      pubId,
      job.owner,
      job.id,
      text,
      digest,
      total,
      now,
      now,
      job.owner,
      job.owner,
      total,
      LIMITS.workspaceRetainedFinancialBytes,
      job.id,
      job.lease_token,
      now,
      now
    )
    .first();
  if (!row) {
    const raced = await env.DB.prepare('SELECT * FROM financial_publications WHERE job_id=?')
      .bind(job.id)
      .first();
    if (raced) {
      if (raced.manifest_hash !== digest) fail('PUBLICATION_CONFLICT', '任务已有不同发布内容', 409);
      return publicationStatus(env, raced);
    }
    await leasedJob(env, job.id, job.lease_token);
    fail('WORKSPACE_STORAGE_BUDGET', '财务产物超过工作区剩余容量', 413);
  }
  return publicationStatus(env, row);
}
function recordIndex(name, row, index, m) {
  if (!row || typeof row !== 'object' || Array.isArray(row))
    fail('INVALID_FINANCIAL_RECORD', '产物行需要对象');
  const selection = m.summary.input.selection,
    symbol = row.symbol || row.ts_code || null,
    state = row.stateId || null;
  if (
    (name !== 'dependencies' &&
      (typeof symbol !== 'string' || !selection.symbols.includes(symbol))) ||
    (['events', 'coverage'].includes(name) &&
      (typeof state !== 'string' || !selection.selectedStateIds.includes(state)))
  )
    fail('INVALID_FINANCIAL_RECORD', '记录超出已验证范围');
  let recordId = null,
    date = null,
    status = null,
    eventId = null,
    period = null,
    depStart = null,
    depCount = null,
    through = null,
    audit = null;
  if (name === 'panel') {
    const fields = selection.selectedStateIds,
      keys = ['ts_code', 'trade_date', ...fields.flatMap((x) => [x, x + '__available_date'])];
    object(row, keys);
    if (
      !/^\d{8}$/.test(row.trade_date) ||
      row.trade_date < selection.start ||
      row.trade_date > selection.end
    )
      fail('INVALID_FINANCIAL_RECORD', '面板日期超界');
    for (const f of fields) {
      const v = row[f],
        a = row[f + '__available_date'];
      if (
        (v !== null && (typeof v !== 'number' || !Number.isFinite(v))) ||
        (v === null && a !== null) ||
        (v !== null && (!/^\d{8}$/.test(a) || a > row.trade_date))
      )
        fail('INVALID_FINANCIAL_RECORD', '数值或可用日期无效');
    }
    audit = { available: fields.filter((f) => row[f] !== null) };
    recordId = symbol + '/' + row.trade_date;
    date = row.trade_date;
  } else if (name === 'events') {
    object(row, [
      'id',
      'symbol',
      'stateId',
      'computedAsOf',
      'result',
      'dependencyStart',
      'dependencyCount',
    ]);
    hash(row.id);
    if (!row.result || Object.hasOwn(row.result, 'dependencies'))
      fail('INVALID_FINANCIAL_RECORD', '事件依赖须独立发布');
    hash(row.result.lineageHash);
    integer(row.dependencyStart, 0, 500000);
    integer(row.dependencyCount, 0, 20000);
    if (row.dependencyCount === 0 && row.dependencyStart !== 0)
      fail('INVALID_FINANCIAL_RECORD', '空依赖起点必须为0');
    if (
      !/^\d{8}$/.test(row.computedAsOf) ||
      row.computedAsOf < selection.start ||
      row.computedAsOf > selection.end
    )
      fail('INVALID_FINANCIAL_RECORD', '事件日期超界');
    if (!['ok', 'missing'].includes(row.result.status))
      fail('INVALID_FINANCIAL_RECORD', '事件状态无效');
    recordId = row.id;
    date = row.computedAsOf;
    status = row.result.status;
    period = row.result.periodEnd;
    depStart = row.dependencyStart;
    depCount = row.dependencyCount;
  } else if (name === 'dependencies') {
    object(row, ['eventId', 'index', 'dependency']);
    hash(row.eventId);
    integer(row.index, 0, 20000);
    if (!row.dependency || typeof row.dependency !== 'object')
      fail('INVALID_FINANCIAL_RECORD', '依赖结构无效');
    eventId = row.eventId;
    recordId = eventId + '/' + row.index;
    depStart = row.index;
  } else if (name === 'coverage') {
    if (!symbol || !state || !['available', 'missing'].includes(row.status))
      fail('INVALID_FINANCIAL_RECORD', '覆盖坐标或状态无效');
    integer(row.okRows, 0, LIMITS.panelRows);
    integer(row.missingRows, 0, LIMITS.panelRows);
    if (row.okRows > 0 !== (row.status === 'available'))
      fail('INVALID_FINANCIAL_RECORD', '覆盖状态与有效行数不符');
    audit = { okRows: row.okRows, missingRows: row.missingRows };
    recordId = symbol + '/' + state;
    status = row.status;
  } else if (name === 'assignments') {
    object(row, ['symbol', 'from', 'through', 'states']);
    if (
      !symbol ||
      !/^\d{8}$/.test(row.from) ||
      !/^\d{8}$/.test(row.through) ||
      row.from > row.through
    )
      fail('INVALID_FINANCIAL_RECORD', '分配范围无效');
    object(row.states, selection.selectedStateIds);
    Object.values(row.states).forEach(hash);
    through = row.through;
    audit = { states: row.states };
    recordId = symbol + '/' + row.from;
    date = row.from;
  }
  return {
    recordId,
    symbol,
    state,
    eventId,
    period,
    date,
    status,
    depStart,
    depCount,
    through,
    audit,
  };
}
export async function putChunk(env, row, leaseToken, digest, name, ordinal, raw) {
  const job = await leasedJob(env, row.job_id, leaseToken);
  if (row.manifest_hash !== hash(digest)) fail('ROOT_MISMATCH', '发布版本不匹配', 409);
  const m = parse(row.manifest_text),
    part = m.collections[name]?.chunks[ordinal];
  if (!part) fail('NOT_FOUND', '分片不存在', 404);
  if (raw.byteLength !== part.byteLength || (await hashBytes(raw)) !== part.sha256)
    fail('CHUNK_INTEGRITY', '分片字节或哈希不符', 400);
  const old = await env.DB.prepare(
    'SELECT * FROM financial_chunks WHERE publication_id=? AND collection=? AND ordinal=?'
  )
    .bind(row.id, name, ordinal)
    .first();
  if (old) {
    if (old.sha256 !== part.sha256) fail('CHUNK_CONFLICT', '分片已存在不同内容', 409);
    return { ok: true, idempotent: true };
  }
  const records = name === 'package' ? null : parseStrictJson(utf8(raw), { canonical: false });
  if (records && (!Array.isArray(records) || records.length !== part.rowCount))
    fail('CHUNK_INTEGRITY', '分片记录数不匹配');
  const indices = records?.map((r, i) => recordIndex(name, r, i, m)) || [];
  const key = `financial/${job.owner}/publications/${row.id}/${name}/${ordinal}.json`;
  await env.ARTIFACTS.put(key, raw, {
    httpMetadata: { contentType: 'application/json' },
  });
  const statements = [
    env.DB.prepare(
      'INSERT INTO financial_chunks(publication_id,collection,ordinal,start_row,row_count,sha256,byte_length,object_key) VALUES(?,?,?,?,?,?,?,?)'
    ).bind(row.id, name, ordinal, part.startRow, part.rowCount, part.sha256, part.byteLength, key),
  ];
  if (indices.length)
    statements.push(
      env.DB.prepare(
        `INSERT INTO financial_records(publication_id,collection,ordinal,chunk_ordinal,item_index,record_id,symbol,state_id,event_id,period_end,date,status,dependency_start,dependency_count,through_date,audit)
 SELECT ?,?,?+CAST(j.key AS INTEGER),?,CAST(j.key AS INTEGER),json_extract(j.value,'$.recordId'),json_extract(j.value,'$.symbol'),json_extract(j.value,'$.state'),json_extract(j.value,'$.eventId'),json_extract(j.value,'$.period'),json_extract(j.value,'$.date'),json_extract(j.value,'$.status'),json_extract(j.value,'$.depStart'),json_extract(j.value,'$.depCount'),json_extract(j.value,'$.through'),json_extract(j.value,'$.audit') FROM json_each(?) j`
      ).bind(row.id, name, part.startRow, ordinal, JSON.stringify(indices))
    );
  try {
    await env.DB.batch(statements);
  } catch (error) {
    const retry = await env.DB.prepare(
      'SELECT sha256 FROM financial_chunks WHERE publication_id=? AND collection=? AND ordinal=?'
    )
      .bind(row.id, name, ordinal)
      .first();
    if (retry?.sha256 === part.sha256) return { ok: true, idempotent: true };
    /* Keep staging bytes: a concurrent receipt winner may own this fixed key. */ fail(
      'INVALID_FINANCIAL_RECORD',
      '分片记录重复或索引无效',
      400
    );
  }
  return { ok: true };
}
export async function readChunk(env, pubId, name, ordinal) {
  const row = await env.DB.prepare(
    'SELECT * FROM financial_chunks WHERE publication_id=? AND collection=? AND ordinal=?'
  )
    .bind(pubId, name, ordinal)
    .first();
  if (!row) fail('INCOMPLETE_PUBLICATION', '产物分片缺失', 409);
  return readStoredChunk(env, row);
}
export async function readStoredChunk(env, row) {
  const obj = await env.ARTIFACTS.get(row.object_key);
  if (!obj || obj.size !== row.byte_length || obj.size > LIMITS.chunkBytes)
    fail('CHUNK_INTEGRITY', '产物分片不可读取', 409);
  const raw = new Uint8Array(await obj.arrayBuffer());
  if ((await hashBytes(raw)) !== row.sha256) fail('CHUNK_INTEGRITY', '产物分片哈希不匹配', 409);
  return raw;
}
export function collectionStream(env, pub, name) {
  const m = parse(pub.manifest_text),
    parts = m.collections[name].chunks;
  let index = 0;
  return new ReadableStream({
    async pull(controller) {
      try {
        if (index === parts.length) {
          controller.close();
          return;
        }
        controller.enqueue(await readChunk(env, pub.id, name, index++));
      } catch (e) {
        controller.error(e);
      }
    },
  });
}
async function verifyPackage(env, pub, m) {
  const stream = new crypto.DigestStream('SHA-256'),
    writer = stream.getWriter();
  let size = 0;
  for (const part of m.collections.package.chunks) {
    const raw = await readChunk(env, pub.id, 'package', part.ordinal);
    size += raw.length;
    await writer.write(raw);
  }
  await writer.close();
  const digest = [...new Uint8Array(await stream.digest)]
    .map((x) => x.toString(16).padStart(2, '0'))
    .join('');
  if (size !== m.collections.package.byteLength || digest !== m.collections.package.sha256)
    fail('CHUNK_INTEGRITY', '输入包整体哈希不匹配', 409);
}
export async function completePublication(env, input) {
  object(input, ['jobId', 'leaseToken', 'publicationId', 'manifestSha256']);
  const job = await leasedJob(env, input.jobId, input.leaseToken, {
      terminal: true,
    }),
    pub = await publication(env, input.publicationId);
  if (pub.job_id !== job.id || pub.manifest_hash !== hash(input.manifestSha256))
    fail('PUBLICATION_CONFLICT', '发布与任务不匹配', 409);
  if (job.status === 'completed') {
    if (job.publication_id !== pub.id) fail('COMPLETION_CONFLICT', '任务已有不同终态', 409);
    const prep = await env.DB.prepare(
      'SELECT id FROM financial_preparations WHERE publication_id=?'
    )
      .bind(pub.id)
      .first();
    return {
      ok: true,
      status: 'completed',
      inputId: job.input_id,
      preparationId: prep?.id || null,
      idempotent: true,
    };
  }
  if (job.status !== 'running') fail('COMPLETION_CONFLICT', '任务不再运行', 409);
  if ((await publicationStatus(env, pub)).missing.length)
    fail('INCOMPLETE_PUBLICATION', '产物分片尚未上传完整', 409);
  const m = parse(pub.manifest_text);
  await verifyPackage(env, pub, m);
  if (m.kind === 'prepared') {
    await verifyPreparedClosure(env, job, pub, m);
    const invalid = await env.DB.prepare(
      `SELECT e.record_id FROM financial_records e LEFT JOIN financial_records d ON d.publication_id=e.publication_id AND d.collection='dependencies' AND d.event_id=e.record_id WHERE e.publication_id=? AND e.collection='events' GROUP BY e.ordinal HAVING COUNT(d.ordinal)!=e.dependency_count OR (COUNT(d.ordinal)>0 AND (MIN(d.ordinal)!=e.dependency_start OR MAX(d.ordinal)!=e.dependency_start+e.dependency_count-1 OR SUM(CASE WHEN d.dependency_start!=d.ordinal-e.dependency_start THEN 1 ELSE 0 END)>0)) LIMIT 1`
    )
      .bind(pub.id)
      .first();
    if (invalid) fail('DEPENDENCY_INTEGRITY', '事件依赖不完整', 409);
    const orphan = await env.DB.prepare(
      `SELECT 1 FROM financial_records d LEFT JOIN financial_records e ON e.publication_id=d.publication_id AND e.collection='events' AND e.record_id=d.event_id WHERE d.publication_id=? AND d.collection='dependencies' AND e.record_id IS NULL LIMIT 1`
    )
      .bind(pub.id)
      .first();
    if (orphan) fail('DEPENDENCY_INTEGRITY', '产物含未引用依赖', 409);
  }
  await leasedJob(env, job.id, job.lease_token); // Recheck cancellation/deadline after bounded verification.
  const now = NOW(),
    prepId = m.kind === 'prepared' ? random() : null,
    roots = JSON.stringify(m.roots),
    metadata = JSON.stringify({
      ...m.summary.input,
      validation: m.summary.validation,
    });
  const statements = [
    env.DB.prepare(
      "UPDATE financial_jobs SET status='completed',publication_id=?,updated_at=? WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?"
    ).bind(pub.id, now, job.id, job.lease_token, now, now),
    env.DB.prepare(
      "UPDATE financial_publications SET status='committed',updated_at=? WHERE id=? AND changes()=1"
    ).bind(now, pub.id),
    env.DB.prepare(
      "UPDATE financial_inputs SET status=?,canonical_publication_id=?,roots=?,metadata=?,error=NULL,updated_at=? WHERE id=? AND EXISTS(SELECT 1 FROM financial_jobs WHERE id=? AND status='completed' AND publication_id=?)"
    ).bind(
      m.kind === 'prepared' ? 'prepared' : 'ready_to_prepare',
      pub.id,
      roots,
      metadata,
      now,
      job.input_id,
      job.id,
      pub.id
    ),
  ];
  if (prepId)
    statements.push(
      env.DB.prepare(
        "INSERT OR IGNORE INTO financial_preparations(id,owner,input_id,publication_id,roots,metadata,created_at) SELECT ?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM financial_jobs WHERE id=? AND status='completed' AND publication_id=?)"
      ).bind(
        prepId,
        job.owner,
        job.input_id,
        pub.id,
        roots,
        JSON.stringify({
          ...m.summary.preparation,
          selection: m.summary.input.selection,
          unitPolicy: m.summary.input.unitPolicy,
          hasUsableStates: m.summary.hasUsableStates,
        }),
        now,
        job.id,
        pub.id
      )
    );
  await env.DB.batch(statements);
  const terminal = await env.DB.prepare(
    'SELECT status,publication_id FROM financial_jobs WHERE id=?'
  )
    .bind(job.id)
    .first();
  if (terminal.status !== 'completed' || terminal.publication_id !== pub.id)
    fail('STALE_LEASE', '任务已取消或过期，产物未发布', 409);
  const savedPrep = prepId
    ? await env.DB.prepare('SELECT id FROM financial_preparations WHERE publication_id=?')
        .bind(pub.id)
        .first()
    : null;
  return {
    ok: true,
    status: 'completed',
    inputId: job.input_id,
    preparationId: savedPrep?.id || null,
    idempotent: false,
  };
}
