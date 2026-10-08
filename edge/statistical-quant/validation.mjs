import financialDefinitions from '../financial/definitions.json' with { type: 'json' };
const financialStateIds = new Set(financialDefinitions.items.map(x => x.id));
import { ApiError } from '../errors.mjs';
import { validateBindings, validateExpression, validateUniverseState } from '../validation.mjs';

const fail = (message) => {
  throw new ApiError('INVALID_STATISTICAL_QUANT', message);
};
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
export function keys(value, allowed, label) {
  if (!object(value)) fail(`${label}须为对象`);
  if (Object.values(value).some((x) => x === null)) fail(`${label}不接受null参数`);
  for (const key of Object.keys(value))
    if (!allowed.includes(key)) fail(`${label}包含尚未支持的字段：${key}`);
  return value;
}
export function number(value, name, min, max, integer = false) {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (integer && !Number.isInteger(value))
  )
    fail(`${name}须为${min}–${max}${integer ? '的整数' : '的有限数值'}`);
  return value;
}
function choice(value, values, label) {
  if (!values.includes(value)) fail(`${label}选项无效`);
  return value;
}
function boolean(value, label) {
  if (typeof value !== 'boolean') fail(`${label}须为布尔值`);
  return value;
}
function text(value, label, max = 100) {
  if (typeof value !== 'string' || !value.trim() || value.length > max)
    fail(`${label}须为1–${max}字符`);
  return value.trim();
}
function date(value) {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) fail('日期须为YYYYMMDD');
  const formatted = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  const parsed = new Date(formatted + 'T00:00:00Z');
  if (!Number.isFinite(+parsed) || parsed.toISOString().slice(0, 10) !== formatted)
    fail('日期不存在');
  return value;
}
function symbols(value, min, max, label) {
  if (
    !Array.isArray(value) ||
    value.length < min ||
    value.length > max ||
    new Set(value).size !== value.length ||
    value.some((x) => typeof x !== 'string' || !/^\d{6}\.(SH|SZ)$/.test(x))
  )
    fail(`${label}须为${min}–${max}个不重复沪深证券代码`);
  return [...value];
}

export function validateExecution(input = {}) {
  keys(input, ['enabled', 'side', 'shorting', 'minEdgeBps', 'maxPositions'], '执行配置');
  return {
    enabled: boolean(input.enabled ?? true, '执行开关'),
    side: choice(input.side ?? 'long_short', ['long_only', 'long_short'], '方向'),
    shorting: choice(input.shorting ?? 'theoretical', ['theoretical'], '借券假设'),
    minEdgeBps: number(input.minEdgeBps ?? 10, '最低预测变化', 0, 10000),
    maxPositions: number(input.maxPositions ?? 5, '最大目标数', 1, 50, true)
  };
}
export function validatePortfolio(input = {}, factors = null) {
  keys(
    input,
    [
      'initialCapital',
      'grossExposure',
      'maxWeight',
      'rebalanceDays',
      'rebalanceThresholdBps',
      'netExposureLimit',
      'sizingMode',
      'targetAnnualVolatility',
      'volatilityLookback',
      'factorExposureLimits'
    ],
    '组合配置'
  );
  const limits = input.factorExposureLimits ?? [];
  if (!Array.isArray(limits) || limits.length > 32) fail('因子风险约束最多32项');
  const ids = new Set(),
    known = factors === null ? null : new Set(factors.map((f) => f.id));
  const factorExposureLimits = limits.map((limit) => {
    keys(limit, ['factorId', 'maxAbsExposure'], '因子风险约束');
    const factorId = text(limit.factorId, '风险因子ID');
    if (ids.has(factorId) || (known && !known.has(factorId)))
      fail('风险因子必须是已选择且不重复的因子');
    ids.add(factorId);
    return { factorId, maxAbsExposure: number(limit.maxAbsExposure, '最大绝对因子暴露', 0, 5) };
  });
  return {
    netExposureLimit: number(input.netExposureLimit ?? 2, '最大净敞口', 0, 2),
    sizingMode: choice(input.sizingMode ?? 'fixed', ['fixed', 'volatility_target'], '规模配置'),
    targetAnnualVolatility: number(input.targetAnnualVolatility ?? 0.1, '目标年化波动', 0.01, 1),
    volatilityLookback: number(input.volatilityLookback ?? 60, '波动估计窗口', 20, 252, true),
    factorExposureLimits,
    initialCapital: number(input.initialCapital ?? 1e6, '初始资金', 10000, 1e9),
    grossExposure: number(input.grossExposure ?? 1, '总敞口', 0.1, 2),
    maxWeight: number(input.maxWeight ?? 0.3, '单股目标上限', 0.01, 1),
    rebalanceDays: number(input.rebalanceDays ?? 1, '调仓间隔', 1, 60, true),
    rebalanceThresholdBps: number(input.rebalanceThresholdBps ?? 25, '调仓偏离带', 0, 10000)
  };
}
export function validateCosts(input = {}) {
  const ranges = {
    commissionBps: [2.5, 100],
    slippageBps: [3, 200],
    sellTaxBps: [5, 100],
    transferBps: [0.1, 100],
    minCommission: [5, 1000],
    borrowAnnualBps: [300, 10000]
  };
  keys(input, Object.keys(ranges), '成本');
  return Object.fromEntries(
    Object.entries(ranges).map(([key, [fallback, max]]) => [
      key,
      number(input[key] ?? fallback, key, 0, max)
    ])
  );
}

