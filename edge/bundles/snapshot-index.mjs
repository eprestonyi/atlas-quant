/** Optional physical snapshot index policy; logical bundle bytes never change. */
import { ApiError } from '../errors.mjs';
import { BUNDLE_PROFILE, HASH } from './profile.mjs';
import { byteLength, object } from './json.mjs';
import { snapshotRecordKey } from './records.mjs';

export const INDEXED_SNAPSHOT = 'indexed_v1';
export const SORTED_SNAPSHOT = 'snapshot_sorted_v1';
export const SNAPSHOT_VALIDATION_BYTES = 64 * 1024;
const KEY = /^\d{8}\|\d{6}\.(?:SH|SZ)$/;
const fail = (message, code = 'BUNDLE_INDEX_POLICY', status = 400) => {
  throw new ApiError(code, message, status);
};

export function requestedSnapshotStrategy(input) {
  if (Object.hasOwn(input, 'indexValidation')) fail('indexValidation 是服务端校验状态');
  if (!Object.hasOwn(input, 'snapshotIndexStrategy')) return null;
  if (![INDEXED_SNAPSHOT, SORTED_SNAPSHOT].includes(input.snapshotIndexStrategy))
    fail('不支持的冻结行情索引策略');
  return input.snapshotIndexStrategy;
}

export function snapshotValidation(stage) {
  let metadata;
  try {
    metadata = JSON.parse(stage.metadata);
  } catch {
    fail('上传阶段元数据损坏', 'BUNDLE_INTEGRITY', 503);
  }
  if (!object(metadata)) fail('上传阶段元数据损坏', 'BUNDLE_INTEGRITY', 503);
  if (!Object.hasOwn(metadata, 'indexValidation'))
    return { strategy: INDEXED_SNAPSHOT, receipts: {} };
  const validation = metadata.indexValidation;
  const state = validation?.snapshotRows;
  if (
    !object(validation) ||
    validation.version !== 1 ||
    Object.keys(validation).some((key) => !['version', 'snapshotRows'].includes(key)) ||
    !object(state) ||
    state.strategy !== SORTED_SNAPSHOT ||
    !object(state.receipts) ||
    Object.keys(state).some((key) => !['strategy', 'receipts'].includes(key)) ||
    byteLength(JSON.stringify(validation)) > SNAPSHOT_VALIDATION_BYTES ||
    byteLength(stage.metadata) > BUNDLE_PROFILE.manifestBytes
  )
    fail('上传阶段索引策略损坏', 'BUNDLE_INTEGRITY', 503);
  return state;
}

function validationWithReceipts(receipts) {
  return { version: 1, snapshotRows: { strategy: SORTED_SNAPSHOT, receipts } };
}

/** Reserve every possible receipt before creating a stage, not after uploads. */
export function stageMetadata(parsed, strategy) {
  if (Object.hasOwn(parsed.metadata, 'indexValidation')) fail('元数据包含保留校验状态');
  if (strategy === INDEXED_SNAPSHOT) return JSON.stringify(parsed.metadata);
  const snapshot = parsed.collections.get('snapshotRows');
  if (!snapshot || parsed.manifest.kind !== 'forecast')
    fail('有序行情策略仅适用于含冻结行情的新预测产物');
  const worstReceipts = Object.fromEntries(
    snapshot.chunks.map((descriptor) => [
      String(descriptor.ordinal),
      {
        start: descriptor.start,
        count: descriptor.count,
        sha256: descriptor.sha256,
        firstKey: '99999999|999999.SZ',
        lastKey: '99999999|999999.SZ'
      }
    ])
  );
  const worst = validationWithReceipts(worstReceipts);
  if (
    byteLength(JSON.stringify(worst)) > SNAPSHOT_VALIDATION_BYTES ||
    byteLength(JSON.stringify({ ...parsed.metadata, indexValidation: worst })) >
      BUNDLE_PROFILE.manifestBytes
  )
    fail('元数据没有足够空间保存全部行情分片凭据', 'BUNDLE_BUDGET', 413);
  return JSON.stringify({
    ...parsed.metadata,
    indexValidation: validationWithReceipts({})
  });
}

