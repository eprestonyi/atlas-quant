import test from 'node:test';
import assert from 'node:assert/strict';
import { recordIndex } from '../edge/bundles/records.mjs';

const observation = () => ({ schema: 'asset-return-observation/1', forecastId: 'r1',
  date: '20260105', assetSymbol: '000001.SZ', targetId: 'asset1', modelFitId: 'fit1',
  featureDate: '20260105', responseStartDate: '20260105', responseEndDate: '20260106',
  informationCutoff: '20260105_AFTER_CLOSE', status: 'valid', inputValid: true,
  originPrice: 100, responseScale: .02, predictedResponse: .5, predictedReturn: .01,
  conditionalPrice: 101, observedReturn: .012, observedResponse: .6,
  responseResidual: .1, labelMaturedAt: '20260106' });
const options = { returnStudy: { schema: 'asset-return-study/1', mode: 'forecast' } };
const index = (row, opts = options) => recordIndex('forecasts', row, 0, 0, 0, opts);
const panel = () => ({schema:'asset-return-panel-row/1',date:'20260105',assetSymbol:'000001.SZ',targetId:'asset1',inputValid:true,invalidReason:null,featureDate:'20260105',responseStartDate:'20260105',responseEndDate:'20260106',originPrice:100,responseScale:.02,observedReturn:.012,observedResponse:.6,features:{'factor:market':.01,'factor:margin':null}});

test('return observations retain independent endpoints without a fake entry', async () => {
  const entry = await index(observation());
  const meta = JSON.parse(entry.at(-1));
  assert.equal(meta.responseEndDate, '20260106');
  assert.equal(meta.entryDate, undefined);
  for (const mutate of [
    row => row.expectedEntry = 100,
    row => row.predictedReturn = .5,
    row => row.conditionalPrice = 100.5,
    row => row.featureDate = '20260106',
    row => row.responseStartDate = '20260104',
    row => row.responseScale = 0,
    row => row.labelMaturedAt = '20260107',
    row => row.observedReturn = .04,
    row => row.observedResponse = null,
    row => row.responseResidual = .6,
    row => row.informationCutoff = '20260105',
    row => row.informationCutoff = '20260106_AFTER_CLOSE',
    row => row.informationCutoff = '20260105_BEFORE_OPEN',
    row => row.informationCutoff = null,
  ]) { const row = observation(); mutate(row); await assert.rejects(index(row)); }
  await assert.rejects(index(observation(), {}));
});

test('association and future forecast endpoints cannot be interchanged', async () => {
  const row = observation(); row.responseStartDate = '20260102'; row.responseEndDate = row.date;
  row.labelMaturedAt = row.date;
  await assert.rejects(index(row));
  await assert.doesNotReject(index(row, { returnStudy: { schema: 'asset-return-study/1', mode: 'association' } }));
});

test('maturity unknown at the tail preserves a numeric forecast', async () => {
  const row = observation(); row.responseEndDate = row.labelMaturedAt = null;
  row.observedReturn = row.observedResponse = row.responseResidual = null;
  await assert.doesNotReject(index(row));
});

test('every asset-scoped collection indexes target identity', async () => {
  for (const [collection, row] of [
    ['modelFits', { id: 'fit1', targetId: 'asset1' }],
    ['factorFeatures', { targetId: 'asset1', name: 'factor:size' }],
    ['factorJointDistributions', { targetId: 'asset1' }],
    ['outerFolds', { targetId: 'asset1' }],
    ['finalTrials', { id: 'trial1', targetId: 'asset1' }],
  ]) {
    const entry = await recordIndex(collection, row, 0, 0, 0);
    assert.equal(entry[5], 'asset1', collection);
  }
});

test('complete research panel rejects omitted factors, nonfinite values and false valid rows',async () => {
  const opts = {...options,returnFactorNames:['factor:market','factor:margin']};
  const check = row => recordIndex('researchPanel',row,0,0,0,opts);
  const entry = await check(panel()); assert.equal(entry[5],'asset1'); assert.equal(JSON.parse(entry.at(-1)).assetSymbol,'000001.SZ');
  for (const mutate of [
    row => delete row.features['factor:margin'], row => row.features.other = 1,
    row => row.features['factor:margin'] = Infinity, row => row.features['factor:market'] = null,
    row => row.assetSymbol = 'unknown', row => row.schema = 'other', row => row.originPrice = -1,
    row => row.inputValid = false, row => row.responseStartDate = '20250101',
    row => row.observedResponse = .3, row => row.extra = true, row => row.responseEndDate = '20261340'
  ]) {const row = panel(); mutate(row); await assert.rejects(check(row));}
  const missing = panel(); missing.inputValid = false; missing.invalidReason = 'response_normalizer_unavailable'; missing.responseScale = missing.observedResponse = null;
  await assert.doesNotReject(check(missing));
  await assert.rejects(recordIndex('researchPanel',panel(),0,0,0));
});
