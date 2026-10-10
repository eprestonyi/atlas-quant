import test from 'node:test';
import assert from 'node:assert/strict';
import { validateStatisticalQuant } from '../edge/statistical-quant/validation.mjs';
import { assertFactorCapabilities } from '../edge/factor-capabilities.mjs';

export const returnStudy = () => ({
  schemaVersion: 2, name: 'Independent asset returns',
  universe: { symbols: ['000001.SZ', '600000.SH'], start: '20230101', end: '20250930' },
  research: { mode: 'statistical_quant', returnStudy: { schema: 'asset-return-study/1', mode: 'forecast' } },
  target: { kind: 'asset_return', horizonSessions: 5, normalization: { kind: 'none' } },
  model: { family: 'trend', estimator: 'ridge', parameterSharing: 'per_target' },
  factors: [{ id: 'momentum', expression: 'returns(close,20)' }],
  preprocess: { automatic: { schema: 'auto-factor-preprocess/2' } },
  execution: { enabled: false },
});

test('return collection is explicit and every asset owns its parameters', () => {
  const input = returnStudy(), result = validateStatisticalQuant(input);
  assert.deepEqual(result.target, input.target);
  assert.deepEqual(result.research.returnStudy, input.research.returnStudy);
  assert.equal(result.model.parameterSharing, 'per_target');
  assert.equal(result.target.basket, undefined);
  for (const mutate of [
    s => delete s.research.returnStudy,
    s => s.research.returnStudy.mode = 'unknown',
    s => delete s.target.normalization,
    s => s.target.normalization.futureSigma = 1,
    s => s.target.basket = {},
    s => s.model.parameterSharing = 'pooled',
    s => delete s.model.parameterSharing,
    s => s.execution.enabled = true,
    s => s.preprocess.automatic.schema = 'auto-factor-preprocess/1',
    s => s.factors = [],
  ]) {
    const changed = returnStudy(); mutate(changed);
    assert.throws(() => validateStatisticalQuant(changed));
  }
});

test('volatility return normalization has an exact historical scale declaration', () => {
  const s = returnStudy();
  s.target.normalization = { kind: 'trailing_volatility', windowSessions: 60, ddof: 1, horizonScale: 'sqrt_h', minimum: 1e-8 };
  assert.deepEqual(validateStatisticalQuant(s).target.normalization, s.target.normalization);
  for (const [key, value] of Object.entries({ windowSessions: 19, ddof: 0, horizonScale: 'future_realized', minimum: 0 })) {
    const changed = structuredClone(s); changed.target.normalization[key] = value;
    assert.throws(() => validateStatisticalQuant(changed));
  }
});

test('legacy targets never accept or silently drop the return protocol', () => {
  const s = returnStudy(); s.target = { kind: 'asset_price' };
  assert.throws(() => validateStatisticalQuant(s));
  delete s.research.returnStudy;
  assert.equal(validateStatisticalQuant(s).research.returnStudy, undefined);
});

test('annual return horizons are explicit while old price horizons stay unchanged', () => {
  const s = returnStudy(); s.target.horizonSessions = 252;
  assert.equal(validateStatisticalQuant(s).target.horizonSessions, 252);
  s.target.horizonSessions = 253;
  assert.throws(() => validateStatisticalQuant(s));
  delete s.research.returnStudy; s.target = {kind: 'asset_price', horizonSessions: 252};
  assert.throws(() => validateStatisticalQuant(s));
});

test('version alone cannot allow old runners to compute scalar return studies', () => {
  const s = returnStudy(), old = { engineVersion: '99.0.0', factorPreprocessFormats: ['auto-factor-preprocess/2'] };
  assert.throws(() => assertFactorCapabilities(s, old));
  assert.doesNotThrow(() => assertFactorCapabilities(s, { ...old, returnStudyFormats: ['asset-return-study/1'] }));
});
