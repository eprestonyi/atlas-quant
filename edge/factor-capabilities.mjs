/** Explicit execution contracts: version strings alone do not authorize new inputs. */
import {ApiError} from './errors.mjs';
export const supportsAutomaticFactors = runner => (Array.isArray(runner?.factorPreprocessFormats) ? runner.factorPreprocessFormats : []).includes('auto-factor-preprocess/1') === true;
export const supportsContextSources = runner => (Array.isArray(runner?.contextSourceFormats) ? runner.contextSourceFormats : []).includes('named-index-history/1') === true;
export const needsAutomaticFactors = strategy => strategy?.preprocess?.automatic !== undefined;
export const needsContextSources = strategy => strategy?.factors?.some(f => /\bext_ctx_/.test(f.expression)) === true;
export function assertFactorCapabilities(strategy, runner) {
  if ((needsAutomaticFactors(strategy) && !supportsAutomaticFactors(runner)) ||
      (needsContextSources(strategy) && !supportsContextSources(runner)))
    throw new ApiError('RUNNER_UPGRADE_REQUIRED', '本次因子需要兼容的自动处理与指数数据服务', 409);
}
