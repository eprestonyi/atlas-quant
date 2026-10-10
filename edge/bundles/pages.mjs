import { ApiError } from '../errors.mjs';
import { parse } from '../runtime.mjs';
import { BUNDLE_PROFILE, DATE } from './profile.mjs';
import { readChunk } from './storage.mjs';
import { targetLabel } from './records.mjs';

const PUBLIC_COLLECTIONS = new Set([
  'forecasts',
  'targets',
  'modelFits',
  'modelSearchCandidates',
  'factorFeatures',
  'factorJointDistributions',
  'hedgeFits',
  'perTarget',
  'outerFolds',
  'finalTrials',
  'baselineRows',
  'baselineModelFits',
  'dailyLosses',
  'baselinePerTarget',
  'baselineOuterFolds',
  'baselineFinalTrials',
  'equity',
  'trades',
  'riskLedger',
  'decisions',
  'plannedOrigins'
  ,'researchPanel'
]);
const fail = (text) => {
  throw new ApiError('INVALID_PAGE', text);
};
const integer = (value, fallback, max) => {
  const n = value === null ? fallback : Number(value);
  if (!Number.isInteger(n) || n < 0 || n > max) fail('分页参数超过范围');
  return n;
};
const boundedId = (value) => {
  if (value !== null && (!value || value.length > 160)) fail('身份筛选参数无效');
  return value;
};

export function pageQuery(params, parsed) {
  const collection = params.get('collection') || 'forecasts';
  if (!PUBLIC_COLLECTIONS.has(collection) || !parsed.collections.has(collection))
    throw new ApiError('NOT_FOUND', '报告集合不存在', 404);
  const offset = integer(params.get('offset'), 0, BUNDLE_PROFILE.rows),
    limit = integer(params.get('limit'), 25, BUNDLE_PROFILE.pageRows);
  if (!limit) fail('每页至少一条');
  const supported = new Set([
    'collection',
    'offset',
    'limit',
    'bundleId',
    'dateFrom',
    'dateTo',
    'targetId',
    'status',
    'id',
    'scope',
    'filter'
  ]);
  for (const key of params.keys()) if (!supported.has(key)) fail('不支持的查询参数：' + key);
  const dateFrom = params.get('dateFrom'),
    dateTo = params.get('dateTo');
  if (
    (dateFrom && !DATE.test(dateFrom)) ||
    (dateTo && !DATE.test(dateTo)) ||
    (dateFrom && dateTo && dateFrom > dateTo)
  )
    fail('日期筛选无效');
  const status = params.get('status') === 'all' ? null : params.get('status');
  if (status && !['valid', 'invalid', 'mature', 'unmatured'].includes(status)) fail('状态筛选无效');
  const scope = params.get('scope') || 'all',
    filter = params.get('filter') || 'all';
  if (!['all', 'latest'].includes(scope) || (scope === 'latest' && collection !== 'forecasts'))
    fail('此集合不支持 latest');
  if (
    !['all', 'events', 'missing'].includes(filter) ||
    (filter !== 'all' && collection !== 'riskLedger')
  )
    fail('此集合不支持风险筛选');
  return {
    collection,
    offset,
    limit,
    dateFrom,
    dateTo,
    status,
    scope,
    filter,
    targetId: boundedId(params.get('targetId')),
    id: boundedId(params.get('id'))
  };
}

function selectedSql(stage, query) {
  const filters = ['r.stage_id=?', 'r.collection=?'],
    args = [stage.id, query.collection];
  for (const [key, column, operator] of [
    ['dateFrom', 'date', '>='],
    ['dateTo', 'date', '<='],
    ['targetId', 'target_id', '='],
    ['id', 'row_id', '=']
  ])
    if (query[key]) {
      filters.push(`r.${column}${operator}?`);
      args.push(query[key]);
    }
  if (query.status) {
    if (['mature', 'unmatured'].includes(query.status)) {
      filters.push('r.matured=?');
      args.push(Number(query.status === 'mature'));
    } else {
      filters.push('r.status=?');
      args.push(query.status);
    }
  }
  if (query.filter === 'missing') filters.push('(r.flags & 2)!=0');
  if (query.filter === 'events')
    filters.push(
      `((r.flags & 1)!=0 OR EXISTS(SELECT 1 FROM quant_bundle_records e WHERE e.stage_id=r.stage_id AND e.date=r.date AND e.collection IN ('trades','decisions') AND e.flags=1))`
    );
  if (query.scope === 'latest') {
    // The complete origin plan is chronological (verified before publication).
    // Aggregate once by stable target identity rather than running a correlated
    // newer-row join separately for each of up to 25,000 forecast records.
    filters.push(`r.ordinal IN (SELECT max(f.ordinal) FROM quant_bundle_records f
      LEFT JOIN quant_bundle_records t ON t.stage_id=f.stage_id AND t.collection='targets' AND t.row_id=f.target_id
      WHERE f.stage_id=? AND f.collection='forecasts' GROUP BY COALESCE(t.group_key,f.target_id))`);
    args.push(stage.id);
  }
  return {
    sql: `FROM quant_bundle_records r WHERE ${filters.join(' AND ')}`,
    args
  };
}

