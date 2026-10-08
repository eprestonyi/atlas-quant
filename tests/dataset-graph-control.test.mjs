/** Actual D1/R2 public API with server-selected test owners; zero provider/F.
 * Session authentication belongs to the unchanged worker, not this API harness. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { randomUUID, createHash } from 'node:crypto';
import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import { bundleFixture, canonical } from './fixtures/bundle-fixture.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const hash = (x) => createHash('sha256').update(x).digest('hex');
const profiles = {
  2: 'financial_snapshot_view_50_v1',
  3: 'financial_snapshot_graph_50_v1'
};
const admission = 'financial_fundamental_graph_auto_50_v1';
const graphCapabilities = {
  datasetFormats: ['atlas.quant.research_dataset/3'],
  snapshotFormats: ['financial_column_snapshot_v1'],
  transportFormats: ['atlas.quant.financial_bundle/2'],
  financialResearchProfiles: [admission]
};
const bundle = await build({
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'browser',
  stdin: {
    resolveDir: ROOT,
    contents: `
import { datasetApi } from './edge/datasets/api.mjs';
import { LEGACY_DATASET,GRAPH_DATASET } from './edge/datasets/context.mjs';
export default {async fetch(req,env){
 const path=new URL(req.url).pathname.replace(/^\\/quant\\/api/,'');
 const flags=await env.DB.prepare("SELECT value FROM meta WHERE key='test_flags'").first();
 try {return await datasetApi(req,{...env,...JSON.parse(flags?.value||'{}')},path,
   req.headers.get('x-fixture-owner'),path.startsWith('/dataset-graphs')?GRAPH_DATASET:LEGACY_DATASET)
   || new Response('unmatched',{status:404});}
 catch(e){return Response.json({error:{code:e.code||'TEST_ERROR',message:e.message}},{status:e.status||500});}
}};`
  }
});
const script = bundle.outputFiles[0].text;
const generated = spawnSync(
  process.env.PYTHON || '.venv/bin/python',
  [
    '-c',
    String.raw`
import json
from pathlib import Path
from atlas_quant.research_dataset import DirectoryDatasetReader,FinancialSource,validate_snapshot_scope_origin
from atlas_quant.research_dataset.graph_v3.dataset import compose_graph_dataset_components
p=Path('tests/fixtures/dataset-v2-core'); old=DirectoryDatasetReader(p/'dataset'); m=old.manifest
ref=m['financialSources'][0]; source=FinancialSource(old.payload(ref['componentId']),ref['preparedRoot'],ref['calendarRef'],tuple(ref['proofRefs']))
registry={p.stem:p.read_bytes() for p in (p/'registry').glob('*.json')}; chunks={}
pub=compose_graph_dataset_components(validate_snapshot_scope_origin(old.payload('marketOrigin')),[source],registry,
 lambda c,n,b:chunks.__setitem__(c+'/'+str(n),b.decode()),market_calendar_ref=m['marketCalendarRef'])
print(json.dumps({'text':pub.manifest_bytes.decode(),'chunks':chunks}))
`
  ],
  {
    cwd: ROOT,
    env: { ...process.env, PYTHONPATH: 'engine' },
    encoding: 'utf8',
    maxBuffer: 4 * 1024 ** 2,
    timeout: 30000
  }
);
assert.equal(generated.status, 0, generated.stderr || String(generated.error));
const graphCore = JSON.parse(generated.stdout);
const legacyText = await fs.readFile(
  new URL('./fixtures/dataset-v2-core/dataset/manifest.json', import.meta.url),
  'utf8'
);
const legacyCore = { text: legacyText, chunks: {} };
for (const c of JSON.parse(legacyText).components)
  for (const p of c.parts)
    legacyCore.chunks[c.componentId + '/' + p.ordinal] = await fs.readFile(
      new URL(
        `./fixtures/dataset-v2-core/dataset/parts/${c.componentId}/${p.ordinal}.bin`,
        import.meta.url
      ),
      'utf8'
    );

async function environment(t, flags = {}) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS']
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB'),
    bucket = await mf.getR2Bucket('ARTIFACTS');
  await db.exec(
    (await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll(
      '\n',
      ' '
    )
  );
  const meta = async (key, value, when = new Date().toISOString()) =>
    db
      .prepare('INSERT OR REPLACE INTO meta(key,value,updated_at) VALUES(?,?,?)')
      .bind(key, JSON.stringify(value), when)
      .run();
  await meta('test_flags', flags);
  const call = (path, { owner = 'owner-a', method = 'GET', data } = {}) =>
    mf.dispatchFetch('https://graph.test/quant/api' + path, {
      method,
      headers: {
        'x-fixture-owner': owner,
        ...(data ? { 'content-type': 'application/json' } : {})
      },
      body: data ? JSON.stringify(data) : undefined
    });
  return { mf, db, bucket, call, meta, flags: (v) => meta('test_flags', v) };
}
async function json(response, status = 200) {
  assert.equal(response.status, status, await response.clone().text());
  return response.json();
}
const flags = {
  RESEARCH_DATASETS_ENABLED: 'true',
  RESEARCH_DATASET_GRAPHS_ENABLED: 'true',
  FINANCIAL_DATASET_RESEARCH_ENABLED: 'true',
  FINANCIAL_GRAPH_RESEARCH_ENABLED: 'true'
};
const base = (version) => (version === 3 ? '/dataset-graphs' : '/datasets');
const planPath = (version, id = '') =>
  (version === 3 ? '/dataset-graphs/plans' : '/dataset-plans') + (id ? '/' + id : '');
const jobPath = (version, id) =>
  (version === 3 ? '/dataset-graphs/jobs/' : '/dataset-preparations/') + id;

async function ready(
  f,
  version,
  {
    owner = 'owner-a',
    specProfile = profiles[version],
    requestProfile = specProfile,
    manifestVersion = version,
    manifestProfile = profiles[manifestVersion],
    stageOwner = owner,
    planOwner = owner,
    created = '2026-10-08T00:00:00Z'
  } = {}
) {
  const planId = randomUUID(),
    jobId = randomUUID(),
    stageId = randomUUID(),
    datasetId = randomUUID();
  const core = version === 3 ? graphCore : legacyCore,
    manifest = JSON.parse(core.text);
  manifest.version = manifestVersion;
  manifest.profile = manifestProfile;
  const text = canonical(manifest),
    root = hash(text),
    scope = manifest.scope;
  const spec = {
    profile: specProfile,
    request: { profile: requestProfile, marketSource: {}, financialInputs: [] },
    sources: {
      market: { scope, originalScope: scope },
      marketCalendarRef: manifest.marketCalendarRef,
      financial: [{ selection: { selectedStateIds: ['model_fin_cash_asset_share'] } }],
      knownSourceBytes: 1,
      registry: []
    }
  };
  await f.db
    .prepare(
      'INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)'
    )
    .bind(
      planId,
      planOwner,
      randomUUID(),
      'a'.repeat(64),
      hash(canonical(spec)),
      'PUBLIC CORE CONTROL FIXTURE',
      canonical(spec),
      created
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,dataset_id,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?,?)"
    )
    .bind(jobId, owner, planId, randomUUID(), 'b'.repeat(64), datasetId, created, created)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed','{}',?,?)"
    )
    .bind(
      stageId,
      jobId,
      stageOwner,
      randomUUID(),
      datasetId,
      root,
      text,
      Buffer.byteLength(text) +
        Object.values(core.chunks).reduce((n, raw) => n + Buffer.byteLength(raw), 0),
      created,
      created
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_research_datasets(id,owner,name,dataset_root,stage_id,status,scope,summary,created_at) VALUES(?,?,?,?,?,'ready',?,'{}',?)"
    )
    .bind(datasetId, owner, 'PUBLIC CORE CONTROL FIXTURE', root, stageId, canonical(scope), created)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_coverage(dataset_id,ordinal,symbol,state_id,status,metadata) VALUES(?,0,?,'model_fin_cash_asset_share','available',?)"
    )
    .bind(
      datasetId,
      scope.symbols[0],
      canonical({
        stateId: 'model_fin_cash_asset_share',
        okRows: 6,
        missingRows: 0
      })
    )
    .run();
  for (const c of manifest.components)
    for (const p of c.parts) {
      const key = `${stageId}/${c.componentId}/${p.ordinal}`;
      await f.bucket.put(key, core.chunks[c.componentId + '/' + p.ordinal]);
      await f.db
        .prepare(
          'INSERT INTO quant_dataset_parts(stage_id,component_id,ordinal,sha256,byte_length,object_key) VALUES(?,?,?,?,?,?)'
        )
        .bind(stageId, c.componentId, p.ordinal, p.sha256, p.byteLength, key)
        .run();
    }
  return {
    planId,
    jobId,
    stageId,
    datasetId,
    root,
    scope,
    text,
    spec,
    detail: base(version) + '/' + datasetId,
    query: '?datasetRoot=' + root
  };
}

test('independent graph flags and complete four-capability tuple drive advertised research', async (t) => {
  const f = await environment(t, {
    RESEARCH_DATASETS_ENABLED: 'true',
    FINANCIAL_DATASET_RESEARCH_ENABLED: 'true'
  });
  await f.meta('runner', graphCapabilities);
  await f.meta('dataset_runner', { capability: 'research-dataset/1' });
  let c = await json(await f.call('/dataset-graphs/capabilities'));
  assert.equal(c.enabled, false);
  assert.equal(c.composition.online, false);
  assert.equal(c.researchBindingEnabled, false);
  assert.equal(c.profile, profiles[3]);
  assert.equal(c.composition.capability, 'research-dataset-graph/1');
  assert.deepEqual(c.datasetFormats, ['atlas.quant.research_dataset/3']);
  assert.deepEqual(c.financialResultFormats, ['atlas.quant.financial_bundle/2']);
  assert.equal(c.forecast.admissionProfile, admission);
  assert.equal(c.providerRequired, false);
  for (const path of ['/dataset-graphs/sources/markets', '/dataset-graphs/sources/financial'])
    await json(await f.call(path), 503);
  await json(await f.call('/dataset-graphs/plans', { method: 'POST', data: {} }), 503);
  await f.flags({ ...flags, RESEARCH_DATASET_GRAPHS_ENABLED: 'false' });
  assert.equal(
    (await json(await f.call('/dataset-graphs/capabilities'))).researchBindingEnabled,
    false
  );
  await f.flags({ ...flags, FINANCIAL_GRAPH_RESEARCH_ENABLED: 'false' });
  assert.equal(
    (await json(await f.call('/dataset-graphs/capabilities'))).researchBindingEnabled,
    false
  );
  await f.flags(flags);
  await f.meta('dataset_graph_runner', {
    capability: 'research-dataset-graph/1'
  });
  for (const key of Object.keys(graphCapabilities)) {
    await f.meta('runner', { ...graphCapabilities, [key]: [] });
    c = await json(await f.call('/dataset-graphs/capabilities'));
    assert.equal(c.forecast.online, false);
    assert.equal(c.researchBindingEnabled, false);
  }
  await f.meta('runner', graphCapabilities);
  c = await json(await f.call('/dataset-graphs/capabilities'));
  assert.equal(c.researchBindingEnabled, true);
  assert.equal(c.composition.online, true);
  assert.equal(c.forecast.researchAdmissions.length, 1);
  assert.equal(c.forecast.researchAdmissions[0].profile, admission);
  const legacy = await json(await f.call('/dataset-capabilities'));
  assert.equal(legacy.forecast.online, false);
  assert.deepEqual(legacy.datasetFormats, ['atlas.quant.research_dataset/2']);
  await f.meta('runner', graphCapabilities, '2020-01-01T00:00:00Z');
  assert.equal((await json(await f.call('/dataset-graphs/capabilities'))).forecast.online, false);
});

test('shared-table lists filter both plan profiles and manifest identity before pagination', async (t) => {
  const f = await environment(t, flags),
    graph = await ready(f, 3),
    legacy = await ready(f, 2);
  await ready(f, 3, { owner: 'owner-b' });
  await ready(f, 3, { requestProfile: profiles[2] });
  await ready(f, 3, { specProfile: profiles[2], requestProfile: profiles[3] });
  await ready(f, 3, { manifestVersion: 2 });
  await ready(f, 3, { manifestProfile: profiles[2] });
  await ready(f, 3, { stageOwner: 'owner-b' });
  await ready(f, 3, { planOwner: 'owner-b' });
  for (const [version, expected] of [
    [3, graph],
    [2, legacy]
  ]) {
    const page = await json(await f.call(base(version) + '?pageSize=1'));
    assert.equal(page.total, 1);
    assert.equal(page.items.length, 1);
    assert.equal(page.items[0].datasetRef.datasetId, expected.datasetId);
    assert.equal(page.items[0].datasetRef.version, version);
    assert.deepEqual((await json(await f.call(base(version) + '?pageSize=1&page=2'))).items, []);
  }
});

test('all graph/legacy ID reads reject the other profile and another owner', async (t) => {
  const f = await environment(t, flags),
    graph = await ready(f, 3),
    legacy = await ready(f, 2);
  for (const [version, entry] of [
    [3, graph],
    [2, legacy]
  ]) {
    const other = version === 3 ? 2 : 3;
    for (const suffix of ['', '/manifest', '/coverage', version === 3 ? '/download' : '/archive']) {
      await json(await f.call(entry.detail + suffix + entry.query, { owner: 'owner-b' }), 404);
      const wrongSuffix =
        suffix === '/download' ? '/archive' : suffix === '/archive' ? '/download' : suffix;
      await json(
        await f.call(base(other) + '/' + entry.datasetId + wrongSuffix + entry.query),
        409
      );
    }
    await json(await f.call(planPath(other, entry.planId)), 409);
    await json(await f.call(jobPath(other, entry.jobId)), 409);
    await json(await f.call(jobPath(other, entry.jobId) + '/cancel', { method: 'POST' }), 409);
    await json(
      await f.call(planPath(other, entry.planId) + '/start', {
        method: 'POST',
        data: {
          requestId: randomUUID(),
          expectedPlanRoot: hash(canonical(entry.spec))
        }
      }),
      409
    );
    await json(await f.call(planPath(version, entry.planId), { owner: 'owner-b' }), 404);
    await json(await f.call(jobPath(version, entry.jobId), { owner: 'owner-b' }), 404);
    const state = await f.db
      .prepare('SELECT status FROM quant_dataset_jobs WHERE id=?')
      .bind(entry.jobId)
      .first();
    assert.equal(state.status, 'completed');
  }
});

test('root-pinned graph detail, manifest, coverage and complete archive remain readable after flag-off', async (t) => {
  const f = await environment(t, flags),
    entry = await ready(f, 3);
  await f.meta('runner', graphCapabilities);
  const first = await json(await f.call(entry.detail + entry.query));
  assert.equal(first.datasetRef.version, 3);
  assert.equal(first.sourceEvidenceClosure, 'separate_research_dataset_v3');
  assert.equal(first.preferredResearchAdmission.profile, admission);
  assert.equal(first.archiveUrl, '/quant/api' + entry.detail + '/download' + entry.query);
  await f.flags({});
  assert.equal(
    (await json(await f.call(entry.detail + entry.query))).researchBindingEnabled,
    false
  );
  assert.equal((await json(await f.call('/dataset-graphs'))).total, 1);
  assert.equal((await json(await f.call(planPath(3, entry.planId)))).plan.profile, profiles[3]);
  assert.equal((await json(await f.call(jobPath(3, entry.jobId)))).datasetRef.version, 3);
  const manifest = await f.call(entry.detail + '/manifest' + entry.query);
  assert.equal(manifest.status, 200);
  assert.equal(manifest.headers.get('content-length'), String(Buffer.byteLength(entry.text)));
  assert.equal(manifest.headers.get('x-content-sha256'), entry.root);
  assert.equal(await manifest.text(), entry.text);
  const coverage = await json(await f.call(entry.detail + '/coverage' + entry.query));
  assert.equal(coverage.datasetRoot, entry.root);
  assert.equal(coverage.items[0].okRows, 6);
  const archive = await f.call(entry.detail + '/download' + entry.query),
    raw = Buffer.from(await archive.arrayBuffer());
  assert.equal(archive.status, 200);
  assert.equal(archive.headers.get('x-atlas-dataset-root'), entry.root);
  const expected = new Map([
    ['manifest.json', entry.text],
    ...Object.entries(graphCore.chunks).map(([p, raw]) => ['parts/' + p + '.bin', raw])
  ]);
  let offset = 0;
  for (const [name, text] of expected) {
    assert.equal(
      raw
        .subarray(offset, offset + 100)
        .toString()
        .replace(/\0.*$/s, ''),
      name
    );
    const size = parseInt(raw.subarray(offset + 124, offset + 136).toString(), 8);
    assert.equal(raw.subarray(offset + 512, offset + 512 + size).toString(), text);
    offset += 512 + size + ((512 - (size % 512)) % 512);
  }
  assert.equal(raw.length, offset + 1024);
  assert.deepEqual(raw.subarray(offset), Buffer.alloc(1024));
  await json(await f.call(entry.detail + '?datasetRoot=' + 'f'.repeat(64)), 409);
  await json(await f.call(entry.detail + '/archive' + entry.query), 404);
  await f.db
    .prepare("UPDATE quant_dataset_stages SET manifest_text=manifest_text||' ' WHERE id=?")
    .bind(entry.stageId)
    .run();
  for (const suffix of ['', '/manifest', '/coverage', '/download'])
    await json(await f.call(entry.detail + suffix + entry.query), 409);
});

test('mismatched plan request profile and manifest version cannot escape via detail or completed job', async (t) => {
  const f = await environment(t, flags);
  for (const options of [
    { requestProfile: profiles[2] },
    { manifestVersion: 2 },
    { manifestProfile: profiles[2] }
  ]) {
    const entry = await ready(f, 3, options);
    for (const suffix of ['', '/manifest', '/coverage', '/download'])
      await json(await f.call(entry.detail + suffix + entry.query), 409);
    await json(await f.call(jobPath(3, entry.jobId)), 409);
  }
});

test('coverage page budget is unchanged and missing archive parts fail rather than returning a partial success', async (t) => {
  const f = await environment(t, flags),
    entry = await ready(f, 3);
  await f.db
    .prepare('UPDATE quant_dataset_coverage SET metadata=? WHERE dataset_id=?')
    .bind(canonical({ reason: 'x'.repeat(262144) }), entry.datasetId)
    .run();
  await json(await f.call(entry.detail + '/coverage' + entry.query), 413);
  await f.db
    .prepare("DELETE FROM quant_dataset_parts WHERE stage_id=? AND component_id='financialInput0'")
    .bind(entry.stageId)
    .run();
  await json(await f.call(entry.detail + '/download' + entry.query), 409);
});

async function sources(f, owner = 'owner-a') {
  const now = new Date().toISOString(),
    runId = randomUUID(),
    stageId = randomUUID(),
    inputId = randomUUID(),
    preparationId = randomUUID(),
    publicationId = randomUUID(),
    financialJob = randomUUID(),
    calendarRef = randomUUID();
  const b = bundleFixture({
    mutate: ({ snapshot }) => {
      snapshot.fingerprintVersion = 'research_input_v1';
    }
  });
  const parsed = await validateManifest(b.manifestText, b.bundleId);
  await f.db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'completed','demo',?,?,?)"
    )
    .bind(runId, owner, 'SYNTHETIC transport', canonical(b.strategy), now, now)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_runs(job_id,experiment_id,experiment_version,owner,kind,created_at) VALUES(?,?,1,?,'forecast',?)"
    )
    .bind(runId, randomUUID(), owner, now)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed',?,?)"
    )
    .bind(
      stageId,
      owner,
      runId,
      randomUUID(),
      b.bundleId,
      b.manifestText,
      'fixture-only',
      canonical(parsed.metadata),
      now,
      now
    )
    .run();
  await f.db
    .prepare('INSERT INTO quant_bundle_runs(job_id,owner,stage_id) VALUES(?,?,?)')
    .bind(runId, owner, stageId)
    .run();
  const roots = {
    inputRoot: 'a'.repeat(64),
    packRoot: 'b'.repeat(64),
    preparedRoot: 'c'.repeat(64),
    calendarRoot: 'd'.repeat(64)
  };
  const selection = {
    symbols: ['000001.SZ'],
    start: '20250101',
    end: '20250103',
    selectedStateIds: ['model_fin_cash_asset_share'],
    announcementStart: '20250101',
    scope: 'consolidated',
    flowBasis: 'ytd'
  };
  const calendar = canonical({
    kind: 'calendar',
    registryVersion: 1,
    scope: { calendarRoot: roots.calendarRoot },
    payload: {
      complete: true,
      coverage_start: selection.start,
      coverage_end: selection.end,
      sessions: ['20250102', '20250103']
    },
    evidenceLevel: 'SYNTHETIC_TRANSPORT'
  });
  const calendarKey = 'calendar/' + calendarRef;
  await f.bucket.put(calendarKey, calendar);
  await f.db
    .prepare(
      "INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,status,created_at) VALUES(?,'calendar',?,?,?,?,'{}','active',?)"
    )
    .bind(calendarRef, owner, calendarKey, hash(calendar), Buffer.byteLength(calendar), now)
    .run();
  await f.db
    .prepare(
      "INSERT INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'prepared',?,'[]',2,?,?,?,?)"
    )
    .bind(inputId, owner, 'SYNTHETIC metadata', calendarRef, randomUUID(), 'f'.repeat(64), now, now)
    .run();
  await f.db
    .prepare(
      "INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'financial_prepare','completed','{}',?,?,?,?)"
    )
    .bind(financialJob, owner, inputId, randomUUID(), 'f'.repeat(64), now, now)
    .run();
  const financialManifest = canonical({
    summary: { input: { selection, unitPolicy: 'verified_only' } },
    collections: {
      package: {
        sha256: hash('{}'),
        byteLength: 2,
        chunks: [
          {
            ordinal: 0,
            startRow: null,
            rowCount: null,
            sha256: hash('{}'),
            byteLength: 2
          }
        ]
      }
    }
  });
  await f.db
    .prepare(
      "INSERT INTO financial_publications(id,owner,job_id,manifest_text,manifest_hash,status,total_bytes,created_at,updated_at) VALUES(?,?,?,?,?,'committed',2,?,?)"
    )
    .bind(publicationId, owner, financialJob, financialManifest, hash(financialManifest), now, now)
    .run();
  await f.db
    .prepare(
      'INSERT INTO financial_preparations(id,owner,input_id,publication_id,roots,metadata,created_at) VALUES(?,?,?,?,?,?,?)'
    )
    .bind(
      preparationId,
      owner,
      inputId,
      publicationId,
      canonical(roots),
      canonical({ hasUsableStates: true }),
      now
    )
    .run();
  return {
    calendarRef,
    request: {
      requestId: randomUUID(),
      name: 'SYNTHETIC metadata plan',
      profile: profiles[3],
      marketSource: {
        kind: 'forecast_snapshot_view',
        runId,
        expectedBundleId: b.bundleId,
        expectedSnapshotSha256: b.manifest.documents.snapshot.sha256,
        transform: {
          kind: 'snapshot_scope_view',
          version: 1,
          mode: 'explicit_subset',
          symbols: ['000001.SZ'],
          start: selection.start,
          end: selection.end
        }
      },
      financialInputs: [{ inputId, preparationId, ...roots }]
    }
  };
}

test('source lists retain owner-bound six-field financialRef and graph plan/start/cancel preserve frozen context', async (t) => {
  const f = await environment(t, flags),
    source = await sources(f);
  const financial = await json(await f.call('/dataset-graphs/sources/financial'));
  assert.equal(financial.total, 1);
  assert.deepEqual(financial.items[0].financialRef, source.request.financialInputs[0]);
  assert.deepEqual(
    Object.keys(financial.items[0].financialRef).sort(),
    ['inputId', 'preparationId', 'inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot'].sort()
  );
  assert.equal('inputPackage' in financial.items[0], false);
  assert.equal(
    (await json(await f.call('/dataset-graphs/sources/financial', { owner: 'owner-b' }))).total,
    0
  );
  assert.equal(
    (await json(await f.call('/dataset-graphs/sources/markets', { owner: 'owner-b' }))).total,
    0
  );
  assert.equal(
    (await json(await f.call('/dataset-graphs/sources/markets'))).items[0].sourceRef.runId,
    source.request.marketSource.runId
  );
  await json(
    await f.call('/dataset-graphs/plans', {
      method: 'POST',
      owner: 'owner-b',
      data: source.request
    }),
    404
  );
  await json(await f.call('/dataset-plans', { method: 'POST', data: source.request }), 400);
  await json(
    await f.call('/dataset-graphs/plans', {
      method: 'POST',
      data: { ...source.request, inputPackage: {} }
    }),
    400
  );
  const created = await json(
    await f.call('/dataset-graphs/plans', {
      method: 'POST',
      data: source.request
    }),
    201
  );
  assert.equal(created.plan.profile, profiles[3]);
  assert.equal(created.plan.checks.semantic, 'pending');
  assert.deepEqual(created.plan.financialInputs, source.request.financialInputs);
  const again = await json(
    await f.call('/dataset-graphs/plans', {
      method: 'POST',
      data: source.request
    }),
    201
  );
  assert.equal(again.plan.id, created.plan.id);
  assert.equal(again.idempotent, true);
  assert.equal((await json(await f.call(planPath(3, created.plan.id)))).plan.id, created.plan.id);
  await json(await f.call(planPath(2, created.plan.id)), 409);
  await f.meta('dataset_graph_runner', {
    capability: 'research-dataset-graph/1'
  });
  const start = {
    requestId: randomUUID(),
    expectedPlanRoot: created.plan.planRoot
  };
  const begun = await json(
    await f.call(planPath(3, created.plan.id) + '/start', {
      method: 'POST',
      data: start
    }),
    202
  );
  assert.equal(begun.preparation.status, 'queued');
  await json(
    await f.call(jobPath(2, begun.preparation.id) + '/cancel', {
      method: 'POST'
    }),
    409
  );
  const before = await json(await f.call(jobPath(3, begun.preparation.id)));
  assert.equal(before.activeJob.id, begun.preparation.id);
  assert.equal(before.datasetRef, null);
  await f.flags({});
  const cancelled = await json(
    await f.call(jobPath(3, begun.preparation.id) + '/cancel', {
      method: 'POST'
    })
  );
  assert.equal(cancelled.preparation.status, 'cancelled');
  assert.equal((await json(await f.call(jobPath(3, begun.preparation.id)))).activeJob, null);
  assert.equal(
    (await f.db.prepare("SELECT COUNT(*) n FROM jobs WHERE status='queued'").first()).n,
    0
  );
  await f.flags(flags);
  const stored = await f.db
    .prepare('SELECT spec FROM jobs WHERE id=?')
    .bind(source.request.marketSource.runId)
    .first();
  const oversized = JSON.parse(stored.spec);
  oversized.universe.symbols = Array.from(
    { length: 51 },
    (_, n) => String(n).padStart(6, '0') + '.SZ'
  );
  await f.db
    .prepare('UPDATE jobs SET spec=? WHERE id=?')
    .bind(canonical(oversized), source.request.marketSource.runId)
    .run();
  for (const path of ['/dataset-graphs/sources/markets', '/datasets/sources/markets']) {
    const listed = await json(await f.call(path));
    assert.equal(listed.items[0].eligibility.status, 'blocked');
    assert.ok(listed.items[0].eligibility.reasonCodes.includes('SOURCE_UNIVERSE_EXCEEDS_PROFILE'));
  }
});
