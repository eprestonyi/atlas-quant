import { LIMITS, fail, id, hash, parse, bytes, integer, object } from './common.mjs';
import { publication, readChunk, readStoredChunk, collectionStream } from './publications.mjs';
import { parseStrictJson } from '../bundles/json.mjs';
export async function ownedPreparation(env, owner, prepId) {
  const row = await env.DB.prepare(
    "SELECT p.*,b.manifest_text,b.manifest_hash FROM financial_preparations p JOIN financial_publications b ON b.id=p.publication_id AND b.owner=p.owner AND b.status='committed' WHERE p.id=? AND p.owner=?"
  )
    .bind(id(prepId), owner)
    .first();
  if (!row) fail('NOT_FOUND', '准备结果不存在', 404);
  return row;
}
export function preparationDTO(row) {
  const m = parse(row.manifest_text),
    metadata = parse(row.metadata);
  return {
    preparation: {
      id: row.id,
      inputId: row.input_id,
      status: 'prepared',
      ...parse(row.roots),
      ...metadata,
      counts: Object.fromEntries(
        Object.entries(m.collections)
          .filter(([k]) => k !== 'package')
          .map(([k, c]) => [k, c.rowCount])
      ),
      createdAt: row.created_at,
    },
    collections: Object.fromEntries(
      ['coverage', 'events'].map((k) => [k, { total: m.collections[k].rowCount }])
    ),
    readiness: {
      prepared: true,
      hasUsableStates: !!metadata.hasUsableStates,
      marketComposition: 'not_checked',
      sampleCoverage: 'not_checked',
      researchBindingEnabled: false,
    },
  };
}
function projectEvent(row) {
  const r = row.result;
  return {
    eventId: row.id,
    stateId: row.stateId,
    symbol: row.symbol,
    computedAsOf: row.computedAsOf,
    asOf: r.asOf,
    availableDate: r.availableDate,
    periodEnd: r.periodEnd,
    status: r.status,
    decimalValue: r.decimalValue,
    decimalPrecision: r.decimalPrecision,
    rounding: r.rounding,
    reasonCodes: r.reasonCodes,
    qualityFlags: r.qualityFlags,
    unitVerified: r.unitVerified,
    unitEvidenceLevels: r.unitEvidenceLevels,
    declarationHashes: (r.declarationHashes || []).slice(0, 32),
    declarationCount: r.declarationHashes?.length || 0,
    declarationHashesTruncated: (r.declarationHashes?.length || 0) > 32,
    dependencyCount: row.dependencyCount,
    lineageHash: r.lineageHash,
  };
}
function projectDependency(row) {
  const d = row.dependency;
  return {
    eventId: row.eventId,
    index: row.index,
    fieldId: d.field_id,
    periodEnd: d.period_end,
    availableDate: d.available_date,
    announcementDate: d.announcement_date,
    rawDecimal: d.raw_decimal,
    rawUnit: d.raw_unit,
    currency: d.currency,
    scope: d.scope,
    basis: d.basis,
    sourceSnapshot: d.source_snapshot,
    recordHash: d.record_hash,
    unitVerified: d.unit_verified,
    evidenceLevel: d.evidence_level,
    qualityFlags: d.quality_flags,
    referencePreview: (d.unit_evidence || '').slice(0, 512),
    referenceTruncated: (d.unit_evidence || '').length > 512,
  };
}
async function cursorSignature(env, payload) {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(env.RUNNER_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  return [...new Uint8Array(await crypto.subtle.sign('HMAC', key, bytes(payload)))]
    .map((x) => x.toString(16).padStart(2, '0'))
    .join('');
}
async function makeCursor(env, payload) {
  return btoa(JSON.stringify({ payload, signature: await cursorSignature(env, payload) }));
}
async function readCursor(env, value, expected) {
  try {
    if (value.length > 4096) throw Error();
    const { payload, signature } = JSON.parse(atob(value));
    if (
      signature !== (await cursorSignature(env, payload)) ||
      JSON.stringify({ ...payload, offset: 0 }) !== JSON.stringify({ ...expected, offset: 0 })
    )
      throw Error();
    integer(payload.offset, 0, 500000);
    return payload.offset;
  } catch {
    fail('INVALID_CURSOR', '分页标识与当前筛选或版本不匹配', 409);
  }
}
export async function recordPage(env, owner, prep, url, collection, eventId = null) {
  const roots = parse(prep.roots);
  if (hash(url.searchParams.get('preparedRoot')) !== roots.preparedRoot)
    fail('ROOT_MISMATCH', '准备结果版本不匹配', 409);
  const allowed = [
    'preparedRoot',
    'cursor',
    'limit',
    ...(collection === 'dependencies' ? [] : ['stateId', 'symbol', 'status']),
    ...(collection === 'events' ? ['periodEnd', 'dateFrom', 'dateTo'] : []),
  ];
  for (const k of url.searchParams.keys())
    if (!allowed.includes(k)) fail('INVALID_INPUT', '不支持的筛选字段');
  const limit = Number(url.searchParams.get('limit') || 25);
  integer(limit, 1, 100);
  const filters = {};
  for (const k of ['stateId', 'symbol', 'status', 'periodEnd', 'dateFrom', 'dateTo']) {
    const v = url.searchParams.get(k);
    if (v && v !== 'all') filters[k] = v;
  }
  const cursorBase = {
      owner,
      preparationId: prep.id,
      preparedRoot: roots.preparedRoot,
      collection,
      eventId,
      filters,
      offset: 0,
    },
    offset = url.searchParams.has('cursor')
      ? await readCursor(env, url.searchParams.get('cursor'), cursorBase)
      : 0;
  const clauses = ['publication_id=?', 'collection=?'],
    params = [prep.publication_id, collection];
  for (const [key, column] of [
    ['stateId', 'state_id'],
    ['symbol', 'symbol'],
    ['periodEnd', 'period_end'],
    ['dateFrom', 'date'],
    ['dateTo', 'date'],
  ])
    if (filters[key]) {
      clauses.push(`${column}${key === 'dateFrom' ? '>=' : key === 'dateTo' ? '<=' : '='}?`);
      params.push(filters[key]);
    }
  if (filters.status) {
    const statuses = collection === 'coverage' ? ['usable', 'missing'] : ['ok', 'missing'];
    if (!statuses.includes(filters.status)) fail('INVALID_INPUT', '状态筛选无效');
    clauses.push('status=?');
    params.push(filters.status === 'usable' ? 'available' : filters.status);
  }
  if (eventId) {
    clauses.push('event_id=?');
    params.push(hash(eventId));
  }
  const where = clauses.join(' AND '),
    total = (
      await env.DB.prepare(`SELECT COUNT(*) n FROM financial_records WHERE ${where}`)
        .bind(...params)
        .first()
    ).n;
  const found = await env.DB.prepare(
    `SELECT * FROM financial_records WHERE ${where} ORDER BY ordinal LIMIT ? OFFSET ?`
  )
    .bind(...params, limit, offset)
    .all();
  const cache = new Map(),
    items = [];
  let outputBytes = 1024;
  for (const index of found.results) {
    if (!cache.has(index.chunk_ordinal)) {
      if (cache.size === 8) break;
      cache.set(
        index.chunk_ordinal,
        parseStrictJson(
          new TextDecoder().decode(
            await readChunk(env, prep.publication_id, collection, index.chunk_ordinal)
          ),
          { canonical: false }
        )
      );
    }
    const record = cache.get(index.chunk_ordinal)[index.item_index],
      item =
        collection === 'events'
          ? projectEvent(record)
          : collection === 'dependencies'
            ? projectDependency(record)
            : record,
      encoded = bytes(item).length;
    if (outputBytes + encoded > LIMITS.pageBytes) {
      if (!items.length) fail('RECORD_PREVIEW_BUDGET', '单条预览超出限制，请下载完整证据', 413);
      break;
    }
    items.push(item);
    outputBytes += encoded;
  }
  const next =
    offset + items.length < total
      ? await makeCursor(env, { ...cursorBase, offset: offset + items.length })
      : null;
  return {
    items,
    total,
    limit,
    nextCursor: next,
    preparedRoot: roots.preparedRoot,
    allMatchingItemsReturned: offset === 0 && items.length === total,
  };
}
export async function eventRecord(env, prep, eventId) {
  const index = await env.DB.prepare(
    "SELECT * FROM financial_records WHERE publication_id=? AND collection='events' AND record_id=?"
  )
    .bind(prep.publication_id, hash(eventId))
    .first();
  if (!index) fail('NOT_FOUND', '状态事件不存在', 404);
  return parseStrictJson(
    new TextDecoder().decode(
      await readChunk(env, prep.publication_id, 'events', index.chunk_ordinal)
    ),
    { canonical: false }
  )[index.item_index];
}
export async function eventDetail(env, prep, eventId) {
  const event = await eventRecord(env, prep, eventId);
  return {
    event: projectEvent(event),
    preparedRoot: parse(prep.roots).preparedRoot,
  };
}
export function attachment(body, name, extra = {}) {
  return new Response(body, {
    encodeBody: 'manual',
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store, no-transform',
      'content-encoding': 'identity',
      'content-disposition': `attachment; filename="${name}"`,
      'x-content-type-options': 'nosniff',
      ...extra,
    },
  });
}
export async function eventDownload(env, prep, eventId) {
  const event = await eventRecord(env, prep, eventId),
    result = { ...event.result };
  // One bounded descriptor query, then each intersecting R2 chunk exactly once.
  // Even a 20,000-dependency event never issues one request per dependency.
  const descriptors = await env.DB.prepare(
    "SELECT * FROM financial_chunks WHERE publication_id=? AND collection='dependencies' AND start_row<? AND start_row+row_count>? ORDER BY ordinal"
  )
    .bind(prep.publication_id, event.dependencyStart + event.dependencyCount, event.dependencyStart)
    .all();
  if (descriptors.results.length > 512) fail('DEPENDENCY_INTEGRITY', '依赖分片超过协议预算', 409);
  let started = false,
    chunkIndex = 0,
    emitted = 0,
    cancelled = false;
  const wrapper = {
    id: event.id,
    symbol: event.symbol,
    stateId: event.stateId,
    computedAsOf: event.computedAsOf,
  };
  const prefix =
    JSON.stringify(wrapper).slice(0, -1) +
    ',"result":' +
    JSON.stringify(result).slice(0, -1) +
    ',"dependencies":[';
  return attachment(
    new ReadableStream(
      {
        async pull(controller) {
          try {
            if (cancelled) return;
            if (!started) {
              started = true;
              controller.enqueue(bytes(prefix));
              return;
            }
            if (chunkIndex === descriptors.results.length) {
              if (emitted !== event.dependencyCount)
                fail('DEPENDENCY_INTEGRITY', '事件依赖缺失', 409);
              controller.enqueue(bytes(']}}'));
              controller.close();
              return;
            }
            const descriptor = descriptors.results[chunkIndex++];
            const rows = parseStrictJson(
              new TextDecoder().decode(await readStoredChunk(env, descriptor)),
              { canonical: false }
            );
            if (cancelled) return;
            const start = Math.max(0, event.dependencyStart - descriptor.start_row),
              end = Math.min(
                rows.length,
                event.dependencyStart + event.dependencyCount - descriptor.start_row
              );
            const parts = [];
            for (let n = start; n < end; n++) {
              const row = rows[n];
              if (row.eventId !== event.id || row.index !== emitted)
                fail('DEPENDENCY_INTEGRITY', '事件依赖顺序不一致', 409);
              parts.push((emitted ? ',' : '') + JSON.stringify(row.dependency));
              emitted++;
            }
            controller.enqueue(bytes(parts.join('')));
          } catch (error) {
            if (!cancelled) controller.error(error);
          }
        },
        cancel() {
          cancelled = true;
        },
      },
      { highWaterMark: 0 }
    ),
    `financial-event-${event.id}.json`,
    { 'x-dependency-count': String(event.dependencyCount) }
  );
}
