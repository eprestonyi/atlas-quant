/** Query metadata only. Complete data remain in immutable R2 chunks. */
import { ApiError } from '../errors.mjs';
import { sha } from '../runtime.mjs';
import { DATE, BUNDLE_PROFILE } from './profile.mjs';
import { byteLength, object } from './json.mjs';
import { marketHedgeMetadata } from '../market-preparation/hedge-index.mjs';

const fail = (message) => {
  throw new ApiError('BUNDLE_RECORD', message);
};
const identifier = (value, label, optional = false) => {
  if (optional && (value === null || value === undefined)) return null;
  if (typeof value !== 'string' || !value || value.length > 160) fail(label + '身份无效');
  return value;
};
const date = (value, optional = false) => {
  if (optional && (value === null || value === undefined)) return null;
  if (typeof value !== 'string' || !DATE.test(value)) fail('记录日期无效');
  return value;
};
const any = (value) =>
  Array.isArray(value) ? value.length > 0 : object(value) ? Object.keys(value).length > 0 : false;

/** Shared identity validation for both snapshot index policies. */
export function snapshotRecordKey(row) {
  if (!object(row)) fail('记录须为对象');
  const symbol = identifier(row.ts_code, '行情证券');
  if (!/^\d{6}\.(?:SH|SZ)$/.test(symbol)) fail('冻结行情证券无效');
  return date(row.trade_date) + '|' + symbol;
}

export function targetSummary(row) {
  return {
    id: row.id,
    kind: row.kind,
    symbols: row.symbols,
    construction: row.construction,
    ...(Number.isInteger(row.hedgeAudit?.projectionColumn)
      ? { hedgeAudit: { projectionColumn: row.hedgeAudit.projectionColumn } }
      : {})
  };
}
export function targetLabel(row) {
  const symbols = row.symbols.join(' / ');
  return row.construction === 'pca_residual'
    ? `${symbols} · 投影列 ${Number(row.hedgeAudit?.projectionColumn) + 1}`
    : symbols;
}