async function indexedRelated(env, stage, records) {
  const targets = [
    ...new Set(
      records
        .flatMap((r) => [r.target_id, ...(r.collection === 'targets' ? [r.row_id] : [])])
        .filter(Boolean)
    )
  ];
  const fits = [...new Set(records.map((r) => r.fit_id).filter(Boolean))];
  const days = [...new Set(records.map((r) => r.date).filter(Boolean))];
  const targetRows = targets.length
    ? (
        await env.DB.prepare(
          `SELECT metadata FROM quant_bundle_records WHERE stage_id=? AND collection='targets' AND row_id IN (SELECT value FROM json_each(?)) ORDER BY ordinal LIMIT 100`
        )
          .bind(stage.id, JSON.stringify(targets))
          .all()
      ).results
    : [];
  const fitRows = fits.length
    ? (
        await env.DB.prepare(
          `SELECT metadata FROM quant_bundle_records WHERE stage_id=? AND collection IN ('modelFits','baselineModelFits') AND row_id IN (SELECT value FROM json_each(?)) ORDER BY ordinal LIMIT 100`
        )
          .bind(stage.id, JSON.stringify(fits))
          .all()
      ).results
    : [];
  const events = days.length
    ? (
        await env.DB.prepare(
          `SELECT date,collection,sum(CASE WHEN flags=1 THEN 1 ELSE 0 END) n FROM quant_bundle_records WHERE stage_id=? AND collection IN ('trades','decisions') AND date IN (SELECT value FROM json_each(?)) GROUP BY date,collection`
        )
          .bind(stage.id, JSON.stringify(days))
          .all()
      ).results
    : [];
  const related = {
    targets: targetRows.map((r) => parse(r.metadata)),
    modelFits: fitRows.map((r) => parse(r.metadata)),
    targetLabels: {},
    riskEvents: {}
  };
  for (const row of related.targets) related.targetLabels[row.id] = targetLabel(row);
  for (const row of records)
    if (row.collection === 'riskLedger')
      related.riskEvents[row.date] = {
        riskExitCount: 0,
        exitPendingCount: 0,
        breachCount: parse(row.metadata)?.breachCount ?? 0
      };
  for (const event of events) {
    related.riskEvents[event.date] ??= {
      riskExitCount: 0,
      exitPendingCount: 0,
      breachCount: 0
    };
    related.riskEvents[event.date][
      event.collection === 'trades' ? 'riskExitCount' : 'exitPendingCount'
    ] = event.n;
  }
  return related;
}

