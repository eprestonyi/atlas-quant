// A catalogue definition may appear more than once. Only the instance ID is a
// model column; editing its time coordinate never changes that ID or formula.
export const FACTOR_TIME_SCHEMA = 'asset-return-study/2';
export const isFactorTimeStudy = s => s?.research?.returnStudy?.schema === FACTOR_TIME_SCHEMA;
export const factorOffset = factor => factor?.timing?.offsetSessions;
export function factorTimeLabel(factor) {
  const offset = factorOffset(factor);
  return Number.isInteger(offset) ? offset === 0 ? '[t]' : `[t${offset < 0 ? '−' : '+'}${Math.abs(offset)}]` : '[时点未定义]';
}
export function factorTimeErrors(factors) {
  return factors.flatMap(factor => {
    const t = factor.timing;
    return !t || Object.keys(t).length !== 2 || t.anchor !== 'target_origin' || !Number.isInteger(t.offsetSessions) || Math.abs(t.offsetSessions) > 252
      ? [`${factor.baseFactorId || factor.id}：因子时点须为起点前后 252 个交易日内的整数。`] : [];
  });
}
export function createFactorInstance(definition, offsetSessions = 0) {
  const factor = {
    id: 'f_' + crypto.randomUUID().replaceAll('-', ''),
    baseFactorId: definition.baseFactorId || definition.id,
    expression: definition.expression,
    direction: definition.direction === -1 ? -1 : 1,
    role: definition.role || 'predictor',
    ...(definition.version === undefined ? {} : { version: definition.version }),
    timing: { anchor: 'target_origin', offsetSessions },
  };
  if (factorTimeErrors([factor]).length) throw Error('因子时点超出范围。');
  return factor;
}

export function duplicateFactorInstance(strategy, id) {
  if (!isFactorTimeStudy(strategy)) throw Error('请先复制为统一方程研究。');
  if (strategy.factors.length >= 32) throw Error('最多 32 个因子时间版本。');
  const original = strategy.factors.find(f => f.id === id);
  if (!original) throw Error('找不到这个因子时间版本。');
  const copy = createFactorInstance(original, factorOffset(original));
  strategy.factors.splice(strategy.factors.indexOf(original) + 1, 0, copy);
  const overrides = strategy.preprocess?.automatic?.overrides;
  if (overrides?.[id]) overrides[copy.id] = structuredClone(overrides[id]);
  return copy;
}
