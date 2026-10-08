import { ApiError } from '../errors.mjs';
import { HASH } from '../bundles/profile.mjs';
import { keys, object } from '../bundles/json.mjs';
import { validateManifestLayout } from '../bundles/manifest.mjs';
import { validateStoredStatisticalQuant } from '../statistical-quant/validation.mjs';

export const FINANCIAL_FORMAT = 'atlas.quant.financial_bundle';
export const FINANCIAL_CAPABILITY = FINANCIAL_FORMAT + '/1';
export const FINANCIAL_PROTOCOL = Object.freeze({
  format: FINANCIAL_FORMAT,
  version: 1,
  kinds: ['forecast'],
  extraKeys: ['sourceEvidence'],
  codecs: Object.freeze({
    forecast: 'forecast_json_v1',
    report: 'forecast_json_v1',
    coverage: 'forecast_json_v1',
    snapshot: 'financial_json_v1'
  }),
  snapshotVersion: 2
});
const profiles = {
  1: 'financial_compose_50_v1',
  2: 'financial_snapshot_view_50_v1'
};
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const fail = (message) => {
  throw new ApiError('FINANCIAL_BUNDLE_FORMAT', message);
};
export function same(left, right) {
  const sorted = (value) =>
    Array.isArray(value)
      ? value.map(sorted)
      : object(value)
        ? Object.fromEntries(
            Object.keys(value)
              .sort()
              .map((key) => [key, sorted(value[key])])
          )
        : value;
  return JSON.stringify(sorted(left)) === JSON.stringify(sorted(right));
}
export function validateSourceEvidence(value) {
  keys(value, ['datasetRef', 'admissionProfile'], 'sourceEvidence');
  const ref = value.datasetRef;
  keys(ref, ['datasetId', 'datasetRoot', 'format', 'version'], 'datasetRef');
  if (
    Object.keys(value).length !== 2 ||
    Object.keys(ref).length !== 4 ||
    typeof ref.datasetId !== 'string' ||
    !UUID.test(ref.datasetId) ||
    typeof ref.datasetRoot !== 'string' ||
    !HASH.test(ref.datasetRoot) ||
    ref.format !== 'atlas.quant.research_dataset' ||
    !Number.isInteger(ref.version) ||
    !Object.hasOwn(profiles, ref.version) ||
    value.admissionProfile !== profiles[ref.version]
  )
    fail('财务来源引用无效');
  return value;
}

/** Explicit new format. Legacy validateManifest never accepts this document. */
export async function validateFinancialManifest(text, expectedId = null) {
  const parsed = await validateManifestLayout(text, expectedId, FINANCIAL_PROTOCOL);
  const { manifest, metadata, collections } = parsed;
  validateSourceEvidence(manifest.sourceEvidence);
  const snapshot = metadata.snapshot,
    report = metadata.report,
    forecast = metadata.forecast;
  const snapshotKeys = [
    'schemaVersion',
    'fingerprintVersion',
    'datasetRef',
    'sourceEvidenceClosure',
    'rows',
    'provenance',
    'dataFingerprint',
    'sourceDataFingerprint',
    'financialSourceCommitment'
  ];
  keys(snapshot, snapshotKeys, 'financial snapshot');
  if (
    Object.keys(snapshot).length !== snapshotKeys.length ||
    snapshot.fingerprintVersion !== 'research_input_financial_v1' ||
    snapshot.sourceEvidenceClosure !==
      'separate_research_dataset_v' + manifest.sourceEvidence.datasetRef.version ||
    !same(snapshot.datasetRef, manifest.sourceEvidence.datasetRef) ||
    snapshot.sourceDataFingerprint !== snapshot.provenance.dataFingerprint ||
    report.provenance.dataSha256 !== manifest.dataFingerprint ||
    !object(snapshot.financialSourceCommitment) ||
    !same(report.provenance.financialSourceCommitment, snapshot.financialSourceCommitment) ||
    manifest.documents.snapshot.byteLength > 24 * 1024 * 1024
  )
    fail('金融快照身份、编码或来源证据不一致');
  for (const source of [report.strategy, forecast.sourceStrategy]) {
    if (!object(source) || source.execution?.enabled !== false) fail('金融研究必须显式关闭执行');
    const config = validateStoredStatisticalQuant(source);
    if (
      config.target.kind !== 'asset_price' ||
      config.model.family !== 'fundamental' ||
      config.model.estimator !== 'ridge'
    )
      fail('金融研究配置不符合注册 profile');
  }
  if (
    !same(report.strategy, forecast.sourceStrategy) ||
    report.research.executionOnly !== false ||
    report.metrics !== null ||
    report.execution?.forecastArtifactId !== manifest.forecastArtifactId
  )
    fail('金融结果只能为来源一致的纯预测');
  for (const name of ['equity', 'trades', 'riskLedger', 'decisions'])
    if (!collections.has(name) || collections.get(name).rowCount !== 0)
      fail('金融预测不能附带执行台账');
  return parsed;
}