/** Read only chunks containing this SQL-selected page, with a hard per-call cap. */
export async function pageRecords(env, stage, parsed, query) {
  const selected = selectedSql(stage, query);
  const count = await env.DB.prepare('SELECT count(*) n ' + selected.sql)
    .bind(...selected.args)
    .first();
  const order =
    query.collection === 'forecasts' ? 'r.date DESC,r.target_id,r.row_id,r.ordinal' : 'r.ordinal';
  const records = (
    await env.DB.prepare(`SELECT r.* ${selected.sql} ORDER BY ${order} LIMIT ? OFFSET ?`)
      .bind(...selected.args, query.limit, query.offset)
      .all()
  ).results;
  const selectedRecords = [],
    groups = new Map();
  for (const record of records) {
    if (!groups.has(record.chunk_ordinal)) {
      if (groups.size >= BUNDLE_PROFILE.pageChunks) break;
      groups.set(record.chunk_ordinal, []);
    }
    groups.get(record.chunk_ordinal).push({ record, position: selectedRecords.length });
    selectedRecords.push(record);
  }
  // Decoded chunks are deliberately scoped to one iteration. Retain only the
  // selected records, never a Map of up to eight full decoded chunks.
  const selectedItems = new Array(selectedRecords.length);
  let retainedBytes = 0;
  for (const [ordinal, selected] of groups) {
    const descriptor = parsed.collections.get(query.collection).chunks[ordinal];
    const decoded = JSON.parse(
      new TextDecoder().decode(await readChunk(env, stage, query.collection, descriptor))
    );
    for (const { record, position } of selected) {
      const item = decoded[record.item_index];
      if (!item) throw new ApiError('BUNDLE_INTEGRITY', '页面记录索引不一致', 503);
      const size = new TextEncoder().encode(JSON.stringify(item)).length;
      if (retainedBytes + size > BUNDLE_PROFILE.pageBytes) continue;
      retainedBytes += size;
      selectedItems[position] = item;
    }
  }
  const firstMissing = selectedItems.findIndex((item) => item === undefined);
  const length = firstMissing < 0 ? selectedItems.length : firstMissing;
  const items = selectedItems.slice(0, length),
    accepted = selectedRecords.slice(0, length);
  if (!items.length && records.length)
    throw new ApiError('BUNDLE_PAGE_BUDGET', '单条记录超过页面预算，请使用完整下载', 413);
  const nextOffset = query.offset + items.length;
  return {
    items,
    total: count.n,
    offset: query.offset,
    limit: query.limit,
    nextOffset: nextOffset < count.n ? nextOffset : null,
    hasMore: nextOffset < count.n,
    bundleId: stage.bundle_id,
    related: await indexedRelated(env, stage, accepted)
  };
}

export async function detailRecord(env, stage, parsed, params) {
  const query = pageQuery(
    new URLSearchParams({
      collection: params.get('collection') || 'forecasts',
      id: params.get('id') || '',
      limit: '1'
    }),
    parsed
  );
  const page = await pageRecords(env, stage, parsed, query);
  if (!page.items.length) throw new ApiError('NOT_FOUND', '报告记录不存在', 404);
  return {
    item: page.items[0],
    related: page.related,
    bundleId: stage.bundle_id
  };
}

export async function chartRecords(env, stage, parsed, params) {
  const collection = params.get('collection') || 'equity';
  if (collection !== 'equity') fail('图表仅支持权益序列');
  const maxPoints = integer(params.get('maxPoints'), 500, 1000);
  if (maxPoints < 2) fail('图表至少两个点');
  const total = parsed.collections.get('equity').rowCount;
  const ordinals = [
    ...new Set(
      Array.from({ length: Math.min(total, maxPoints) }, (_, i) =>
        total <= maxPoints ? i : Math.round((i * (total - 1)) / (maxPoints - 1))
      )
    )
  ];
  const records = ordinals.length
    ? (
        await env.DB.prepare(
          `SELECT * FROM quant_bundle_records WHERE stage_id=? AND collection='equity' AND ordinal IN (SELECT value FROM json_each(?)) ORDER BY ordinal`
        )
          .bind(stage.id, JSON.stringify(ordinals))
          .all()
      ).results
    : [];
  const groups = new Map(),
    selected = new Map();
  for (const record of records) {
    if (!groups.has(record.chunk_ordinal)) groups.set(record.chunk_ordinal, []);
    groups.get(record.chunk_ordinal).push(record);
  }
  for (const [ordinal, indexed] of groups) {
    const decoded = JSON.parse(
      new TextDecoder().decode(
        await readChunk(env, stage, 'equity', parsed.collections.get('equity').chunks[ordinal])
      )
    );
    for (const record of indexed) {
      const item = decoded[record.item_index];
      if (!item || typeof item.equity !== 'number' || !Number.isFinite(item.equity))
        throw new ApiError('BUNDLE_INTEGRITY', '曲线数值索引无效', 503);
      selected.set(record.ordinal, {
        date: item.date,
        equity: item.equity,
        ...(Number.isFinite(item.benchmark) ? { benchmark: item.benchmark } : {})
      });
    }
  }
  const items = records.map((record) => selected.get(record.ordinal));
  return {
    points: items,
    totalPoints: total,
    samplingMethod: items.length === total ? 'complete' : 'uniform_index_including_endpoints',
    range: {
      dateFrom: items[0]?.date ?? null,
      dateTo: items.at(-1)?.date ?? null
    },
    bundleId: stage.bundle_id,
    metricsRecomputed: false
  };
}
