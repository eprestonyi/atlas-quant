/** Estimator admission is distinct from immutable source-composition admission. */
import { ApiError } from '../errors.mjs';
export const FINANCIAL_AUTO_PROFILE = 'financial_fundamental_auto_50_v1';
export const FINANCIAL_SOURCE_PROFILES = Object.freeze({1:'financial_compose_50_v1',2:'financial_snapshot_view_50_v1'});
export const FINANCIAL_AUTO_LIMITS = Object.freeze({symbols:50,factors:16,calendarDays:366,innerFolds:2,outerFolds:2,minRefitDays:20});
const day = x => typeof x === 'string' && /^\d{8}$/.test(x)
  ? Date.UTC(+x.slice(0,4),+x.slice(4,6)-1,+x.slice(6,8))/86400000 : NaN;
function autoScopeEligible(scope) {
  const span = day(scope?.end)-day(scope?.start);
  return Array.isArray(scope?.symbols) && scope.symbols.length > 0 &&
    scope.symbols.length <= FINANCIAL_AUTO_LIMITS.symbols && Number.isFinite(span) &&
    span >= 0 && span <= FINANCIAL_AUTO_LIMITS.calendarDays;
}
export function financialResearchAdmissions(enabled, availability, scope) {
  const autoEligible = scope === undefined || autoScopeEligible(scope);
  return [
    {profile:FINANCIAL_SOURCE_PROFILES[2],estimator:'ridge',configurationEligible:!!enabled,
     runnerAvailable:!!availability.ridge,sampleStatus:'not_checked'},
    {profile:FINANCIAL_AUTO_PROFILE,estimator:'auto',configurationEligible:!!enabled && autoEligible,
     runnerAvailable:!!availability.auto,sampleStatus:'not_checked',limits:FINANCIAL_AUTO_LIMITS,
     reasonCodes:[...(!enabled ? ['DATASET_RESEARCH_UNAVAILABLE'] : []),
       ...(!autoEligible ? ['DATASET_SCOPE_EXCEEDS_AUTO_PROFILE'] : [])],
     selection:'predeclared_eight_candidates_with_independent_factor_free_baseline'},
  ];
}
export function registeredFinancialProfile(profile, version) {
  return Number.isInteger(version) && Object.hasOwn(FINANCIAL_SOURCE_PROFILES, version) &&
    (profile === FINANCIAL_SOURCE_PROFILES[version] || version === 2 && profile === FINANCIAL_AUTO_PROFILE);
}
export function assertFinancialResearchConfig(strategy, profile, version) {
  const fail = () => { throw new ApiError('DATASET_RESEARCH_PROFILE', '财务模型须匹配明确的版本化研究准入，不能更换来源、机制或开启执行'); };
  if (!registeredFinancialProfile(profile, version) || strategy?.schemaVersion !== 2 ||
      strategy.research?.mode !== 'statistical_quant' || strategy.target?.kind !== 'asset_price' ||
      strategy.model?.family !== 'fundamental' || strategy.model.estimator !== (profile === FINANCIAL_AUTO_PROFILE ? 'auto' : 'ridge') ||
      strategy.execution?.enabled !== false || strategy.universe?.selection ||
      !Array.isArray(strategy.factors) || strategy.factors.some(f => f.role !== 'predictor') ||
      Object.values(strategy.dataBindings || {}).some(x => Object.keys(x || {}).length)) fail();
  if (profile === FINANCIAL_AUTO_PROFILE) {
    if (!autoScopeEligible(strategy.universe) || strategy.factors.length > 16 ||
        strategy.validation.innerFolds !== 2 || strategy.validation.outerFolds !== 2 || strategy.model.refitDays < 20) fail();
  }
  return strategy;
}
