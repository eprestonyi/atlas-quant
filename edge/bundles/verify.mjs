import { ApiError } from '../errors.mjs';
import { NOW } from '../runtime.mjs';
import {
  readChunk,
  loadStage,
  leasedJob,
  parsedStage,
  terminalDiscard,
  assertTransport
} from './storage.mjs';
import { verifyDocuments } from './streams.mjs';
import { verifyContextRoot } from './context-sources.mjs';
import { assertRunMarket } from '../market-preparation/research.mjs';
import { verifyMarketCoverage } from '../market-preparation/coverage.mjs';
import { SORTED_SNAPSHOT, snapshotValidation, verifySnapshotReceipts } from './snapshot-index.mjs';
import {
  assertMarketBundle,
  storedMarketAdmission,
  verifyMarketSnapshot
} from '../market-preparation/bundle.mjs';

const invalid = (message) => {
  throw new ApiError('BUNDLE_INCOMPLETE', message, 409);
};
async function rejectIfRows(env, sql, args, message) {
  if (
    await env.DB.prepare(sql + ' LIMIT 1')
      .bind(...args)
      .first()
  )
    invalid(message);
}

/** Coverage and references are checked against all staged records, not a preview. */
export async function verifyRecords(env, stage, parsed) {
  const sortedSnapshot = snapshotValidation(stage).strategy === SORTED_SNAPSHOT;
  verifySnapshotReceipts(stage, parsed);
  const counts = await env.DB.prepare(
    'SELECT collection,count(*) n FROM quant_bundle_records WHERE stage_id=? GROUP BY collection'
  )
    .bind(stage.id)
    .all();
  const actual = new Map(counts.results.map((row) => [row.collection, row.n]));
  for (const collection of parsed.collections.values()) {
    const expected = sortedSnapshot && collection.id === 'snapshotRows' ? 0 : collection.rowCount;
    if ((actual.get(collection.id) ?? 0) !== expected)
      invalid('完整集合计数不一致：' + collection.id);
  }
  if ([...actual.keys()].some((id) => !parsed.collections.has(id))) invalid('索引含未声明集合');
  const primary = [
    'forecasts',
    ...(parsed.metadata.coverage.baselineRequired ? ['baselineRows'] : [])
  ];
  for (const collection of primary) {
    await rejectIfRows(
      env,
      `SELECT f.ordinal FROM quant_bundle_records f
      LEFT JOIN quant_bundle_records p ON p.stage_id=f.stage_id AND p.collection='plannedOrigins' AND p.ordinal=f.ordinal
      WHERE f.stage_id=? AND f.collection=? AND (p.ordinal IS NULL OR f.date IS NOT p.date OR f.target_id IS NOT p.target_id
        OR json_extract(f.metadata,'$.entryDate') IS NOT json_extract(p.metadata,'$.entryDate')
        OR json_extract(f.metadata,'$.targetDate') IS NOT json_extract(p.metadata,'$.targetDate')
        OR (f.target_id='unavailable' AND (f.status!='invalid' OR json_extract(p.metadata,'$.inputValid')!=0)))`,
      [stage.id, collection],
      '预测顺序或端点未覆盖独立计划'
    );
    await rejectIfRows(
      env,
      `SELECT f.ordinal FROM quant_bundle_records f
      WHERE f.stage_id=? AND f.collection=? AND f.target_id!='unavailable' AND NOT EXISTS(
        SELECT 1 FROM quant_bundle_records t WHERE t.stage_id=f.stage_id AND t.collection='targets' AND t.row_id=f.target_id)`,
      [stage.id, collection],
      '预测目标定义缺失'
    );
    const fitCollection = collection === 'forecasts' ? 'modelFits' : 'baselineModelFits';
    await rejectIfRows(
      env,
      `SELECT f.ordinal FROM quant_bundle_records f
      WHERE f.stage_id=? AND f.collection=? AND f.fit_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM quant_bundle_records m WHERE m.stage_id=f.stage_id AND m.collection=? AND m.row_id=f.fit_id)`,
      [stage.id, collection, fitCollection],
      '预测引用的拟合记录缺失'
    );
    await rejectIfRows(
      env,
      `SELECT f.ordinal FROM quant_bundle_records f JOIN quant_bundle_records m ON m.stage_id=f.stage_id AND m.collection=? AND m.row_id=f.fit_id WHERE f.stage_id=? AND f.collection=? AND f.status='valid' AND m.status IS NOT 'valid'`,
      [fitCollection, stage.id, collection],
      '有效预测引用失效拟合记录'
    );
  }
  await rejectIfRows(
    env,
    `SELECT t.ordinal FROM quant_bundle_records t
    LEFT JOIN quant_bundle_records f ON f.stage_id=t.stage_id AND f.collection='forecasts' AND f.row_id=t.reference_id
    WHERE t.stage_id=? AND t.collection IN ('trades','decisions') AND t.reference_id IS NOT NULL
      AND (f.ordinal IS NULL OR (t.collection='trades' AND t.target_id IS NOT f.target_id))`,
    [stage.id],
    '成交或决策引用其他预测'
  );
  if (!storedMarketAdmission(stage)) {
    await rejectIfRows(
      env,
      `SELECT ordinal FROM quant_bundle_records WHERE stage_id=? AND collection='hedgeFits'
       AND (json_type(metadata,'$.targetIds') IS NOT 'array' OR json_array_length(metadata,'$.targetIds')>50
         OR json_type(metadata,'$.targetIndexPolicy') IS NOT NULL)`,
      [stage.id],
      '普通报告不能使用完整市场拟合索引'
    );
  }
  await rejectIfRows(
    env,
    `SELECT h.ordinal FROM quant_bundle_records h,json_each(h.metadata,'$.targetIds') ids
    WHERE h.stage_id=? AND h.collection='hedgeFits' AND NOT EXISTS(
      SELECT 1 FROM quant_bundle_records t WHERE t.stage_id=h.stage_id AND t.collection='targets' AND t.row_id=ids.value)`,
    [stage.id],
    '对冲构造目标引用缺失'
  );
  await rejectIfRows(
    env,
    `SELECT p.ordinal FROM quant_bundle_records p
    JOIN quant_bundle_records q ON q.stage_id=p.stage_id AND q.collection=p.collection AND q.ordinal=p.ordinal-1
    WHERE p.stage_id=? AND p.collection='plannedOrigins' AND p.date<q.date`,
    [stage.id],
    '覆盖计划日期顺序无效'
  );
  const equity = parsed.collections.get('equity'),
    ledger = parsed.collections.get('riskLedger');
  if (equity.rowCount !== ledger.rowCount) invalid('权益与风险台账日期数量不一致');
  await rejectIfRows(
    env,
    `SELECT e.ordinal FROM quant_bundle_records e
    LEFT JOIN quant_bundle_records l ON l.stage_id=e.stage_id AND l.collection='riskLedger' AND l.ordinal=e.ordinal
    WHERE e.stage_id=? AND e.collection='equity' AND e.date IS NOT l.date`,
    [stage.id],
    '权益与风险台账日期不一致'
  );
}

