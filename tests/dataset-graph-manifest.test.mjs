/** Real public purecore graph bytes; no network, provider, private inputs or fit. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { validateGraphDatasetManifest } from '../edge/dataset-graphs/manifest.mjs';
import { validateDatasetManifest } from '../edge/datasets/manifest.mjs';
import protocol from '../contracts/hosted-dataset-graphs-v1.json' with { type: 'json' };

const rootDirectory = fileURLToPath(new URL('../', import.meta.url));
// This generator restores committed public raw inputs and actually recomposes
// dataset/3. It does not relabel the old fixture or synthesize a successful fit.
const generated = spawnSync(
  process.env.PYTHON || '.venv/bin/python',
  [
    '-c',
    String.raw`
import json
from pathlib import Path
from atlas_quant.research_dataset import DirectoryDatasetReader,FinancialSource,validate_snapshot_scope_origin
from atlas_quant.research_dataset.graph_v3.dataset import compose_graph_dataset_components
from atlas_quant.research_dataset.codec import sha
p=Path('tests/fixtures/dataset-v2-core')
old=DirectoryDatasetReader(p/'dataset')
m=old.manifest
view=validate_snapshot_scope_origin(old.payload('marketOrigin'))
ref=m['financialSources'][0]
raw=old.payload(ref['componentId'])
source=FinancialSource(raw,ref['preparedRoot'],ref['calendarRef'],tuple(ref['proofRefs']))
registry={x.stem:x.read_bytes() for x in (p/'registry').glob('*.json')}
parts={}
pub=compose_graph_dataset_components(view,[source],registry,lambda c,n,b:parts.__setitem__((c,n),b),market_calendar_ref=m['marketCalendarRef'])
manifest=json.loads(pub.manifest_bytes)
origin=json.loads(view.origin_bytes)['source']
summary=json.loads(old.payload('coverage'))['financial'][0]
profile=manifest['profile']
spec={'profile':profile,'request':{'profile':profile},'sources':{
 'market':{'scope':manifest['scope'],'bundleId':origin['bundleId'],'snapshot':{'sha256':origin['snapshotSha256']}},
 'marketCalendarRef':m['marketCalendarRef'],
 'financial':[{'inputId':'00000000-0000-0000-0000-000000000001','preparationId':'00000000-0000-0000-0000-000000000002',
 'calendarRef':ref['calendarRef'],'proofRefs':ref['proofRefs'],
 'roots':{k:summary[k] for k in ['inputRoot','packRoot','preparedRoot','calendarRoot']},
 'package':{'sha256':sha(raw),'byteLength':len(raw)}}]}}
print(json.dumps({'manifestText':pub.manifest_bytes.decode(),'datasetRoot':pub.dataset_root,'spec':spec,
 'partCount':len(parts),'totalBytes':len(pub.manifest_bytes)+sum(map(len,parts.values())),
 'modelFits':0,'providerCalls':0}))
`
  ],
  {
    cwd: rootDirectory,
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: 'engine' },
    maxBuffer: 2 * 1024 ** 2,
    timeout: 30000
  }
);
assert.equal(generated.status, 0, generated.stderr || String(generated.error));
const fixture = JSON.parse(generated.stdout),
  original = JSON.parse(fixture.manifestText);
const h = (value) => createHash('sha256').update(value).digest('hex');
const clone = (value) => structuredClone(value);
const canonical = (value) =>
  Array.isArray(value)
    ? '[' + value.map(canonical).join(',') + ']'
    : value && typeof value === 'object'
      ? '{' +
        Object.keys(value)
          .sort()
          .map((key) => JSON.stringify(key) + ':' + canonical(value[key]))
          .join(',') +
        '}'
      : JSON.stringify(value);
const component = (m, name) => m.components.find((c) => c.componentId === name);
function resign(m) {
  const changed = new Map();
  for (const c of m.components) {
    const previous = c.componentRoot;
    c.dependencies = c.dependencies.map((d) => changed.get(d) || d).sort();
    const projection = { ...c };
    delete projection.componentRoot;
    c.componentRoot = h(canonical(projection));
    changed.set(previous, c.componentRoot);
  }
  return canonical(m);
}
async function check(m, spec = clone(fixture.spec)) {
  const text = resign(m);
  return validateGraphDatasetManifest(text, h(text), spec);
}
async function rejected(mutator, code) {
  const m = clone(original),
    spec = clone(fixture.spec);
  mutator(m, spec);
  await assert.rejects(check(m, spec), (error) => {
    assert.ok(error.code, `Expected typed error, got ${error}`);
    if (code) assert.equal(error.code, code);
    return true;
  });
}
function resize(c, size, partBytes = protocol.limits.partBytes) {
  c.byteLength = size;
  c.parts = Array.from({ length: Math.ceil(size / partBytes) }, (_, ordinal) => ({
    ordinal,
    byteLength: Math.min(partBytes, size - ordinal * partBytes),
    sha256: h(`${c.componentId}/${ordinal}`)
  }));
}
function secondSource(m, spec) {
  const input = clone(component(m, 'financialInput0')),
    graph = clone(component(m, 'financialGraph0'));
  input.componentId = 'financialInput1';
  input.componentRoot = h('new input seed');
  input.semanticRoots = { inputRoot: h('new input'), packRoot: 'f'.repeat(64) };
  input.payloadSha256 = h('new package');
  graph.componentId = 'financialGraph1';
  graph.componentRoot = h('new graph seed');
  graph.semanticRoots.packRoot = input.semanticRoots.packRoot;
  graph.dependencies = [input.componentRoot];
  m.components.splice(m.components.indexOf(component(m, 'researchColumns')), 0, input, graph);
  for (const name of ['researchColumns', 'schema', 'coverage'])
    component(m, name).dependencies.push(graph.componentRoot);
  m.financialSources.push({
    ...clone(m.financialSources[0]),
    componentId: 'financialInput1'
  });
  const source = clone(spec.sources.financial[0]);
  source.roots.inputRoot = input.semanticRoots.inputRoot;
  source.roots.packRoot = input.semanticRoots.packRoot;
  source.package.sha256 = input.payloadSha256;
  spec.sources.financial.push(source);
  return { input, graph };
}

test('fresh public purecore dataset/3 is accepted with exact counts and remains rejected by dataset/2', async () => {
  assert.equal(fixture.modelFits, 0);
  assert.equal(fixture.providerCalls, 0);
  const result = await validateGraphDatasetManifest(
    fixture.manifestText,
    fixture.datasetRoot,
    fixture.spec
  );
  assert.deepEqual(result.manifest, original);
  assert.equal(result.totalBytes, fixture.totalBytes);
  assert.equal(result.partCount, fixture.partCount);
  assert.equal(result.components.size, 8);
  assert.equal(result.components.get('financialGraph0').type, 'financial_prepared_graph');
  assert.equal(result.components.get('researchColumns').type, 'research_columns');
  await assert.rejects(
    validateDatasetManifest(fixture.manifestText, fixture.datasetRoot, fixture.spec)
  );
});

for (const [label, mutation] of [
  [
    'version 2',
    (m) => {
      m.version = 2;
    }
  ],
  [
    'legacy profile',
    (m) => {
      m.profile = 'financial_snapshot_view_50_v1';
    }
  ],
  [
    'bool version',
    (m) => {
      m.version = true;
    }
  ],
  [
    'wrong frozen profile',
    (_, s) => {
      s.profile = 'financial_snapshot_view_50_v1';
    }
  ],
  [
    'wrong request profile',
    (_, s) => {
      s.request.profile = 'financial_snapshot_view_50_v1';
    }
  ],
  [
    'extra authority field',
    (m) => {
      m.sourceAuthorityVerified = true;
    }
  ],
  [
    'zero sources',
    (m, s) => {
      m.financialSources = [];
      s.sources.financial = [];
    }
  ],
  [
    'nine sources',
    (m, s) => {
      m.financialSources = Array(9).fill(m.financialSources[0]);
      s.sources.financial = Array(9).fill(s.sources.financial[0]);
    }
  ],
  [
    'too many components',
    (m) => {
      m.components = Array(33).fill(m.components[0]);
    }
  ],
  [
    'unknown type',
    (m) => {
      m.components[0].type = 'constructor';
    }
  ],
  [
    'legacy graph type',
    (m) => {
      component(m, 'financialGraph0').type = 'financial_prepared';
    }
  ],
  [
    'legacy rows name',
    (m) => {
      component(m, 'researchColumns').componentId = 'researchRows';
    }
  ],
  [
    'unregistered encoding',
    (m) => {
      m.components[0].encoding = 'pickle';
    }
  ],
  [
    'bool component version',
    (m) => {
      m.components[0].version = true;
    }
  ],
  [
    'duplicate component id',
    (m) => {
      m.components[1].componentId = m.components[0].componentId;
    }
  ],
  [
    'path component id',
    (m) => {
      m.components[0].componentId = '../part';
    }
  ],
  [
    'missing component',
    (m) => {
      m.components.pop();
    }
  ],
  [
    'extra component property',
    (m) => {
      m.components[0].url = 'https://untrusted.invalid';
    }
  ],
  [
    'missing graph commitment',
    (m) => {
      delete component(m, 'financialGraph0').semanticRoots.preparedPayloadSha256;
    }
  ],
  [
    'missing logical commitment',
    (m) => {
      delete component(m, 'researchColumns').semanticRoots.logicalJoinedSha256;
    }
  ],
  [
    'extra semantic root',
    (m) => {
      component(m, 'researchColumns').semanticRoots.preparedRoot = h('wrong');
    }
  ],
  [
    'malformed new commitment',
    (m) => {
      component(m, 'financialGraph0').semanticRoots.preparedPayloadSha256 = 'ABC';
    }
  ],
  [
    'top-level root changed',
    (m) => {
      m.roots.financialDatasetRoot = h('wrong');
    }
  ],
  [
    'market derived root changed',
    (m) => {
      component(m, 'marketDataset').semanticRoots.marketRoot = h('wrong');
    }
  ],
  [
    'market payload contradicts market root',
    (m) => {
      component(m, 'marketDataset').payloadSha256 = h('other complete market bytes');
    }
  ],
  [
    'null market ref',
    (m) => {
      m.marketCalendarRef = null;
    }
  ],
  [
    'invalid market UUID',
    (m, s) => {
      m.marketCalendarRef = s.sources.marketCalendarRef = 'not-uuid';
    }
  ]
])
  test(`fully rehashed graph rejects ${label}`, () => rejected(mutation));

for (const [label, mutation] of [
  [
    'market scope',
    (m) => {
      m.scope.start = '20240425';
    }
  ],
  [
    'market bundle',
    (m) => {
      component(m, 'marketOrigin').semanticRoots.sourceBundleId = h('other bundle');
    }
  ],
  [
    'market snapshot',
    (m) => {
      component(m, 'marketOrigin').semanticRoots.sourceSnapshotSha256 = h('other snapshot');
    }
  ],
  [
    'financial input root',
    (m) => {
      component(m, 'financialInput0').semanticRoots.inputRoot = h('other input');
    }
  ],
  [
    'financial pack root',
    (m) => {
      component(m, 'financialInput0').semanticRoots.packRoot = h('other package');
    }
  ],
  [
    'prepared root',
    (m) => {
      component(m, 'financialGraph0').semanticRoots.preparedRoot = h('other prepared');
    }
  ],
  [
    'calendar root',
    (m) => {
      component(m, 'financialGraph0').semanticRoots.calendarRoot = h('other calendar');
    }
  ],
  [
    'package SHA',
    (m) => {
      component(m, 'financialInput0').payloadSha256 = h('other bytes');
    }
  ],
  [
    'package length',
    (m) => {
      const c = component(m, 'financialInput0');
      c.byteLength++;
      c.parts.at(-1).byteLength++;
    }
  ],
  [
    'source order',
    (m) => {
      m.financialSources[0].componentId = 'financialInput1';
    }
  ],
  [
    'calendar grant',
    (m) => {
      m.financialSources[0].calendarRef = '00000000-0000-0000-0000-000000000099';
    }
  ],
  [
    'proof grant',
    (m) => {
      m.financialSources[0].proofRefs = ['00000000-0000-0000-0000-000000000099'];
    }
  ],
  [
    'proof duplicates despite matching plan',
    (m, s) => {
      m.financialSources[0].proofRefs = s.sources.financial[0].proofRefs = Array(2).fill(
        m.marketCalendarRef
      );
    }
  ],
  [
    '257 proof refs despite matching plan',
    (m, s) => {
      m.financialSources[0].proofRefs = s.sources.financial[0].proofRefs = Array.from(
        { length: 257 },
        (_, n) => `00000000-0000-0000-0000-${n.toString(16).padStart(12, '0')}`
      );
    }
  ],
  [
    'duplicate frozen pack',
    (m, s) => {
      const c = secondSource(m, s);
      c.input.semanticRoots.packRoot =
        c.graph.semanticRoots.packRoot =
        s.sources.financial[1].roots.packRoot =
          s.sources.financial[0].roots.packRoot;
    }
  ],
  [
    'unordered frozen packs',
    (m, s) => {
      const c = secondSource(m, s);
      c.input.semanticRoots.packRoot =
        c.graph.semanticRoots.packRoot =
        s.sources.financial[1].roots.packRoot =
          '0'.repeat(64);
    }
  ]
])
  test(`frozen graph source binding rejects ${label}`, () =>
    rejected(mutation, 'DATASET_SOURCE_CHANGED'));

for (const [label, mutation] of [
  [
    'missing typed edge',
    (m) => {
      component(m, 'marketDataset').dependencies.pop();
    }
  ],
  [
    'extra typed edge',
    (m) => {
      component(m, 'financialInput0').dependencies.push(component(m, 'marketOrigin').componentRoot);
    }
  ],
  [
    'duplicate edge',
    (m) => {
      component(m, 'financialGraph0').dependencies.push(
        ...component(m, 'financialGraph0').dependencies
      );
    }
  ],
  [
    'forward edge',
    (m) => {
      component(m, 'registryEvidence').dependencies = [component(m, 'coverage').componentRoot];
    }
  ],
  [
    'unknown edge',
    (m) => {
      component(m, 'financialGraph0').dependencies = [h('missing')];
    }
  ],
  [
    'depth four',
    (m) => {
      component(m, 'financialInput0').dependencies = [component(m, 'marketDataset').componentRoot];
    }
  ]
])
  test(`fully rehashed topology rejects ${label}`, () => rejected(mutation, 'DATASET_GRAPH'));

for (const [label, dates] of [
  ['367 inclusive days', ['20240101', '20250101']],
  ['nonleap February 29', ['20230229', '20230301']],
  ['February 30', ['20240230', '20240301']],
  ['backward dates', ['20240503', '20240426']],
  ['year zero', ['00000101', '00000102']],
  ['noncanonical date', ['2024-04-26', '20240503']]
])
  test(`invalid scope rejected even when frozen plan agrees: ${label}`, () =>
    rejected((m, s) => {
      [m.scope.start, m.scope.end] = dates;
      s.sources.market.scope = clone(m.scope);
    }, 'DATASET_SCOPE'));

test('366 inclusive days and Python-compatible Gregorian years remain accepted', async () => {
  for (const [start, end] of [
    ['20240101', '20241231'],
    ['00010101', '00010102'],
    ['99991230', '99991231']
  ]) {
    const m = clone(original),
      s = clone(fixture.spec);
    m.scope.start = start;
    m.scope.end = end;
    s.sources.market.scope = clone(m.scope);
    await check(m, s);
  }
});
for (const symbols of [
  [],
  ['600000.SH', '600000.SH'],
  ['600000.SH', '000001.SZ'],
  ['600000.HK'],
  Array.from({ length: 51 }, (_, n) => `${String(n).padStart(6, '0')}.SZ`)
])
  test(`scope symbols independently checked: ${JSON.stringify(symbols).slice(0, 65)}`, () =>
    rejected((m, s) => {
      m.scope.symbols = symbols;
      s.sources.market.scope = clone(m.scope);
    }, 'DATASET_SCOPE'));

for (const [label, mutation] of [
  [
    'zero component',
    (m) => {
      resize(component(m, 'schema'), 0);
    }
  ],
  [
    'bool byte length',
    (m) => {
      component(m, 'schema').byteLength = true;
    }
  ],
  [
    'too large part',
    (m) => {
      resize(component(m, 'schema'), protocol.limits.partBytes + 1, protocol.limits.partBytes + 1);
    }
  ],
  [
    'discontinuous ordinal',
    (m) => {
      component(m, 'schema').parts[0].ordinal = 1;
    }
  ],
  [
    'bool ordinal',
    (m) => {
      component(m, 'schema').parts[0].ordinal = false;
    }
  ],
  [
    'component size mismatch',
    (m) => {
      component(m, 'schema').byteLength++;
    }
  ],
  [
    'joined 24 MiB plus one',
    (m) => {
      resize(component(m, 'researchColumns'), protocol.limits.packageBytes + 1);
    }
  ],
  [
    'market 24 MiB plus one',
    (m) => {
      resize(component(m, 'marketDataset'), protocol.limits.packageBytes + 1);
    }
  ],
  [
    'package 24 MiB plus one',
    (m) => {
      resize(component(m, 'financialInput0'), protocol.limits.packageBytes + 1);
    }
  ],
  [
    'shared package budget',
    (m, s) => {
      const { input } = secondSource(m, s);
      resize(input, 13 * 1024 ** 2);
      resize(component(m, 'financialInput0'), 13 * 1024 ** 2);
    }
  ],
  [
    'shared 64 MiB',
    (m) => {
      resize(component(m, 'marketOrigin'), protocol.limits.closureBytes);
    }
  ],
  [
    '257 aggregate parts',
    (m) => {
      resize(component(m, 'schema'), 250, 1);
    }
  ],
  [
    'hosted coverage index 8 MiB plus one',
    (m) => {
      resize(component(m, 'coverage'), protocol.limits.sourceChunkBytes + 1);
    }
  ]
])
  test(`graph resource guard rejects ${label}`, () => rejected(mutation));

test('typed 24 MiB, hosted 8 MiB, and total 256-part boundaries are inclusive', async () => {
  for (const name of ['marketDataset', 'researchColumns']) {
    const m = clone(original);
    resize(component(m, name), protocol.limits.packageBytes);
    await check(m);
  }
  const m = clone(original);
  resize(component(m, 'coverage'), protocol.limits.sourceChunkBytes);
  await check(m);
  const many = clone(original);
  resize(component(many, 'schema'), 249, 1);
  assert.equal((await check(many)).partCount, 256);
});

test('64 MiB counts exact original manifest bytes as well as all part bytes', async () => {
  const m = clone(original),
    origin = component(m, 'marketOrigin');
  const otherBytes = m.components.filter((c) => c !== origin).reduce((n, c) => n + c.byteLength, 0);
  let text = resign(m);
  for (let i = 0; i < 8; i++) {
    resize(origin, protocol.limits.closureBytes - otherBytes - Buffer.byteLength(text));
    text = resign(m);
  }
  const result = await validateGraphDatasetManifest(text, h(text), fixture.spec);
  assert.equal(result.totalBytes, protocol.limits.closureBytes);
  resize(origin, origin.byteLength + 1);
  await assert.rejects(check(m), (e) => e.code === 'DATASET_BUDGET');
});

test('canonical original manifest bytes and component roots cannot be normalized into agreement', async () => {
  await assert.rejects(
    validateGraphDatasetManifest(fixture.manifestText, '0'.repeat(64), fixture.spec)
  );
  for (const text of [
    fixture.manifestText + ' ',
    JSON.stringify(original, null, 2),
    fixture.manifestText.replace('"version":3', '"version":3.0'),
    fixture.manifestText.replace('"version":1', '"version":1.0'),
    fixture.manifestText.replace('"ordinal":0', '"ordinal":0.0'),
    fixture.manifestText.replace('"ordinal":0', '"ordinal":-0'),
    fixture.manifestText.replace('"version":3', '"version":3,"version":3'),
    fixture.manifestText.replace('"components":', '"components":[],"components":'),
    ' '.repeat(protocol.limits.manifestBytes + 1)
  ])
    await assert.rejects(validateGraphDatasetManifest(text, h(text), fixture.spec));
  const m = clone(original);
  m.components[0].componentRoot = '0'.repeat(64);
  const raw = canonical(m);
  await assert.rejects(
    validateGraphDatasetManifest(raw, h(raw), fixture.spec),
    (e) => e.code === 'DATASET_ROOT'
  );
});
