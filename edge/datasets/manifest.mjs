/** Exact version-2 typed graph; old dataset/1 is deliberately not widened. */
import { parseStrictJson } from '../bundles/json.mjs';
import {
  PROFILE,
  LIMITS,
  protocol,
  object,
  hash,
  id,
  integer,
  bytes,
  hashBytes,
  canonical,
  fail,
} from './common.mjs';
const rootsByType = {
  snapshot_scope_origin: [
    'sourceBundleId',
    'sourceSnapshotSha256',
    'marketRoot',
  ],
  registry_evidence: [],
  market_dataset: ['marketRoot'],
  financial_input: ['inputRoot', 'packRoot'],
  financial_prepared: ['packRoot', 'preparedRoot', 'calendarRoot'],
  research_rows: ['financialDatasetRoot'],
  dataset_schema: [],
  dataset_coverage: [],
};
const same = (a, b) => canonical(a) === canonical(b);
export async function validateDatasetManifest(text, expectedRoot, spec) {
  hash(expectedRoot);
  if (
    typeof text !== 'string' ||
    bytes(text).length > LIMITS.manifestBytes ||
    (await hashBytes(bytes(text))) !== expectedRoot
  )
    fail('DATASET_ROOT', '清单原字节与数据集根不匹配');
  const m = parseStrictJson(text);
  object(m, [
    'format',
    'version',
    'profile',
    'scope',
    'marketCalendarRef',
    'financialSources',
    'components',
    'roots',
  ]);
  if (
    m.format !== protocol.datasetFormat ||
    m.version !== 2 ||
    m.profile !== PROFILE ||
    !same(m.scope, spec.sources.market.scope) ||
    m.marketCalendarRef !== spec.sources.marketCalendarRef
  )
    fail('DATASET_MANIFEST', '数据集版本、范围或授权日历不匹配');
  object(m.roots, ['marketRoot', 'financialDatasetRoot']);
  Object.values(m.roots).forEach(hash);
  if (
    !Array.isArray(m.components) ||
    !m.components.length ||
    m.components.length > LIMITS.components ||
    !Array.isArray(m.financialSources) ||
    m.financialSources.length !== spec.sources.financial.length
  )
    fail('DATASET_MANIFEST', '组件或财务来源数量不匹配');
  const components = new Map(),
    seenRoots = new Map();
  let total = bytes(text).length,
    partCount = 0,
    packageBytes = 0;
  for (const c of m.components) {
    object(c, [
      'componentId',
      'type',
      'version',
      'componentRoot',
      'semanticRoots',
      'encoding',
      'payloadSha256',
      'byteLength',
      'dependencies',
      'parts',
    ]);
    if (
      typeof c.componentId !== 'string' ||
      !/^[a-z][A-Za-z0-9]{0,39}$/.test(c.componentId) ||
      components.has(c.componentId) ||
      !rootsByType[c.type] ||
      c.version !== 1 ||
      c.encoding !== 'raw_bytes'
    )
      fail('DATASET_MANIFEST', '组件身份或编码未登记');
    object(c.semanticRoots, rootsByType[c.type]);
    Object.values(c.semanticRoots).forEach(hash);
    hash(c.payloadSha256);
    hash(c.componentRoot);
    const projection = { ...c };
    delete projection.componentRoot;
    if (
      (await hashBytes(bytes(canonical(projection)))) !== c.componentRoot ||
      seenRoots.has(c.componentRoot)
    )
      fail('DATASET_ROOT', '组件根不匹配或重复');
    if (
      !Array.isArray(c.dependencies) ||
      !same(c.dependencies, [...new Set(c.dependencies)].sort()) ||
      c.dependencies.some((x) => !seenRoots.has(x))
    )
      fail('DATASET_GRAPH', '依赖必须为前序的唯一组件');
    const depth = c.dependencies.length
      ? 1 + Math.max(...c.dependencies.map((x) => seenRoots.get(x)))
      : 0;
    if (depth > 3) fail('DATASET_GRAPH', '闭包依赖过深');
    integer(
      c.byteLength,
      1,
      ['market_dataset', 'research_rows', 'financial_input'].includes(c.type)
        ? LIMITS.packageBytes
        : LIMITS.closureBytes,
    );
    if (!Array.isArray(c.parts) || !c.parts.length)
      fail('DATASET_PART', '非空组件必须包含分片');
    let size = 0;
    c.parts.forEach((p, n) => {
      object(p, ['ordinal', 'byteLength', 'sha256']);
      if (p.ordinal !== n) fail('DATASET_PART', '分片编号不连续');
      integer(p.byteLength, 1, LIMITS.partBytes);
      hash(p.sha256);
      size += p.byteLength;
    });
    if (size !== c.byteLength) fail('DATASET_PART', '组件分片长度不一致');
    total += size;
    partCount += c.parts.length;
    if (c.type === 'financial_input') packageBytes += size;
    if (
      total > LIMITS.closureBytes ||
      partCount > LIMITS.parts ||
      packageBytes > LIMITS.packageBytes
    )
      fail('DATASET_BUDGET', '闭包共同预算超限', 413);
    components.set(c.componentId, c);
    seenRoots.set(c.componentRoot, depth);
  }
  const fixed = {
    registryEvidence: 'registry_evidence',
    marketOrigin: 'snapshot_scope_origin',
    marketDataset: 'market_dataset',
    researchRows: 'research_rows',
    schema: 'dataset_schema',
    coverage: 'dataset_coverage',
  };
  m.financialSources.forEach((r, n) => {
    object(r, ['componentId', 'calendarRef', 'proofRefs', 'preparedRoot']);
    const source = spec.sources.financial[n];
    if (
      r.componentId !== `financialInput${n}` ||
      r.calendarRef !== source.calendarRef ||
      !same(r.proofRefs, source.proofRefs) ||
      r.preparedRoot !== source.roots.preparedRoot
    )
      fail('DATASET_SOURCE_CHANGED', '财务组成来源与冻结计划不一致');
    fixed[`financialInput${n}`] = 'financial_input';
    fixed[`financialPrepared${n}`] = 'financial_prepared';
  });
  if (
    !same([...components.keys()].sort(), Object.keys(fixed).sort()) ||
    Object.entries(fixed).some(([n, t]) => components.get(n).type !== t)
  )
    fail('DATASET_MANIFEST', '缺少或增加未登记组件');
  const root = (n) => components.get(n).componentRoot,
    dependencies = {
      registryEvidence: [],
      marketOrigin: [],
      marketDataset: [root('registryEvidence'), root('marketOrigin')].sort(),
    };
  const origin = components.get('marketOrigin').semanticRoots;
  if (
    origin.sourceBundleId !== spec.sources.market.bundleId ||
    origin.sourceSnapshotSha256 !== spec.sources.market.snapshot.sha256 ||
    origin.marketRoot !== m.roots.marketRoot
  )
    fail('DATASET_SOURCE_CHANGED', '原行情身份与组成计划不一致');
  m.financialSources.forEach((_, n) => {
    const a = components.get(`financialInput${n}`),
      b = components.get(`financialPrepared${n}`),
      s = spec.sources.financial[n];
    if (
      !same(a.semanticRoots, {
        inputRoot: s.roots.inputRoot,
        packRoot: s.roots.packRoot,
      }) ||
      !same(b.semanticRoots, {
        packRoot: s.roots.packRoot,
        preparedRoot: s.roots.preparedRoot,
        calendarRoot: s.roots.calendarRoot,
      }) ||
      a.payloadSha256 !== s.package.sha256 ||
      a.byteLength !== s.package.byteLength
    )
      fail('DATASET_SOURCE_CHANGED', '财务原始包或准备根发生改变');
    dependencies[a.componentId] = [root('registryEvidence')];
    dependencies[b.componentId] = [a.componentRoot];
  });
  const derived = [
    root('marketDataset'),
    ...m.financialSources.map((_, n) => root(`financialPrepared${n}`)),
  ].sort();
  for (const n of ['researchRows', 'schema', 'coverage'])
    dependencies[n] = derived;
  for (const [n, d] of Object.entries(dependencies))
    if (!same(components.get(n).dependencies, d))
      fail('DATASET_GRAPH', '组件依赖与类型合同不一致');
  if (
    components.get('marketDataset').semanticRoots.marketRoot !==
      m.roots.marketRoot ||
    components.get('researchRows').semanticRoots.financialDatasetRoot !==
      m.roots.financialDatasetRoot
  )
    fail('DATASET_ROOT', '派生根与清单不一致');
  // Coverage is an index document, never the full values/lineage closure.
  if (components.get('coverage').byteLength > LIMITS.sourceChunkBytes)
    fail('DATASET_BUDGET', '覆盖索引超过8MiB，完整来源保持不变', 413);
  return { manifest: m, components, totalBytes: total, partCount };
}