/** Actual decoded records determine boundaries; no caller-provided summary is used. */
export function summarizeSnapshot(rows, descriptor) {
  if (!rows.length || rows.length !== descriptor.count)
    fail('冻结行情分片计数无效', 'BUNDLE_RECORD');
  let firstKey = null,
    lastKey = null;
  for (const row of rows) {
    const key = snapshotRecordKey(row);
    if (lastKey !== null && key <= lastKey)
      fail('冻结行情必须按日期、证券严格升序且不能重复', 'BUNDLE_RECORD');
    firstKey ??= key;
    lastKey = key;
  }
  return {
    start: descriptor.start,
    count: rows.length,
    sha256: descriptor.sha256,
    firstKey,
    lastKey
  };
}

export function receiptPath(ordinal) {
  if (!Number.isInteger(ordinal) || ordinal < 0 || ordinal >= BUNDLE_PROFILE.chunks)
    fail('行情分片位置无效');
  return `$.indexValidation.snapshotRows.receipts."${ordinal}"`;
}

/** The JSON_SET operates on the live DB value inside the chunk transaction. */
export function snapshotReceiptStatement(env, stage, ordinal, receipt, now) {
  const path = receiptPath(ordinal),
    text = JSON.stringify(receipt);
  return env.DB.prepare(
    `UPDATE quant_bundle_stages SET metadata=json_set(metadata,?,json(?))
    WHERE id=? AND status='staging'
      AND json_extract(metadata,'$.indexValidation.snapshotRows.strategy')=?
      AND length(CAST(json_set(metadata,?,json(?)) AS BLOB))<=?
      AND length(CAST(json_extract(json_set(metadata,?,json(?)),'$.indexValidation') AS BLOB))<=?
      AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=quant_bundle_stages.job_id
        AND j.owner=quant_bundle_stages.owner AND j.status='running'
        AND j.lease_token=quant_bundle_stages.lease_token AND j.lease_until>?)`
  ).bind(
    path,
    text,
    stage.id,
    SORTED_SNAPSHOT,
    path,
    text,
    BUNDLE_PROFILE.manifestBytes,
    path,
    text,
    SNAPSHOT_VALIDATION_BYTES,
    now
  );
}

export function assertSnapshotReceipt(receipt, descriptor) {
  if (
    !object(receipt) ||
    Object.keys(receipt).length !== 5 ||
    receipt.start !== descriptor.start ||
    receipt.count !== descriptor.count ||
    receipt.sha256 !== descriptor.sha256 ||
    !HASH.test(receipt.sha256) ||
    typeof receipt.firstKey !== 'string' ||
    !KEY.test(receipt.firstKey) ||
    typeof receipt.lastKey !== 'string' ||
    !KEY.test(receipt.lastKey) ||
    receipt.firstKey > receipt.lastKey ||
    (receipt.count === 1 && receipt.firstKey !== receipt.lastKey) ||
    (receipt.count > 1 && receipt.firstKey === receipt.lastKey)
  )
    fail('行情分片校验凭据与 manifest 不一致', 'BUNDLE_INCOMPLETE', 409);
}

export function verifySnapshotReceipts(stage, parsed) {
  const state = snapshotValidation(stage);
  if (state.strategy !== SORTED_SNAPSHOT) return;
  const snapshot = parsed.collections.get('snapshotRows');
  if (!snapshot || Object.keys(state.receipts).length !== snapshot.chunks.length)
    fail('行情分片校验凭据不完整', 'BUNDLE_INCOMPLETE', 409);
  let lastKey = null,
    count = 0;
  for (const descriptor of snapshot.chunks) {
    const receipt = state.receipts[String(descriptor.ordinal)];
    assertSnapshotReceipt(receipt, descriptor);
    if (lastKey !== null && receipt.firstKey <= lastKey)
      fail('冻结行情跨分片乱序或重复', 'BUNDLE_INCOMPLETE', 409);
    lastKey = receipt.lastKey;
    count += receipt.count;
  }
  if (count !== snapshot.rowCount) fail('冻结行情实际分片总计数不一致', 'BUNDLE_INCOMPLETE', 409);
}
