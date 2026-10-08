/** Closed dataset/3 graph. This validator never broadens the legacy dataset path. */
import protocol from '../../contracts/hosted-dataset-graphs-v1.json' with { type: 'json' };
import { parseStrictJson } from '../bundles/json.mjs';
import { object, hash, id, integer, bytes, hashBytes, fail } from '../financial/common.mjs';
import { canonical } from '../datasets/common.mjs';

const LIMITS = protocol.limits;
const rootsByType = {
  snapshot_scope_origin: ['sourceBundleId', 'sourceSnapshotSha256', 'marketRoot'],
  registry_evidence: [],
  market_dataset: ['marketRoot'],
  financial_input: ['inputRoot', 'packRoot'],
  financial_prepared_graph: ['packRoot', 'preparedRoot', 'calendarRoot', 'preparedPayloadSha256'],
  research_columns: ['financialDatasetRoot', 'logicalJoinedSha256'],
  dataset_schema: [],
  dataset_coverage: []
};
const same = (a, b) => canonical(a) === canonical(b);

function scope(value) {
  object(value, ['symbols', 'start', 'end']);
  if (
    !Array.isArray(value.symbols) ||
    !value.symbols.length ||
    value.symbols.length > LIMITS.symbols ||
    value.symbols.some((s) => typeof s !== 'string' || !/^\d{6}\.(SH|SZ)$/.test(s)) ||
    !same(value.symbols, [...new Set(value.symbols)].sort())
  )
    fail('DATASET_SCOPE', 'Graph scope requires a sorted exact 1–50 symbol universe');
  const day = (text) => {
    if (typeof text !== 'string' || !/^\d{8}$/.test(text))
      fail('DATASET_SCOPE', 'Graph dates must be exact YYYYMMDD');
    const year = Number(text.slice(0, 4)),
      month = Number(text.slice(4, 6)),
      date = Number(text.slice(6));
    const checked = new Date(0);
    checked.setUTCFullYear(year, month - 1, date);
    if (
      year < 1 ||
      checked.getUTCFullYear() !== year ||
      checked.getUTCMonth() !== month - 1 ||
      checked.getUTCDate() !== date
    )
      fail('DATASET_SCOPE', 'Graph dates must be valid calendar dates');
    return checked.getTime();
  };
  const start = day(value.start),
    end = day(value.end);
  if (end < start || (end - start) / 86400000 + 1 > 366)
    fail('DATASET_SCOPE', 'Graph scope exceeds 366 inclusive calendar days');
}

