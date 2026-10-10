// Fixed economic transforms. /1 remains readable; /2 is explicit for new studies.
export const AUTOMATIC_FACTOR_SCHEMAS = ['auto-factor-preprocess/1', 'auto-factor-preprocess/2'];
export const LATEST_AUTOMATIC_FACTOR_SCHEMA = AUTOMATIC_FACTOR_SCHEMAS[1];
export const ECONOMIC_TYPES = ['price','log_price','positive_size','nonnegative_flow','valuation_multiple','percent','ratio','currency_per_share','currency_amount','derived','custom_numeric'];
export const ECONOMIC_TRANSFORMS = Object.freeze({
  identity: {kind:'identity'},
  log_positive: {kind:'log_positive',invalid:'missing'},
  log1p_nonnegative: {kind:'log1p_nonnegative',invalid:'missing'},
  reciprocal_nonzero: {kind:'reciprocal_nonzero',invalid:'missing'},
  percent_to_fraction: {kind:'percent_to_fraction',divisor:100},
  return_over_trailing_volatility: {kind:'return_over_trailing_volatility',returnLag:1,volatilityWindow:20,volatilityLag:1,ddof:1,minVolatility:1e-8,invalid:'missing'},
  first_difference: {kind:'first_difference',lag:1,invalid:'missing'},
  simple_return: {kind:'simple_return',lag:1,invalid:'missing'},
  log_return: {kind:'log_return',lag:1,invalid:'missing'},
  signed_log1p: {kind:'signed_log1p',referenceUnit:1,invalid:'missing'}
});
export const ECONOMIC_TRANSFORM_LABELS = Object.freeze({identity:'保留已定义数值',first_difference:'1 期一阶差分',simple_return:'1 期简单收益率',log_return:'1 期对数收益率',return_over_trailing_volatility:'收益 / 滞后 20 期波动',log_positive:'正值取自然对数',log1p_nonnegative:'非负值 ln(1+x)',signed_log1p:'带符号 log1p · 参考值 1 原单位',reciprocal_nonzero:'非零值倒数',percent_to_fraction:'百分数 / 100'});
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
export function validEconomicTransform(value, schema = LATEST_AUTOMATIC_FACTOR_SCHEMA) {
  const canonical = Object.hasOwn(ECONOMIC_TRANSFORMS,value?.kind) ? ECONOMIC_TRANSFORMS[value.kind] : null;
  if (!canonical || !AUTOMATIC_FACTOR_SCHEMAS.includes(schema)) return false;
  if (schema === AUTOMATIC_FACTOR_SCHEMAS[0] && ['simple_return','log_return','signed_log1p','first_difference'].includes(value.kind)) return false;
  return exact(value,Object.keys(canonical)) && Object.keys(canonical).every(key => value[key] === canonical[key]);
}
export function validateAutomaticFactorConfig(value, factors = []) {
  if (!object(value) || !AUTOMATIC_FACTOR_SCHEMAS.includes(value.schema)) throw Error('自动因子处理版本无效');
  const hasOverrides = Object.hasOwn(value,'overrides');
  if (!exact(value, ['schema', ...(hasOverrides ? ['overrides'] : [])]) || (hasOverrides && value.schema !== LATEST_AUTOMATIC_FACTOR_SCHEMA)) throw Error('自动因子处理字段无效');
  if (hasOverrides) {
    if (!object(value.overrides) || Object.keys(value.overrides).length > 32) throw Error('因子处理覆盖配置无效');
    for (const [id, choice] of Object.entries(value.overrides)) {
      const factor = factors.find(f => f.id === id);
      if (!factor || factor.role === 'hedge' || !exact(choice,['transform']) || !validEconomicTransform(choice.transform,value.schema)) throw Error(`因子 ${id} 的处理方法无效`);
    }
  }
  return structuredClone(value);
}
