/** Scalar return observations have no entry quote or execution edge. */
const finite = x => typeof x === 'number' && Number.isFinite(x);
const near = (a,b) => Math.abs(a-b) <= 1e-10*Math.max(1,Math.abs(a),Math.abs(b));
const symbol = x => typeof x === 'string' && /^\d{6}\.(SH|SZ)$/.test(x);
function observedIdentity(row,fail) {
  for (const key of ['observedResponse','observedReturn']) if (row[key] != null && !finite(row[key])) fail('收益观察结果须为有限值或空');
  if (finite(row.observedResponse) && (!finite(row.observedReturn) || !finite(row.responseScale) || row.responseScale <= 0 || !near(row.observedReturn,row.responseScale*row.observedResponse))) fail('已观测收益逆变换不一致');
  if (finite(row.observedReturn) && finite(row.responseScale) && row.responseScale > 0 && !finite(row.observedResponse)) fail('已观测收益缺少标准化响应');
}

export function validateReturnRecord(row, fail) {
  const date = x => typeof x === 'string' && /^\d{8}$/.test(x);
  if (row.schema !== 'asset-return-observation/1' ||
      !symbol(row.assetSymbol))
    fail('收益记录身份无效');
  if (['expectedEntry', 'expectedFuture', 'expectedGrossPnl', 'entryDate'].some(k => Object.hasOwn(row, k)))
    fail('收益记录不得混入旧价格交易输出');
  for (const key of ['responseStartDate', 'responseEndDate', 'featureDate'])
    if (row[key] != null && !date(row[key])) fail('收益观察日期无效');
  if (row.informationCutoff !== row.date+'_AFTER_CLOSE') fail('收益信息截止必须为观察日收盘后');
  if (row.featureDate !== row.date) fail('因子观察时点不一致');
  if (row.status === 'valid') {
    if (!row.modelFitId || row.targetId === 'unavailable') fail('收益输出缺少逐资产拟合身份');
    for (const key of ['originPrice', 'responseScale', 'predictedResponse', 'predictedReturn', 'conditionalPrice'])
      if (!finite(row[key])) fail('收益输出缺少有限数值');
    if (row.originPrice <= 0 || row.responseScale <= 0) fail('收益价格与起点尺度须为正');
    if (!near(row.predictedReturn, row.responseScale * row.predictedResponse) ||
        !near(row.conditionalPrice, row.originPrice * (1 + row.predictedReturn)))
      fail('收益逆变换不一致');
  }
  if (row.responseStartDate && row.responseEndDate && row.responseStartDate >= row.responseEndDate)
    fail('收益观察区间倒置');
  if (row.labelMaturedAt != null && row.labelMaturedAt !== row.responseEndDate)
    fail('收益标签成熟时点不一致');
  observedIdentity(row,fail);
  if (finite(row.observedResponse) && finite(row.predictedResponse)) {
    if (!finite(row.responseResidual) || !near(row.responseResidual,row.observedResponse-row.predictedResponse)) fail('收益残差与响应不一致');
  } else if (row.responseResidual != null) fail('没有响应与预测时不能声明残差');
  return {
    studyProtocol: 'asset-return-study/1', assetSymbol: row.assetSymbol,
    responseStartDate: row.responseStartDate ?? null,
    responseEndDate: row.responseEndDate ?? null,
  };
}

export function validateReturnPanelRecord(row, factorNames, fail) {
  const expected = ['schema','date','targetId','assetSymbol','inputValid','invalidReason','featureDate','responseStartDate','responseEndDate','originPrice','responseScale','observedReturn','observedResponse','features'];
  if (JSON.stringify(Object.keys(row).sort()) !== JSON.stringify(expected.sort()) || row.schema !== 'asset-return-panel-row/1' || !symbol(row.assetSymbol)) fail('收益面板记录字段或身份无效');
  if (typeof row.inputValid !== 'boolean' || (row.inputValid ? row.invalidReason !== null : typeof row.invalidReason !== 'string' || !row.invalidReason || row.invalidReason.length > 100)) fail('收益面板有效性与原因不一致');
  if (row.featureDate !== row.date) fail('收益面板因子时点不一致');
  for (const key of ['date','featureDate','responseStartDate','responseEndDate']) {
    const value = row[key];
    if (value === null && ['responseStartDate','responseEndDate'].includes(key)) continue;
    if (typeof value !== 'string' || !/^\d{8}$/.test(value)) fail('收益面板日期无效');
    const iso = `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}`, d = new Date(iso+'T00:00:00Z');
    if (!Number.isFinite(+d) || d.toISOString().slice(0,10) !== iso) fail('收益面板日期不存在');
  }
  if (row.responseStartDate && row.responseEndDate && row.responseStartDate >= row.responseEndDate) fail('收益面板区间倒置');
  for (const key of ['originPrice','responseScale']) if (row[key] !== null && (!finite(row[key]) || row[key] <= 0)) fail('收益面板价格与尺度须为正值或空');
  if (row.inputValid && (!finite(row.originPrice) || !finite(row.responseScale))) fail('有效收益面板缺少起点价格或尺度');
  const features = row.features;
  if (!features || typeof features !== 'object' || Array.isArray(features) || !Array.isArray(factorNames) || !factorNames.length || factorNames.length > 32 || new Set(factorNames).size !== factorNames.length || JSON.stringify(Object.keys(features).sort()) !== JSON.stringify([...factorNames].sort()) || Object.values(features).some(x => x !== null && !finite(x))) fail('收益面板必须保留全部所选因子的有限值或明确缺失');
  if (row.inputValid && !Object.values(features).some(finite)) fail('有效收益面板没有已观测因子');
  observedIdentity(row,fail);
  return {assetSymbol:row.assetSymbol,responseStartDate:row.responseStartDate,responseEndDate:row.responseEndDate};
}