export async function validateGraphDatasetManifest(text, expectedRoot, spec) {
  hash(expectedRoot);
  if (
    typeof text !== 'string' ||
    !text.length ||
    bytes(text).length > LIMITS.manifestBytes ||
    (await hashBytes(bytes(text))) !== expectedRoot
  )
    fail('DATASET_ROOT', 'Manifest original bytes differ from the dataset root');
  // Every numeric field in a manifest is an integer. The strict scanner rejects
  // integral float tokens before JSON.parse could erase their Python type.
  const m = parseStrictJson(text);
  object(m, [
    'format',
    'version',
    'profile',
    'scope',
    'marketCalendarRef',
    'financialSources',
    'components',
    'roots'
  ]);
  if (
    m.format !== protocol.datasetFormat ||
    m.version !== 3 ||
    m.profile !== protocol.profile ||
    spec?.profile !== protocol.profile ||
    spec.request?.profile !== protocol.profile
  )
    fail('DATASET_MANIFEST', 'Graph dataset version or frozen plan profile differs');
  scope(m.scope);
  id(m.marketCalendarRef);
  if (
    !same(m.scope, spec.sources?.market?.scope) ||
    m.marketCalendarRef !== spec.sources?.marketCalendarRef
  )
    fail(
      'DATASET_SOURCE_CHANGED',
      'Scope or authorized market calendar differs from the frozen plan'
    );
  object(m.roots, ['marketRoot', 'financialDatasetRoot']);
  Object.values(m.roots).forEach(hash);
  if (
    !Array.isArray(m.financialSources) ||
    !m.financialSources.length ||
    m.financialSources.length > LIMITS.financialInputs ||
    !Array.isArray(spec.sources.financial) ||
    m.financialSources.length !== spec.sources.financial.length ||
    !Array.isArray(m.components) ||
    !m.components.length ||
    m.components.length > LIMITS.components
  )
    fail('DATASET_BUDGET', 'Graph source or component count exceeds the registered profile');
  const components = new Map(),
    seenRoots = new Map();
  let totalBytes = bytes(text).length,
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
      'parts'
    ]);
    if (
      typeof c.componentId !== 'string' ||
      !/^[a-z][A-Za-z0-9]{0,39}$/.test(c.componentId) ||
      components.has(c.componentId) ||
      typeof c.type !== 'string' ||
      !Object.hasOwn(rootsByType, c.type) ||
      c.version !== 1 ||
      c.encoding !== 'raw_bytes'
    )
      fail('DATASET_MANIFEST', 'Unregistered graph component identity or encoding');
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
      fail('DATASET_ROOT', 'Graph component root changed or is duplicated');
    if (
      !Array.isArray(c.dependencies) ||
      !same(c.dependencies, [...new Set(c.dependencies)].sort()) ||
      c.dependencies.some((root) => typeof root !== 'string' || !seenRoots.has(root))
    )
      fail('DATASET_GRAPH', 'Dependencies must be unique sorted earlier component roots');
    const depth = c.dependencies.length
      ? 1 + Math.max(...c.dependencies.map((root) => seenRoots.get(root)))
      : 0;
    if (depth > 3) fail('DATASET_GRAPH', 'Graph dependency depth exceeds the registered profile');
    const ceiling = ['market_dataset', 'financial_input', 'research_columns'].includes(c.type)
      ? LIMITS.packageBytes
      : LIMITS.closureBytes;
    integer(c.byteLength, 1, ceiling);
    if (!Array.isArray(c.parts) || !c.parts.length || c.parts.length > LIMITS.parts)
      fail('DATASET_BUDGET', 'Nonempty bounded graph parts are required');
    let size = 0;
    c.parts.forEach((p, n) => {
      object(p, ['ordinal', 'byteLength', 'sha256']);
      if (p.ordinal !== n) fail('DATASET_PART', 'Graph part ordinals must be contiguous');
      integer(p.byteLength, 1, LIMITS.partBytes);
      hash(p.sha256);
      size += p.byteLength;
    });
    if (size !== c.byteLength) fail('DATASET_PART', 'Graph component part byte count differs');
    totalBytes += size;
    partCount += c.parts.length;
    if (c.type === 'financial_input') packageBytes += size;
    if (
      totalBytes > LIMITS.closureBytes ||
      partCount > LIMITS.parts ||
      packageBytes > LIMITS.packageBytes
    )
      fail('DATASET_BUDGET', 'Graph closure exceeds the shared byte or part budget', 413);
    components.set(c.componentId, c);
    seenRoots.set(c.componentRoot, depth);
  }
  const fixed = {
    registryEvidence: 'registry_evidence',
    marketOrigin: 'snapshot_scope_origin',
    marketDataset: 'market_dataset',
    researchColumns: 'research_columns',
    schema: 'dataset_schema',
    coverage: 'dataset_coverage'
  };
  let previousPackRoot = null;
  m.financialSources.forEach((ref, n) => {
    object(ref, ['componentId', 'calendarRef', 'proofRefs', 'preparedRoot']);
    id(ref.calendarRef);
    hash(ref.preparedRoot);
    if (
      !Array.isArray(ref.proofRefs) ||
      ref.proofRefs.length > 256 ||
      !same(ref.proofRefs, [...new Set(ref.proofRefs)].sort())
    )
      fail('DATASET_SOURCE_CHANGED', 'Graph proof references must be unique sorted bounded UUIDs');
    ref.proofRefs.forEach(id);
    const source = spec.sources.financial[n];
    if (
      ref.componentId !== `financialInput${n}` ||
      ref.calendarRef !== source.calendarRef ||
      !same(ref.proofRefs, source.proofRefs) ||
      ref.preparedRoot !== source.roots.preparedRoot
    )
      fail('DATASET_SOURCE_CHANGED', 'Financial source differs from the frozen graph plan');
    hash(source.roots.packRoot);
    if (previousPackRoot !== null && previousPackRoot >= source.roots.packRoot)
      fail(
        'DATASET_SOURCE_CHANGED',
        'Frozen graph financial packages must be unique and sorted by packRoot'
      );
    previousPackRoot = source.roots.packRoot;
    fixed[`financialInput${n}`] = 'financial_input';
    fixed[`financialGraph${n}`] = 'financial_prepared_graph';
  });
  if (
    !same([...components.keys()].sort(), Object.keys(fixed).sort()) ||
    Object.entries(fixed).some(([name, type]) => components.get(name).type !== type)
  )
    fail('DATASET_MANIFEST', 'Missing or unexpected registered graph component');
  const root = (name) => components.get(name).componentRoot;
  const dependencies = {
    registryEvidence: [],
    marketOrigin: [],
    marketDataset: [root('registryEvidence'), root('marketOrigin')].sort()
  };
  const origin = components.get('marketOrigin').semanticRoots;
  if (
    origin.sourceBundleId !== spec.sources.market.bundleId ||
    origin.sourceSnapshotSha256 !== spec.sources.market.snapshot.sha256 ||
    origin.marketRoot !== m.roots.marketRoot
  )
    fail('DATASET_SOURCE_CHANGED', 'Original market identity differs from the frozen graph plan');
  m.financialSources.forEach((_, n) => {
    const input = components.get(`financialInput${n}`),
      graph = components.get(`financialGraph${n}`),
      source = spec.sources.financial[n];
    if (
      !same(input.semanticRoots, {
        inputRoot: source.roots.inputRoot,
        packRoot: source.roots.packRoot
      }) ||
      graph.semanticRoots.packRoot !== source.roots.packRoot ||
      graph.semanticRoots.preparedRoot !== source.roots.preparedRoot ||
      graph.semanticRoots.calendarRoot !== source.roots.calendarRoot ||
      input.payloadSha256 !== source.package.sha256 ||
      input.byteLength !== source.package.byteLength
    )
      fail('DATASET_SOURCE_CHANGED', 'Original financial package bytes or prepared roots differ');
    dependencies[input.componentId] = [root('registryEvidence')];
    dependencies[graph.componentId] = [input.componentRoot];
  });
  const derived = [
    root('marketDataset'),
    ...m.financialSources.map((_, n) => root(`financialGraph${n}`))
  ].sort();
  for (const name of ['researchColumns', 'schema', 'coverage']) dependencies[name] = derived;
  for (const [name, expected] of Object.entries(dependencies))
    if (!same(components.get(name).dependencies, expected))
      fail('DATASET_GRAPH', 'Graph typed dependencies differ from the registered closure');
  if (
    components.get('marketDataset').semanticRoots.marketRoot !== m.roots.marketRoot ||
    components.get('marketDataset').payloadSha256 !== m.roots.marketRoot ||
    components.get('researchColumns').semanticRoots.financialDatasetRoot !==
      m.roots.financialDatasetRoot
  )
    fail('DATASET_ROOT', 'Graph derived roots differ from the manifest');
  // Hosted coverage is a bounded index; complete evidence remains in components.
  if (components.get('coverage').byteLength > LIMITS.sourceChunkBytes)
    fail('DATASET_BUDGET', 'Hosted graph coverage index exceeds 8 MiB', 413);
  return { manifest: m, components, totalBytes, partCount };
}