export async function finalizeBundle(
  env,
  input,
  {
    expectedFormat = 'atlas.quant.bundle',
    expectedVersion = 1,
    authorize = null,
    verifySource = null
  } = {}
) {
  const job = await leasedJob(env, input);
  if (['failed', 'cancelled'].includes(job.status)) return terminalDiscard(job);
  const stage = await loadStage(env, input.stageId, job);
  if (stage.bundle_id !== input.bundleId)
    throw new ApiError('BUNDLE_CONFLICT', '传输身份不匹配', 409);
  const parsed = await parsedStage(stage);
  assertTransport(parsed, expectedFormat, expectedVersion);
  if (stage.status === 'committed')
    return {
      ok: true,
      bundleId: stage.bundle_id,
      status: 'committed',
      idempotent: true
    };
  if (stage.status === 'aborted' || job.status !== 'running') invalid('终态任务不能验证新增产物');
  if (authorize) await authorize(env, job, parsed);
  const marketAdmission = storedMarketAdmission(stage);
  if (marketAdmission) await assertMarketBundle(env, job, parsed, marketAdmission);
  const receipts = await env.DB.prepare(
    'SELECT collection,ordinal,sha256,byte_length,row_count,start_row,object_key FROM quant_bundle_chunks WHERE stage_id=?'
  )
    .bind(stage.id)
    .all();
  if (receipts.results.length !== parsed.manifest.totals.chunkCount) invalid('完整分片尚未上传');
  const byKey = new Map(receipts.results.map((row) => [row.collection + ':' + row.ordinal, row]));
  for (const collection of parsed.collections.values())
    for (const descriptor of collection.chunks) {
      const row = byKey.get(collection.id + ':' + descriptor.ordinal);
      if (
        !row ||
        row.sha256 !== descriptor.sha256 ||
        row.byte_length !== descriptor.byteLength ||
        row.row_count !== descriptor.count ||
        row.start_row !== descriptor.start
      )
        invalid('分片凭据与 manifest 不一致');
    }
  await verifyRecords(env, stage, parsed);
  await verifyDocuments(parsed, (collection, descriptor) =>
    readChunk(env, stage, collection, descriptor, byKey)
  );
  await verifyContextRoot(parsed, (collection, descriptor) =>
    readChunk(env, stage, collection, descriptor, byKey)
  );
  await verifyMarketSnapshot(stage, parsed, (collection, descriptor) =>
    readChunk(env, stage, collection, descriptor, byKey)
  );
  if (marketAdmission) {
    const admission = await assertRunMarket(env, job);
    await verifyMarketCoverage(env, stage, parsed, admission, (collection, descriptor) =>
      readChunk(env, stage, collection, descriptor, byKey)
    );
  }
  if (verifySource)
    await verifySource(
      parsed,
      (collection, descriptor) => readChunk(env, stage, collection, descriptor, byKey),
      { env, stage }
    );
  await env.ARTIFACTS.put(stage.manifest_key, stage.manifest_text, {
    sha256: stage.bundle_id,
    httpMetadata: { contentType: 'application/json' }
  });
  const update = await env.DB.prepare(
    `UPDATE quant_bundle_stages SET status='verified',updated_at=?
    WHERE id=? AND status IN ('staging','verified') AND EXISTS(SELECT 1 FROM jobs WHERE id=? AND owner=? AND lease_token=? AND status='running' AND lease_until>?)`
  )
    .bind(NOW(), stage.id, job.id, job.owner, job.lease_token, NOW())
    .run();
  if (!update.meta.changes) {
    const latest = await leasedJob(env, input);
    if (['failed', 'cancelled'].includes(latest.status)) return terminalDiscard(latest);
    const current = await loadStage(env, stage.id, latest);
    if (current.status === 'committed')
      return {
        ok: true,
        bundleId: stage.bundle_id,
        status: 'committed',
        idempotent: true
      };
    throw new ApiError('BUNDLE_CONFLICT', '验证阶段租约已变化', 409);
  }
  return { ok: true, bundleId: stage.bundle_id, status: 'verified' };
}
