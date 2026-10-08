/** Explicit financial bundle/2. Old bundle/1 parsers never admit these columns. */
import { ApiError } from '../errors.mjs';
import { COLLECTION_PATHS, HASH, SENSITIVE_KEY } from '../bundles/profile.mjs';
import { keys, object, byteLength } from '../bundles/json.mjs';
import { validateManifestLayout } from '../bundles/manifest.mjs';
import { validateStoredStatisticalQuant } from '../statistical-quant/validation.mjs';
import {
  assertFinancialResearchConfig,
  FINANCIAL_GRAPH_PROFILE
} from '../datasets/research-profile.mjs';
import { same, FINANCIAL_FORMAT } from '../financial-bundles/manifest.mjs';
export { FINANCIAL_FORMAT };
const MIB = 1024 * 1024;
export const FINANCIAL_GRAPH_PROTOCOL = Object.freeze({
  format: FINANCIAL_FORMAT,
  version: 2,
  kinds: ['forecast'],
  extraKeys: ['sourceEvidence'],
  codecs: Object.freeze({
    forecast: 'forecast_json_v1',
    report: 'forecast_json_v1',
    coverage: 'forecast_json_v1',
    snapshot: 'financial_column_snapshot_v1'
  }),
  snapshotVersion: 3,
  snapshotCollection: 'snapshotColumns',
  snapshotRowCountPath: '/numericInput/rowCount',
  collectionPaths: Object.freeze({
    ...Object.fromEntries(Object.entries(COLLECTION_PATHS).filter(([id]) => id !== 'snapshotRows')),
    snapshotColumns: ['snapshot', '/numericInput/columns']
  })
});
const fail = (message) => {
  throw new ApiError('FINANCIAL_GRAPH_BUNDLE_FORMAT', message);
};
const exact = (value, allowed, label) => {
  keys(value, allowed, label);
  if (Object.keys(value).length !== allowed.length) fail(label + '缺少字段');
};
const count = (n, min, max) => Number.isSafeInteger(n) && n >= min && n <= max;
export function validateGraphSourceEvidence(value) {
  exact(value, ['datasetRef', 'admissionProfile'], 'sourceEvidence');
  const r = value.datasetRef;
  exact(r, ['datasetId', 'datasetRoot', 'format', 'version'], 'datasetRef');
  if (
    typeof r.datasetId !== 'string' ||
    !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(r.datasetId) ||
    typeof r.datasetRoot !== 'string' ||
    !HASH.test(r.datasetRoot) ||
    r.format !== 'atlas.quant.research_dataset' ||
    r.version !== 3 ||
    value.admissionProfile !== FINANCIAL_GRAPH_PROFILE
  )
    fail('图来源引用无效');
}
export async function validateFinancialGraphManifest(text, id = null) {
  const parsed = await validateManifestLayout(text, id, FINANCIAL_GRAPH_PROTOCOL);
  const { manifest, metadata, collections } = parsed;
  validateGraphSourceEvidence(manifest.sourceEvidence);
  const s = metadata.snapshot,
    r = metadata.report,
    f = metadata.forecast;
  exact(
    s,
    [
      'schemaVersion',
      'fingerprintVersion',
      'datasetRef',
      'sourceEvidenceClosure',
      'numericInput',
      'provenance',
      'dataFingerprint',
      'sourceDataFingerprint',
      'financialSourceCommitment'
    ],
    'graph snapshot'
  );
  exact(
    s.numericInput,
    ['format', 'version', 'rowCount', 'columns', 'logicalRows'],
    'numericInput'
  );
  exact(s.numericInput.logicalRows, ['sha256', 'byteLength'], 'logicalRows');
  if (
    s.fingerprintVersion !== 'research_input_financial_column_v1' ||
    s.sourceEvidenceClosure !== 'separate_research_dataset_v3' ||
    !same(s.datasetRef, manifest.sourceEvidence.datasetRef) ||
    !HASH.test(s.sourceDataFingerprint) ||
    s.sourceDataFingerprint !== s.provenance.dataFingerprint ||
    r.provenance.dataSha256 !== manifest.dataFingerprint ||
    !object(s.financialSourceCommitment) ||
    !same(r.provenance.financialSourceCommitment, s.financialSourceCommitment) ||
    manifest.documents.snapshot.byteLength > 24 * MIB ||
    s.numericInput.format !== 'atlas.quant.exact_column_table' ||
    s.numericInput.version !== 1 ||
    !count(collections.get('snapshotColumns').rowCount, 1, 128) ||
    !HASH.test(s.numericInput.logicalRows.sha256) ||
    !count(s.numericInput.logicalRows.byteLength, 2, 24 * MIB)
  )
    fail('图快照版本、维度或来源身份不一致');
  for (const source of [r.strategy, f.sourceStrategy]) {
    if (!object(source) || source.execution?.enabled !== false) fail('金融研究必须明确关闭执行');
    assertFinancialResearchConfig(
      validateStoredStatisticalQuant(source),
      FINANCIAL_GRAPH_PROFILE,
      3
    );
  }
  if (
    !same(r.strategy, f.sourceStrategy) ||
    r.research.executionOnly !== false ||
    r.metrics !== null ||
    r.execution?.forecastArtifactId !== manifest.forecastArtifactId
  )
    fail('图结果仅支持来源一致的纯预测');
  for (const name of ['equity', 'trades', 'riskLedger', 'decisions'])
    if (!collections.has(name) || collections.get(name).rowCount !== 0) fail('图结果不得附带执行');
  if (!collections.has('hedgeFits')) fail('图结果缺少完整构造记录');
  return parsed;
}
/** Bounded one-chunk structural validation, preserving raw numeric spelling. */
export function validateGraphRows(collection, rows, parsed) {
  if (collection !== 'snapshotColumns') return;
  const n = parsed.metadata.snapshot.numericInput.rowCount;
  for (const c of rows) {
    if (
      typeof c.name !== 'string' ||
      !/^[A-Za-z_][A-Za-z0-9_]{0,95}$/.test(c.name) ||
      SENSITIVE_KEY.test(c.name)
    )
      fail('列身份无效或包含敏感名称');
    if (c.kind === 'number') {
      exact(c, ['name', 'kind', 'values'], 'number column');
      if (
        !Array.isArray(c.values) ||
        c.values.length !== n ||
        c.values.some((v) => v !== null && (typeof v !== 'number' || !Number.isFinite(v)))
      )
        fail('数值列缺行或非有限值');
    } else {
      exact(c, ['name', 'kind', 'dictionary', 'indices'], 'dictionary column');
      if (
        c.kind !== 'dictionary' ||
        !Array.isArray(c.dictionary) ||
        !count(c.dictionary.length, 1, n) ||
        c.dictionary.some((v) => v !== null && (typeof v !== 'string' || byteLength(v) > 256)) ||
        new Set(c.dictionary).size !== c.dictionary.length ||
        !Array.isArray(c.indices) ||
        c.indices.length !== n
      )
        fail('字典列形状无效');
      const seen = new Set();
      for (const i of c.indices) {
        if (!count(i, 0, c.dictionary.length - 1)) fail('字典索引越界');
        if (!seen.has(i)) {
          if (i !== seen.size) fail('字典必须按首次出现排列');
          seen.add(i);
        }
      }
      if (seen.size !== c.dictionary.length) fail('字典含未使用成员');
    }
  }
}
