// Versioned research defaults and client-side protocol checks; server validation remains authoritative.
export const RESEARCH_MODE = 'statistical_quant';
export const STEPS = [
  { id: 'universe', name: '股票筛选', short: '筛选', icon: 'database', hint: '逐层加入和剔除条件，完整筛选结果就是研究票池' },
  { id: 'model', name: '研究机制', short: '机制', icon: 'model', hint: '选择 F 所检验的经济或统计假设' },
  { id: 'settings', name: '研究设置', short: '设置', icon: 'clock', hint: '定义数据、研究窗口、观察频率与预测期限' },
  { id: 'state', name: '因子与状态', short: '因子', icon: 'layers', hint: '定义 X，拟合和方法选择由系统按时间验证完成' },
  { id: 'validation', name: '拟合与检验', short: '检验', icon: 'shield', hint: '冻结候选协议，比较样本外预测误差与无变化基准' },
  { id: 'report', name: 'F 模型与报告', short: '报告', icon: 'book', hint: '保存可复核的函数、拟合记录与因子证据' },
];
export const FAMILIES = {
  mean_reversion: {
    name: '状态均值回归',
    description: '在声明期限内检验价格或篮子状态的条件变化。',
  },
  pair_reversion: {
    name: '配对相对价值',
    description: '用冻结数量定义两腿价差，再估计其未来状态。',
  },
  trend: {
    name: '趋势条件预测',
    description: '把历史变化与状态输入模型，检验有限期限的延续假设。',
  },
  fundamental: {
    name: '基本面条件预测',
    description: '使用实际已披露的基本面输入估计指定期限的状态。',
  },
  event: { name: '事件条件预测', description: '使用带可用时点的事件输入研究后续调整。' },
};
export const ESTIMATORS = {
  auto: '按时间验证比较',
  no_change: '无变化基准',
  historical_drift: '历史漂移',
  ridge: 'Ridge',
  elastic_net: 'Elastic Net',
  hist_gradient_boosting: 'Histogram Gradient Boosting',
};

export function defaultStrategy() {
  return {
    schemaVersion: 2,
    name: '我的因子研究',
    research: { mode: RESEARCH_MODE, observationDays: 1 },
    universe: { symbols: [], start: '20230101', end: '20260930' },
    factors: [],
    preprocess: {
      winsorize: true,
      standardize: true,
      decorrelation: 'drop_correlated',
      correlationThreshold: 0.9,
    },
    target: { kind: 'asset_price', horizonSessions: 5 },
    model: { family: 'mean_reversion', estimator: 'auto', trainWindow: 504, refitDays: 20 },
    validation: { holdoutFraction: 0.2, minTrainDates: 80, innerFolds: 2, outerFolds: 2 },
    execution: {
      enabled: false,
      side: 'long_short',
      minEdgeBps: 10,
      maxPositions: 5,
      shorting: 'theoretical',
    },
    portfolio: {
      initialCapital: 1000000,
      grossExposure: 1,
      maxWeight: 0.3,
      rebalanceDays: 1,
      rebalanceThresholdBps: 25,
      netExposureLimit: 2,
      sizingMode: 'fixed',
      targetAnnualVolatility: 0.1,
      volatilityLookback: 60,
      factorExposureLimits: [],
    },
    costs: {
      commissionBps: 2.5,
      slippageBps: 3,
      sellTaxBps: 5,
      transferBps: 0.1,
      minCommission: 5,
      borrowAnnualBps: 300,
    },
    dataBindings: { pcd: {} },
  };
}

export function isStatistical(strategy) {
  return strategy?.schemaVersion === 2 && strategy?.research?.mode === RESEARCH_MODE;
}

export function normalizeStrategy(raw) {
  raw = structuredClone(raw);
  const base = defaultStrategy(),
    result = { ...base, ...raw };
  for (const key of [
    'research',
    'universe',
    'preprocess',
    'target',
    'model',
    'validation',
    'execution',
    'portfolio',
    'costs',
    'dataBindings',
  ])
    result[key] = { ...base[key], ...raw[key] };
  result.factors = Array.isArray(raw.factors)
    ? raw.factors.map((f) => ({
        ...f,
        id: String(f.id || ''),
        expression: String(f.expression || ''),
        direction: Number(f.direction) === -1 ? -1 : 1,
      }))
    : [];
  result.universe.symbols = Array.isArray(result.universe.symbols)
    ? [...new Set(result.universe.symbols.map(String))]
    : [];
  delete result.graph;
  result.name = String(result.name || base.name).slice(0, 80);
  return result;
}

