/** Explicit execution contracts: version strings alone do not authorize new inputs. */
import {ApiError} from './errors.mjs';
export const supportsAutomaticFactors = (runner, strategy) => (Array.isArray(runner?.factorPreprocessFormats) ? runner.factorPreprocessFormats : []).includes(strategy?.preprocess?.automatic?.schema || 'auto-factor-preprocess/1') === true;
export const supportsContextSources = runner => (Array.isArray(runner?.contextSourceFormats) ? runner.contextSourceFormats : []).includes('named-index-history/1') === true;
export const supportsForeignContextSources = runner => (Array.isArray(runner?.contextSourceFormats) ? runner.contextSourceFormats : []).includes('named-market-history/2') === true;
export const supportsYahooContextSources = runner => (Array.isArray(runner?.contextSourceFormats) ? runner.contextSourceFormats : []).includes('named-market-history/3') === true;
export const needsYahooContextSources = strategy => strategy?.factors?.some(f => /\bext_ctx_yf_/.test(f.expression)) === true;
export const supportsModelSearch = runner => (Array.isArray(runner?.functionSearchFormats) ? runner.functionSearchFormats : []).includes('factor-model-search/1') === true;
export const needsModelSearch = strategy => strategy?.model?.search !== undefined;
export const needsAutomaticFactors = strategy => strategy?.preprocess?.automatic !== undefined;
export const needsContextSources = strategy => strategy?.factors?.some(f => /\bext_ctx_/.test(f.expression)) === true;
export const needsForeignContextSources = strategy => strategy?.factors?.some(f => /\bext_ctx_[a-z]/.test(f.expression)) === true;
export function assertFactorCapabilities(strategy, runner) {
  if ((needsAutomaticFactors(strategy) && !supportsAutomaticFactors(runner, strategy)) ||
      (needsContextSources(strategy) && !supportsContextSources(runner)) ||
      (needsForeignContextSources(strategy) && !supportsForeignContextSources(runner)) ||
      (needsYahooContextSources(strategy) && !supportsYahooContextSources(runner)) ||
      (needsModelSearch(strategy) && !supportsModelSearch(runner)))
    throw new ApiError('RUNNER_UPGRADE_REQUIRED', '本次因子需要兼容的自动处理与指数数据服务', 409);
}