export function validateStatisticalQuant(input) {
  keys(
    input,
    [
      'schemaVersion',
      'name',
      'universe',
      'research',
      'factors',
      'preprocess',
      'target',
      'model',
      'validation',
      'execution',
      'portfolio',
      'costs',
      'dataBindings'
    ],
    '研究'
  );
  if (input.schemaVersion !== 2) fail('新研究需要schemaVersion=2');
  const u = keys(
    input.universe,
    [
      'symbols',
      'start',
      'end',
      'selection',
      'resolutionHash',
      'snapshotHash',
      'subsetPolicy',
      'catalogSnapshot',
      'presetId',
      'snapshotDate'
    ],
    '股票池'
  );
  const universe = {
    symbols: symbols(u.symbols, 1, 50, '股票池'),
    start: date(u.start),
    end: date(u.end)
  };
  const timestamp = (d) => Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8));
  if (
    universe.start >= universe.end ||
    timestamp(universe.end) - timestamp(universe.start) > 366 * 8 * 86400000
  )
    fail('历史日期须递增且最多8年');
  Object.assign(universe, validateUniverseState(u, { strictSnapshot: true }));
  if (u.presetId !== undefined) universe.presetId = text(u.presetId, '票池ID', 120);
  if (u.snapshotDate !== undefined) universe.snapshotDate = date(u.snapshotDate);
  const r = keys(input.research, ['mode', 'observationDays'], '研究模式');
  if (r.mode !== 'statistical_quant') fail('需要 statistical_quant 模式');
  const factors = input.factors ?? [];
  if (!Array.isArray(factors) || factors.length > 32) fail('最多32个因子');
  const seen = new Set();
  const cleanFactors = factors.map((f) => {
    keys(f, ['id', 'expression', 'direction', 'role', 'version'], '因子');
    const id = text(f.id, '因子ID');
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(id) || seen.has(id))
      fail('因子ID须唯一且为ASCII字母数字、下划线或连字符');
    seen.add(id);
    const expression = text(f.expression, '表达式', 500);
    validateExpression(expression);
    return {
      id,
      expression,
      direction: choice(f.direction ?? 1, [-1, 1], '因子方向'),
      role: choice(f.role ?? 'predictor', ['predictor', 'hedge', 'event'], '因子用途'),
      ...(f.version === undefined ? {} : { version: number(f.version, '因子版本', 1, 1e6, true) })
    };
  });
  const pre = keys(
    input.preprocess ?? {},
    ['winsorize', 'standardize', 'decorrelation', 'correlationThreshold'],
    '训练预处理'
  );
  const preprocess = {
    winsorize: boolean(pre.winsorize ?? true, '缩尾'),
    standardize: boolean(pre.standardize ?? true, '标准化'),
    decorrelation: choice(
      pre.decorrelation ?? 'drop_correlated',
      ['none', 'drop_correlated'],
      '去相关'
    ),
    correlationThreshold: number(pre.correlationThreshold ?? 0.9, '相关阈值', 0.5, 1)
  };
  const t = keys(input.target, ['kind', 'horizonSessions', 'basket'], '预测目标');
  const target = {
    kind: choice(t.kind, ['asset_price', 'frozen_basket'], '目标类型'),
    horizonSessions: number(t.horizonSessions ?? 5, '预测期限', 1, 60, true)
  };
  if (target.kind === 'asset_price') {
    if (t.basket !== undefined) fail('单资产目标不得携带篮子定义');
  } else {
    const b = keys(
      t.basket,
      ['method', 'symbols', 'formationDays', 'components', 'quantities'],
      '冻结篮子'
    );
    const method = choice(b.method, ['pair_ols', 'pca_residual', 'fixed'], '篮子方法');
    const basketSymbols = symbols(
      b.symbols,
      method === 'pair_ols' ? 2 : method === 'pca_residual' ? 3 : 1,
      method === 'pair_ols' ? 2 : 20,
      '篮子'
    );
    if (basketSymbols.some((s) => !universe.symbols.includes(s)))
      fail('篮子成员必须属于本次股票池');
    const basket = {
      method,
      symbols: basketSymbols,
      formationDays: number(b.formationDays ?? 126, '形成窗口', 60, 504, true)
    };
    if (method === 'pca_residual')
      basket.components = number(
        b.components ?? Math.min(2, basketSymbols.length - 2),
        'PCA维度',
        1,
        Math.min(10, basketSymbols.length - 2),
        true
      );
    else if (b.components !== undefined) fail('仅PCA篮子可配置components');
    if (method === 'fixed') {
      keys(b.quantities, basketSymbols, '冻结数量');
      if (Object.keys(b.quantities).length !== basketSymbols.length)
        fail('每个篮子成员须给出固定数量');
      basket.quantities = Object.fromEntries(
        basketSymbols.map((s) => [s, number(b.quantities[s], '数量', -1e6, 1e6)])
      );
      if (!Object.values(basket.quantities).some((q) => q !== 0)) fail('冻结篮子不可全为零');
    } else if (b.quantities !== undefined) fail('估计篮子不接受人工quantities');
    target.basket = basket;
  }
  const m = keys(input.model, ['family', 'estimator', 'trainWindow', 'refitDays'], '预测模型');
  const model = {
    family: choice(
      m.family,
      ['mean_reversion', 'pair_reversion', 'trend', 'fundamental', 'event'],
      '机制模型'
    ),
    estimator: choice(
      m.estimator ?? 'auto',
      ['auto', 'no_change', 'historical_drift', 'ridge', 'elastic_net', 'hist_gradient_boosting'],
      '预测估计器'
    ),
    trainWindow: number(m.trainWindow ?? 504, '训练窗口', 120, 1260, true),
    refitDays: number(m.refitDays ?? 20, '重新拟合间隔', 1, 126, true)
  };
  if (model.family === 'pair_reversion' && target.basket?.method !== 'pair_ols')
    fail('配对模型需要pair_ols冻结篮子');
  if (cleanFactors.some((f) => f.role === 'hedge') && target.basket?.method !== 'pca_residual')
    fail('对冲用途因子仅用于PCA篮子构建');
  const predictorFields = cleanFactors
    .filter((f) => f.role === 'predictor')
    .flatMap((f) => validateExpression(f.expression).fields);
  if (
    model.family === 'fundamental' &&
    !predictorFields.some(
      (f) =>
        /^(fd|pcd)_/.test(f) || financialStateIds.has(f) ||
        [
          'pe',
          'pe_ttm',
          'pb',
          'ps',
          'ps_ttm',
          'dv_ratio',
          'dv_ttm',
          'total_mv',
          'circ_mv'
        ].includes(f)
    )
  )
    fail('基本面模型需要实际财务或估值输入');
  if (
    model.family === 'event' &&
    !cleanFactors.some(
      (f) =>
        f.role === 'event' &&
        validateExpression(f.expression).fields.some((x) => /^(ext|pcd|fd)_/.test(x))
    )
  )
    fail('事件模型需要有时点来源的event用途因子');
  const v = keys(
    input.validation ?? {},
    ['holdoutFraction', 'minTrainDates', 'innerFolds', 'outerFolds'],
    '验证配置'
  );
  const validation = {
    holdoutFraction: number(v.holdoutFraction ?? 0.2, '留出比例', 0.1, 0.4),
    minTrainDates: number(v.minTrainDates ?? 80, '最少训练日', 40, 252, true),
    innerFolds: number(v.innerFolds ?? 2, '内层折数', 2, 3, true),
    outerFolds: number(v.outerFolds ?? 2, '外层折数', 2, 3, true)
  };
  if (validation.minTrainDates > model.trainWindow) fail('最少训练日不能超过训练窗口');
  if (input.dataBindings?.pcd)
    for (const binding of Object.values(input.dataBindings.pcd)) {
      keys(binding, ['fieldId', 'unitCode', 'records'], 'PCD绑定');
      if (Array.isArray(binding.records))
        for (const record of binding.records)
          keys(record, ['ts_code', 'entityId', 'recordId'], 'PCD记录');
    }
  const dataBindings = validateBindings(input.dataBindings ?? {});
  if (input.dataBindings) keys(input.dataBindings, ['pcd'], '数据绑定');
  for (const binding of Object.values(dataBindings.pcd ?? {}))
    if (binding.records.some((r) => !universe.symbols.includes(r.ts_code)))
      fail('PCD绑定包含股票池外成员');
  return {
    schemaVersion: 2,
    name: text(input.name, '研究名称', 80),
    universe,
    research: {
      mode: 'statistical_quant',
      observationDays: number(r.observationDays ?? 1, '观察间隔', 1, 60, true)
    },
    factors: cleanFactors,
    preprocess,
    target,
    model,
    validation,
    execution: validateExecution(input.execution),
    portfolio: validatePortfolio(input.portfolio, cleanFactors),
    costs: validateCosts(input.costs),
    dataBindings
  };
}

