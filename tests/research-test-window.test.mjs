/** Explicit dates use the frozen trading calendar; no fitting or provider. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { validateStatisticalQuant } from '../edge/statistical-quant/validation.mjs';
import { marketOriginDomain } from '../edge/market-preparation/coverage.mjs';

const base = {
  schemaVersion: 2, name: 'SYNTHETIC date-boundary unit fixture',
  universe: { symbols: ['000001.SZ'], start: '20240501', end: '20241031' },
  research: { mode: 'statistical_quant', observationDays: 3 }, factors: [],
  target: { kind: 'asset_price', horizonSessions: 5 },
  model: { family: 'mean_reversion', estimator: 'ridge', refitDays: 20 },
  execution: { enabled: false },
};
const calendar = [];
for (let d = new Date('2024-05-01T00:00:00Z'); d <= new Date('2024-10-31T00:00:00Z'); d.setUTCDate(d.getUTCDate() + 1)) {
  const date = d.toISOString().slice(0, 10).replaceAll('-', '');
  if (![0, 6].includes(d.getUTCDay()) && !(date >= '20241001' && date <= '20241007')) calendar.push(date);
}
const make = testStart => validateStatisticalQuant({ ...base, validation: { testStart } });

test('optional explicit date preserves legacy normalized protocol and is idempotent', () => {
  const legacy = validateStatisticalQuant(base);
  assert.equal(Object.hasOwn(legacy.validation, 'testStart'), false);
  assert.deepEqual(validateStatisticalQuant(legacy), legacy);
  const explicit = make('20241001');
  assert.equal(explicit.validation.testStart, '20241001');
  assert.deepEqual(validateStatisticalQuant(explicit), explicit);
});

test('schema rejects malformed, impossible and out-of-scope dates', () => {
  for (const value of [null, true, 20241001, '', '2024-10-01', '20240931', '20240430', '20241101'])
    assert.throws(() => make(value), undefined, String(value));
  assert.equal(make(base.universe.start).validation.testStart, base.universe.start);
  assert.equal(make(base.universe.end).validation.testStart, base.universe.end);
});

test('general pair research accepts date controls without changing the basket', () => {
  const pair = validateStatisticalQuant({
    ...base, universe: { ...base.universe, symbols: ['000001.SZ', '600000.SH'] },
    model: { family: 'pair_reversion', estimator: 'ridge' },
    target: { kind: 'frozen_basket', basket: { method: 'pair_ols', symbols: ['000001.SZ', '600000.SH'] } },
    validation: { testStart: '20241001' },
  });
  assert.equal(pair.validation.testStart, '20241001');
  assert.equal(pair.target.basket.method, 'pair_ols');
});

test('holiday boundary advances to a real session without resetting observation stride', async () => {
  const s = make('20241001'), domain = await marketOriginDomain(s, calendar, s.universe.symbols);
  assert.equal(domain.holdoutStart, '20241008');
  assert.deepEqual(domain.origins.map(x => x.date), calendar.filter((date, i) => i >= 61 && (i - 61) % 3 === 0 && date >= '20241008'));
  assert.equal(domain.origins.at(-1).targetDate, null);
  const alteredFraction = await marketOriginDomain({ ...s, validation: { ...s.validation, holdoutFraction: 0.4 } }, calendar, s.universe.symbols);
  assert.deepEqual(alteredFraction, domain);
});

test('legacy fraction boundary remains unchanged and explicit limits use eligible sessions', async () => {
  const legacy = validateStatisticalQuant(base);
  const old = await marketOriginDomain(legacy, calendar, legacy.universe.symbols);
  assert.equal(old.holdoutStart, calendar[61 + Math.floor((calendar.length - 61) * 0.8)]);
  for (const date of [calendar[62], calendar.at(-10)]) {
    const s = make(date);
    assert.equal((await marketOriginDomain(s, calendar, s.universe.symbols)).holdoutStart, date);
  }
  for (const date of [base.universe.start, calendar[61], calendar.at(-9), base.universe.end]) {
    const s = make(date);
    await assert.rejects(marketOriginDomain(s, calendar, s.universe.symbols), /完整报告窗口/);
  }
});

test('coverage refuses malformed explicit dates even before schema normalization', async () => {
  for (const date of [null, '20240931', '20241101']) {
    const s = { ...base, validation: { testStart: date, holdoutFraction: 0.2 } };
    await assert.rejects(marketOriginDomain(s, calendar, s.universe.symbols), /YYYYMMDD/);
  }
});