export async function recordIndex(
  collection,
  row,
  ordinal,
  chunkOrdinal,
  itemIndex,
  { marketHedgeTargets = null } = {}
) {
  if (!object(row)) fail('记录须为对象');
  let rowId = null,
    day = null,
    targetId = null,
    fitId = null,
    referenceId = null;
  let status = null,
    matured = 0,
    flags = 0,
    groupKey = null,
    metadata = {};
  if (['forecasts', 'baselineRows'].includes(collection)) {
    rowId = identifier(row.forecastId, '预测');
    day = date(row.date);
    targetId = identifier(row.targetId, '目标');
    fitId = identifier(row.modelFitId, '拟合', true);
    if (!['valid', 'invalid'].includes(row.status)) fail('预测状态无效');
    status = row.status;
    matured = Number(row.labelMaturedAt !== null && row.labelMaturedAt !== undefined);
    date(row.entryDate, true);
    date(row.targetDate, true);
    date(row.labelMaturedAt, true);
    if (status === 'valid') {
      if (
        !fitId ||
        targetId === 'unavailable' ||
        !row.entryDate ||
        !row.targetDate ||
        !(row.date < row.entryDate && row.entryDate < row.targetDate)
      )
        fail('有效预测缺少目标、拟合或正确端点');
      for (const key of [
        'currentState',
        'scale',
        'expectedEntry',
        'expectedFuture',
        'edgeGap',
        'expectedChange',
        'expectedGrossPnl',
        'expectedGrossBps'
      ]) {
        if (typeof row[key] !== 'number' || !Number.isFinite(row[key]))
          fail('有效预测缺少有限条件值');
      }
      if (row.scale <= 0) fail('有效预测尺度必须为正');
    }
    if (row.labelMaturedAt != null && row.labelMaturedAt !== row.targetDate)
      fail('标签成熟日期不一致');
    metadata = {
      entryDate: row.entryDate ?? null,
      targetDate: row.targetDate ?? null
    };
  } else if (collection === 'plannedOrigins') {
    day = date(row.date);
    targetId = identifier(row.targetId, '计划目标');
    rowId = day + '|' + targetId;
    if (typeof row.inputValid !== 'boolean') fail('计划缺少原始有效性掩码');
    date(row.entryDate, true);
    date(row.targetDate, true);
    metadata = {
      entryDate: row.entryDate ?? null,
      targetDate: row.targetDate ?? null,
      inputValid: row.inputValid
    };
  } else if (collection === 'targets') {
    rowId = identifier(row.id, '目标');
    if (
      !Array.isArray(row.symbols) ||
      row.symbols.length < 1 ||
      row.symbols.length > 50 ||
      row.symbols.some((value) => typeof value !== 'string' || !/^\d{6}\.(?:SH|SZ)$/.test(value))
    )
      fail('目标证券身份无效');
    if (
      !['asset_price', 'frozen_basket'].includes(row.kind) ||
      typeof row.construction !== 'string' ||
      row.construction.length > 80
    )
      fail('目标构造缺失');
    metadata = targetSummary(row);
    groupKey = await sha(
      JSON.stringify([
        row.kind,
        row.symbols,
        row.construction,
        row.hedgeAudit?.projectionColumn ?? null
      ])
    );
  } else if (['modelFits', 'baselineModelFits'].includes(collection)) {
    rowId = identifier(row.id, '拟合');
    metadata = {
      id: row.id,
      estimator: row.estimator ?? null,
      fitDate: row.fitDate ?? null,
      trainStart: row.trainStart ?? null,
      trainEnd: row.trainEnd ?? null,
      labelEndMax: row.labelEndMax ?? null,
      status: row.status ?? null
    };
    status = identifier(row.status, '拟合状态', true);
  } else if (collection === 'snapshotColumns') {
    rowId = identifier(row.name, '冻结数据列');
    metadata = { kind: row.kind };
  } else if (collection === 'snapshotContextSources') {
    rowId = row.api + '/' + row.params.ts_code;
    metadata = {sha256: row.sha256, rowCount: row.records.length};
  } else if (collection === 'snapshotRows') {
    rowId = snapshotRecordKey(row);
    day = row.trade_date;
  } else if (collection === 'trades') {
    day = date(row.date);
    referenceId = identifier(row.forecastId, '成交引用预测');
    targetId = identifier(row.targetId, '成交目标');
    flags = Number(row.exitReason === 'risk_limit_exit');
  } else if (collection === 'riskLedger' || collection === 'equity') {
    day = date(row.date);
    rowId = day;
    if (collection === 'riskLedger') {
      flags =
        (any(row.riskBreaches) ? 1 : 0) |
        (any(row.unavailableRiskInputs?.factors) ||
        any(row.unavailableRiskInputs?.volatilitySymbols)
          ? 2
          : 0);
      metadata = { breachCount: row.riskBreaches?.length ?? 0 };
    }
  } else if (collection === 'decisions') {
    day = date(row.date);
    flags = Number(row.action === 'exit_pending');
    referenceId = identifier(row.forecastId, '决策预测', true);
    targetId = identifier(row.targetId, '决策目标', true);
  } else if (['finalTrials', 'baselineFinalTrials'].includes(collection)) {
    rowId = identifier(row.id, '试验');
    status = identifier(row.status, '试验状态', true);
  } else if (['perTarget', 'baselinePerTarget'].includes(collection)) {
    targetId = identifier(row.targetId, '分目标指标', true);
    rowId = targetId;
  } else if (collection === 'dailyLosses') {
    day = date(row.date);
    rowId = day;
  } else if (collection === 'hedgeFits') {
    day = date(row.date);
    if (marketHedgeTargets !== null) {
      metadata = await marketHedgeMetadata(row, marketHedgeTargets);
    } else {
      if (!Array.isArray(row.targetIds) || row.targetIds.length > 50) fail('对冲拟合目标引用无效');
      row.targetIds.forEach((id) => identifier(id, '对冲目标'));
      metadata = { targetIds: row.targetIds };
    }
  } else if (collection === 'outerFolds') {
    rowId = String(ordinal);
  } else if (row.id !== undefined) rowId = identifier(row.id, '记录');
  const encodedMetadata = JSON.stringify(metadata);
  if (byteLength(encodedMetadata) > 8192) fail('索引元数据超过上限');
  return [
    ordinal,
    chunkOrdinal,
    itemIndex,
    rowId,
    day,
    targetId,
    fitId,
    referenceId,
    status,
    matured,
    flags,
    groupKey,
    encodedMetadata
  ];
}

/** A bounded JSON binding makes one SQL operation insert many index rows. */
export function indexStatements(env, stage, collection, rows) {
  const batches = [],
    limit = BUNDLE_PROFILE.indexBatchBytes;
  let parts = [],
    bytes = 2;
  const flush = () => {
    if (!parts.length) return;
    batches.push(
      env.DB.prepare(
        `INSERT INTO quant_bundle_records
      (stage_id,collection,ordinal,chunk_ordinal,item_index,row_id,date,target_id,fit_id,reference_id,status,matured,flags,group_key,metadata)
      SELECT ?,?,json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),
        json_extract(value,'$[3]'),json_extract(value,'$[4]'),json_extract(value,'$[5]'),json_extract(value,'$[6]'),
        json_extract(value,'$[7]'),json_extract(value,'$[8]'),json_extract(value,'$[9]'),json_extract(value,'$[10]'),
        json_extract(value,'$[11]'),json_extract(value,'$[12]') FROM json_each(?)
      WHERE EXISTS(SELECT 1 FROM quant_bundle_stages s JOIN jobs j ON j.id=s.job_id
        WHERE s.id=? AND s.status='staging' AND j.status='running' AND j.lease_token=s.lease_token AND j.lease_until>?)`
      ).bind(stage.id, collection, '[' + parts.join(',') + ']', stage.id, new Date().toISOString())
    );
    parts = [];
    bytes = 2;
  };
  for (const row of rows) {
    const text = JSON.stringify(row),
      size = byteLength(text) + 1;
    if (size > limit) fail('单条索引超过预算');
    if (bytes + size > limit) flush();
    parts.push(text);
    bytes += size;
  }
  flush();
  return batches;
}