/** Read compatibility for early, server-stored candidates; public input stays strict.
 * Only the four exact resolver summary fields from that candidate are removable.
 * Full immutable forecast artifacts are never rewritten through this function.
 */
export function validateStoredStatisticalQuant(input) {
  const candidate = structuredClone(input),
    snapshot = candidate?.universe?.catalogSnapshot;
  if (object(snapshot)) {
    const extras = ['source', 'securityCount', 'universeCount', 'missingIdentityCount'];
    const used = extras.filter((key) => Object.hasOwn(snapshot, key));
    if (used.length) {
      if (
        Object.keys(snapshot).some(
          (key) => !['hash', 'asOf', 'historicalMembershipVerified', ...extras].includes(key)
        )
      )
        fail('已存目录快照包含未知字段，请重新解析股票池');
      if (
        snapshot.source !== undefined &&
        (typeof snapshot.source !== 'string' || !snapshot.source || snapshot.source.length > 120)
      )
        fail('已存目录来源无效');
      for (const key of ['securityCount', 'universeCount', 'missingIdentityCount'])
        if (snapshot[key] !== undefined)
          number(snapshot[key], key, 0, key === 'universeCount' ? 5000 : 10000, true);
      candidate.universe.catalogSnapshot = Object.fromEntries(
        ['hash', 'asOf', 'historicalMembershipVerified']
          .filter((key) => Object.hasOwn(snapshot, key))
          .map((key) => [key, snapshot[key]])
      );
    }
  }
  return validateStatisticalQuant(candidate);
}