export function validateStrategy(
  strategy,
  { includeData = false, dataSource = 'tushare', dataset = null, session = null } = {}
) {
  const errors = [],
    s = strategy,
    u = s.universe || {};
  const number = (value, min, max, label, integer = false) => {
    if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < min ||
      value > max ||
      (integer && !Number.isInteger(value))
    )
      errors.push(`${label}需要为 ${min}–${max}${integer ? ' 的整数' : ' 之间的数值'}。`);
  };
  if (!s.name?.trim()) errors.push('填写研究名称。');
  if (!Array.isArray(u.symbols) || u.symbols.length < 1)
    errors.push('请计算股票筛选结果；完整集合至少需要一个成员。');
  else if (
    new Set(u.symbols).size !== u.symbols.length ||
    u.symbols.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x))
  )
    errors.push('成员不能重复，且代码需为 600000.SH 或 000001.SZ 格式。');
  const date = (value) => {
    if (!/^\d{8}$/.test(value || '')) return null;
    const d = new Date(`${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}T00:00:00Z`);
    return Number.isFinite(+d) && d.toISOString().slice(0, 10).replaceAll('-', '') === value
      ? d
      : null;
  };
  const start = date(u.start),
    end = date(u.end);
  if (!start || !end || start >= end) errors.push('填写有效且递增的研究日期。');
  else if (end - start > 366 * 8 * 86400000) errors.push('研究日期范围最多 8 年。');
  const evidenceKeys = ['catalogSnapshot', 'resolutionHash', 'snapshotHash', 'subsetPolicy'];
  if (!u.selection && evidenceKeys.some((key) => u[key] !== undefined))
    errors.push('股票池版本证据需要完整集合规则；请重新解析股票池或重新导入明确成员。');
  if (u.selection) {
    if (
      !/^[a-f0-9]{64}$/.test(u.snapshotHash || '') ||
      !/^[a-f0-9]{64}$/.test(u.resolutionHash || '')
    )
      errors.push('股票池规则变化后需重新计算完整集合。');
    if (!['all', 'explicit'].includes(u.subsetPolicy))
      errors.push('解析集合后，需要保存完整结果与集合规则。');
    if (u.catalogSnapshot !== undefined) {
      const snap = u.catalogSnapshot;
      const unknown =
        snap && typeof snap === 'object' && !Array.isArray(snap)
          ? Object.keys(snap).filter(
              (key) => !['hash', 'asOf', 'historicalMembershipVerified'].includes(key)
            )
          : [];
      if (!snap || typeof snap !== 'object' || Array.isArray(snap) || unknown.length)
        errors.push(
          '股票池目录快照包含无效或旧版字段' +
            (unknown.length ? '：' + unknown.join('、') : '') +
            '。请在研究范围重新计算完整集合；不会静默删除未知字段。'
        );
      else if (
        !/^[a-f0-9]{64}$/.test(snap.hash || '') ||
        (snap.historicalMembershipVerified !== undefined &&
          snap.historicalMembershipVerified !== false) ||
        (snap.asOf !== undefined &&
          snap.asOf !== null &&
          (typeof snap.asOf !== 'string' || snap.asOf.length > 80))
      )
        errors.push('股票池目录快照证据无效，请重新计算完整集合。');
    }
  }
  if (
    !Array.isArray(s.factors) ||
    s.factors.length > 32 ||
    s.factors.some(
      (f) =>
        !f.id ||
        !f.expression ||
        ![1, -1].includes(f.direction) ||
        !['predictor', 'hedge', 'event'].includes(f.role || 'predictor')
    ) ||
    new Set(s.factors.map((f) => f.id)).size !== s.factors.length
  )
    errors.push('最多 32 个不同因子；每个因子需有定义、公式、方向和有效角色。');
  if (!['asset_price', 'frozen_basket'].includes(s.target?.kind))
    errors.push('选择有计量定义的预测目标。');
  number(s.target?.horizonSessions, 1, 60, '预测期限', true);
  number(s.research?.observationDays, 1, 60, '观察间隔', true);
  const b = s.target?.basket || {},
    symbols = b.symbols || [];
  if (s.target?.kind === 'frozen_basket') {
    if (!['pair_ols', 'pca_residual', 'fixed'].includes(b.method))
      errors.push('选择冻结篮子的构造方法。');
    if (new Set(symbols).size !== symbols.length || symbols.some((x) => !u.symbols?.includes(x)))
      errors.push('篮子腿需为已选择研究成员的唯一子集。');
    if (b.method === 'pair_ols' && symbols.length !== 2)
      errors.push('配对篮子需要明确选择恰好两个成员。');
    if (b.method === 'pca_residual') {
      if (symbols.length < 3 || symbols.length > 20)
        errors.push('PCA 状态篮子需要 3–20 个明确成员。');
      number(
        b.components,
        1,
        Math.min(10, Math.max(1, symbols.length - 2)),
        '共同主成分数量',
        true
      );
    }
    number(b.formationDays, 60, 504, '形成窗口', true);
    if (b.method === 'fixed') {
      if (symbols.length < 1 || symbols.length > 20)
        errors.push('固定数量篮子需要 1–20 个明确成员。');
      if (
        Object.keys(b.quantities || {}).length !== symbols.length ||
        symbols.some(
          (x) =>
            typeof b.quantities?.[x] !== 'number' ||
            !Number.isFinite(b.quantities[x]) ||
            Math.abs(b.quantities[x]) > 1e6
        ) ||
        !symbols.some((x) => Number.isFinite(b.quantities?.[x]) && b.quantities[x] !== 0)
      )
        errors.push('固定数量必须与所有篮子腿一一对应，处于 ±1,000,000 且不能全零。');
    }
  }
  if (!FAMILIES[s.model?.family]) errors.push('选择支持的预测模型族。');
  if (!ESTIMATORS[s.model?.estimator]) errors.push('选择支持的估计器。');
  if (
    s.model?.family === 'pair_reversion' &&
    (s.target?.kind !== 'frozen_basket' || b.method !== 'pair_ols')
  )
    errors.push('配对模型族需要两腿 OLS 冻结篮子目标。');
  if (
    s.factors?.some((f) => f.role === 'hedge') &&
    (s.target?.kind !== 'frozen_basket' || b.method !== 'pca_residual')
  )
    errors.push('对冲因子角色仅用于 PCA 冻结篮子。');
  if (
    s.model?.family === 'event' &&
    !s.factors?.some((f) => f.role === 'event' && /\b(?:ext_|pcd_|fd_)/.test(f.expression))
  )
    errors.push('事件模型需要实际 PIT 外部字段的事件角色因子。');
  if (
    s.model?.family === 'fundamental' &&
    !s.factors?.some(
      (f) =>
        (f.role || 'predictor') === 'predictor' &&
        /\b(?:model_fin_[a-z_]+\b|fd_|pcd_|pe\b|pe_ttm\b|pb\b|ps\b|ps_ttm\b|dv_ratio\b|dv_ttm\b|total_mv\b|circ_mv\b)/.test(
          f.expression
        )
    )
  )
    errors.push('基本面模型需要真实基本面预测输入。');
  number(s.model?.trainWindow, 120, 1260, '训练窗口', true);
  number(s.model?.refitDays, 1, 126, '重新拟合间隔', true);
  number(s.validation?.holdoutFraction, 0.1, 0.4, '最终报告占比');
  number(s.validation?.minTrainDates, 40, 252, '最少训练日期', true);
  number(s.validation?.innerFolds, 2, 3, '内层折数', true);
  number(s.validation?.outerFolds, 2, 3, '外层折数', true);
  if (s.validation?.minTrainDates > s.model?.trainWindow)
    errors.push('最少训练日期不能超过训练窗口。');
  if (
    typeof s.execution?.enabled !== 'boolean' ||
    !['long_only', 'long_short'].includes(s.execution?.side) ||
    s.execution?.shorting !== 'theoretical'
  )
    errors.push('选择有效的独立执行方向与理论借券模式。');
  number(s.execution?.minEdgeBps, 0, 10000, '剩余预期 edge');
  number(s.execution?.maxPositions, 1, 50, '最多目标持仓', true);
  number(s.portfolio?.initialCapital, 10000, 1e9, '研究资金');
  number(s.portfolio?.grossExposure, 0.1, 2, '总敞口');
  number(s.portfolio?.maxWeight, 0.01, 1, '单标的权重上限');
  number(s.portfolio?.rebalanceDays, 1, 60, '调仓间隔', true);
  number(s.portfolio?.rebalanceThresholdBps, 0, 10000, '最小权重变化');
  number(s.portfolio?.netExposureLimit ?? 2, 0, 2, '净敞口上限');
  number(s.portfolio?.targetAnnualVolatility ?? 0.1, 0.01, 1, '目标年化波动');
  number(s.portfolio?.volatilityLookback ?? 60, 20, 252, '波动估计窗口', true);
  if (!['fixed', 'volatility_target'].includes(s.portfolio?.sizingMode || 'fixed'))
    errors.push('选择有效的仓位缩放方式。');
  const limits = s.portfolio?.factorExposureLimits || [];
  if (
    new Set(limits.map((x) => x.factorId)).size !== limits.length ||
    limits.some((x) => !s.factors.some((f) => f.id === x.factorId))
  )
    errors.push('暴露约束必须引用本研究中的不同因子。');
  for (const limit of limits) number(limit.maxAbsExposure, 0, 5, '因子暴露上限');
  for (const [key, max, label] of [
    ['commissionBps', 100, '佣金'],
    ['slippageBps', 200, '滑点'],
    ['sellTaxBps', 100, '卖出税费'],
    ['transferBps', 100, '过户费'],
    ['minCommission', 1000, '最低佣金'],
    ['borrowAnnualBps', 10000, '年化借券费'],
  ])
    number(s.costs?.[key], 0, max, label);
  number(s.preprocess?.correlationThreshold, 0.5, 1, '相关阈值');
  if (includeData && dataSource === 'upload' && !dataset) errors.push('请先导入研究数据。');
  if (includeData && dataSource === 'tushare' && !session?.capabilities?.tushareHosted)
    errors.push('Tushare 接入当前不可用，等待恢复或明确选择导入数据。');
  return errors;
}

export function describeFactor(text) {
  return String(text || '用于条件预测的数值输入。')
    .replaceAll('反向排序假设', '反向编码状态')
    .replace('，偏好较低历史波动', '，作为历史风险状态输入')
    .replace('，偏好稳定成交', '，刻画成交稳定性')
    .replace('，较少极端损失得分较高', '，刻画下行极端变化')
    .replace('方向是研究假设，非投资结论', '方向仅改变输入的符号编码，不决定仓位');
}
