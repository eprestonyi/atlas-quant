/** Real Worker/D1 claims over explicit structural source fixtures; no provider/F.
 * Committed metadata here exercises authorization, not numerical composition. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import { validateStatisticalQuant } from '../edge/statistical-quant/validation.mjs';
import { canonical } from './fixtures/bundle-fixture.mjs';

const script = await buildWorkerSource({ buildId: 'graph-claim-control-test' });
const h = (x) => createHash('sha256').update(x).digest('hex');
const sourceProfile = 'financial_snapshot_graph_50_v1';
const profile = 'financial_fundamental_graph_auto_50_v1';
const GRAPH = {
  datasetFormats: ['atlas.quant.research_dataset/3'],
  snapshotFormats: ['financial_column_snapshot_v1'],
  transportFormats: ['atlas.quant.financial_bundle/2'],
  financialResearchProfiles: [profile]
};
const LEGACY = {
  datasetFormats: ['atlas.quant.research_dataset/2'],
  snapshotFormats: ['financial_json_v1'],
  transportFormats: ['atlas.quant.financial_bundle/1'],
  financialResearchProfiles: ['financial_fundamental_auto_50_v1']
};
const ON = {
  RESEARCH_DATASET_GRAPHS_ENABLED: 'true',
  FINANCIAL_GRAPH_RESEARCH_ENABLED: 'true',
  RESEARCH_DATASETS_ENABLED: 'true',
  FINANCIAL_DATASET_RESEARCH_ENABLED: 'true'
};
const BASIC = JSON.parse(
  await fs.readFile(new URL('../engine/examples/basic.json', import.meta.url), 'utf8')
);

async function fixture(t, flags = ON) {
  const options = {
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: { RUNNER_SECRET: 'graph-claim-test-only', ...flags }
  };
  const mf = new Miniflare(options);
  t.after(() => mf.dispose());
  let db = await mf.getD1Database('DB');
  await db.exec(
    (await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll(
      '\n',
      ' '
    )
  );
  const call = (input, authenticated = true) =>
    mf.dispatchFetch('https://claims.test/quant/api/runner/claim', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(authenticated ? { authorization: 'Bearer graph-claim-test-only' } : {})
      },
      body: JSON.stringify(input)
    });
  return {
    get db() {
      return db;
    },
    mf,
    call,
    claim: (capability = GRAPH, requestId = randomUUID()) =>
      call({ engineVersion: '0.9.0', requestId, ...capability }),
    legacyClaim: (capability = GRAPH) => call({ engineVersion: '0.9.0', ...capability }),
    flags: async (value) => {
      await mf.setOptions({
        ...options,
        bindings: { RUNNER_SECRET: 'graph-claim-test-only', ...value }
      });
      db = await mf.getD1Database('DB');
    },
    read: (id) => db.prepare('SELECT * FROM jobs WHERE id=?').bind(id).first(),
    claims: () => db.prepare('SELECT request_id,job_id FROM runner_claims').all()
  };
}
async function json(response, status = 200) {
  assert.equal(response.status, status, await response.clone().text());
  return response.json();
}

async function seedBasic(
  f,
  { dataSource = 'demo', created = '2026-10-08T00:00:00Z', owner = randomUUID() } = {}
) {
  const id = randomUUID();
  await f.db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?)"
    )
    .bind(
      id,
      owner,
      'SYNTHETIC ordinary claim fixture',
      dataSource,
      canonical(BASIC),
      created,
      created
    )
    .run();
  return { id, owner };
}

async function seedGraph(
  f,
  {
    relationProfile = profile,
    relationOwner,
    dataSource = 'ready_dataset',
    relation = true,
    version = 3
  } = {}
) {
  const owner = randomUUID(),
    id = randomUUID(),
    datasetId = randomUUID(),
    compositionId = randomUUID(),
    planId = randomUUID(),
    stageId = randomUUID();
  const stamp = '2020-01-01T00:00:00Z';
  const scope = { symbols: ['600000.SH'], start: '20240101', end: '20241231' };
  const strategy = validateStatisticalQuant({
    schemaVersion: 2,
    name: 'SYNTHETIC graph claim fixture',
    universe: scope,
    research: { mode: 'statistical_quant' },
    factors: [
      {
        id: 'cash',
        expression: 'model_fin_cash_asset_share',
        role: 'predictor'
      }
    ],
    target: { kind: 'asset_price', horizonSessions: 5 },
    model: {
      family: 'fundamental',
      estimator: 'auto',
      refitDays: 20,
      trainWindow: 120
    },
    validation: { innerFolds: 2, outerFolds: 2 },
    execution: { enabled: false }
  });
  const source = version === 3 ? sourceProfile : 'financial_snapshot_view_50_v1';
  const manifest = {
    format: 'atlas.quant.research_dataset',
    version,
    profile: source,
    components: []
  };
  const manifestText = canonical(manifest),
    root = h(manifestText);
  const spec = {
    profile: source,
    request: { profile: source },
    sources: {
      registry: [],
      financial: [{ selection: { selectedStateIds: ['model_fin_cash_asset_share'] } }]
    }
  };
  await f.db
    .prepare(
      'INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)'
    )
    .bind(
      planId,
      owner,
      randomUUID(),
      h('request'),
      h(canonical(spec)),
      'SYNTHETIC source metadata',
      canonical(spec),
      stamp
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,dataset_id,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?,?)"
    )
    .bind(compositionId, owner, planId, randomUUID(), h('compose'), datasetId, stamp, stamp)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed',?,?)"
    )
    .bind(
      stageId,
      compositionId,
      owner,
      randomUUID(),
      datasetId,
      root,
      manifestText,
      Buffer.byteLength(manifestText),
      stamp,
      stamp
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_research_datasets(id,owner,name,dataset_root,stage_id,status,scope,summary,created_at) VALUES(?,?,?,?,?,'ready',?,'{}',?)"
    )
    .bind(datasetId, owner, 'SYNTHETIC source metadata', root, stageId, canonical(scope), stamp)
    .run();
  await f.db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?)"
    )
    .bind(id, owner, 'SYNTHETIC graph claim fixture', dataSource, canonical(strategy), stamp, stamp)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,created_at) VALUES(?,?,?,1,'forecast',?)"
    )
    .bind(id, owner, randomUUID(), stamp)
    .run();
  if (relation)
    await f.db
      .prepare(
        'INSERT INTO quant_run_datasets(job_id,owner,dataset_id,dataset_root,profile,admission,created_at) VALUES(?,?,?,?,?,?,?)'
      )
      .bind(
        id,
        relationOwner ?? owner,
        datasetId,
        root,
        relationProfile,
        canonical({ strategyHash: h(canonical(strategy)) }),
        stamp
      )
      .run();
  return {
    id,
    owner,
    root,
    stageId,
    planId,
    strategy,
    datasetRef: {
      datasetId,
      datasetRoot: root,
      format: manifest.format,
      version
    }
  };
}

test('graph complete tuple alone claims exact graph source and bundle/2 without implicit legacy capabilities', async (t) => {
  const f = await fixture(t),
    source = await seedGraph(f);
  const reply = await json(await f.claim());
  assert.equal(reply.job.id, source.id);
  assert.equal(reply.job.dataSource, 'ready_dataset');
  assert.equal(reply.job.dataset, null);
  assert.equal(reply.job.jobKind, 'forecast');
  assert.deepEqual(reply.job.datasetRef, source.datasetRef);
  assert.deepEqual(reply.job.sourceEvidence, {
    datasetRef: source.datasetRef,
    admissionProfile: profile
  });
  assert.equal(reply.job.admissionProfile, profile);
  assert.equal(reply.job.datasetInputUrl, `/quant/api/runner/research-datasets/${source.id}/input`);
  assert.deepEqual(reply.job.resultTransport, {
    format: 'atlas.quant.financial_bundle',
    version: 2
  });
  assert.equal((await f.read(source.id)).status, 'running');
});

for (const field of Object.keys(GRAPH)) {
  test(`missing graph capability ${field} skips graph without reserving it`, async (t) => {
    const f = await fixture(t),
      graph = await seedGraph(f),
      ordinary = await seedBasic(f);
    const missing = { ...GRAPH };
    delete missing[field];
    const result = await json(await f.claim(missing));
    assert.equal(result.job.id, ordinary.id);
    assert.equal((await f.read(graph.id)).status, 'queued');
    assert.deepEqual(
      (await f.claims()).results.map((r) => r.job_id),
      [ordinary.id]
    );
    const requestId = randomUUID();
    assert.deepEqual(await json(await f.claim(missing, requestId)), {
      job: null,
      claim: { requestId, status: 'empty' }
    });
    assert.equal((await f.read(graph.id)).lease_token, null);
    assert.equal((await json(await f.claim(GRAPH, requestId))).job.id, graph.id);
  });
}

test('legacy financial tuple cannot imply graph capability, and graph tuple cannot imply legacy financial auto', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    legacy = await seedGraph(f, {
      version: 2,
      relationProfile: 'financial_fundamental_auto_50_v1'
    });
  const old = await json(await f.claim(LEGACY));
  assert.equal(old.job.id, legacy.id);
  assert.equal(old.job.datasetRef.version, 2);
  assert.deepEqual(old.job.resultTransport, {
    format: 'atlas.quant.financial_bundle',
    version: 1
  });
  assert.equal((await f.read(graph.id)).status, 'queued');
  assert.equal((await json(await f.claim(GRAPH))).job.id, graph.id);
  const pendingLegacy = await seedGraph(f, {
    version: 2,
    relationProfile: 'financial_fundamental_auto_50_v1'
  });
  assert.equal((await json(await f.claim(GRAPH))).job, null);
  assert.equal((await f.read(pendingLegacy.id)).status, 'queued');
});

for (const [label, bindings] of [
  ['default off', {}],
  [
    'legacy flags alone',
    {
      RESEARCH_DATASETS_ENABLED: 'true',
      FINANCIAL_DATASET_RESEARCH_ENABLED: 'true'
    }
  ],
  ['graph composition alone', { RESEARCH_DATASET_GRAPHS_ENABLED: 'true' }],
  ['graph research alone', { FINANCIAL_GRAPH_RESEARCH_ENABLED: 'true' }]
])
  test(`${label} cannot enable graph claims`, async (t) => {
    const f = await fixture(t, bindings),
      graph = await seedGraph(f);
    const result = await json(await f.claim(GRAPH));
    assert.equal(result.job, null);
    assert.equal((await f.read(graph.id)).status, 'queued');
    assert.deepEqual((await f.claims()).results, []);
    assert.equal((await json(await f.legacyClaim(GRAPH))).job, null);
  });

for (const [label, options] of [
  ['unknown profile', { relationProfile: 'financial_unregistered_profile' }],
  ['missing relation', { relation: false }],
  ['orphan relation on ordinary data source', { dataSource: 'demo' }],
  ['cross-owner relation', { relationOwner: 'different-owner' }]
])
  test(`queued ${label} is fenced before reserving work for both claim APIs`, async (t) => {
    const f = await fixture(t),
      wrong = await seedGraph(f, options);
    const ordinary = await seedBasic(f);
    assert.equal((await json(await f.claim(GRAPH))).job.id, ordinary.id);
    assert.equal((await f.read(wrong.id)).status, 'queued');
    const next = await seedBasic(f);
    assert.equal((await json(await f.legacyClaim(GRAPH))).job.id, next.id);
    assert.equal((await f.read(wrong.id)).lease_token, null);
    assert.deepEqual(
      (await f.claims()).results.map((r) => r.job_id),
      [ordinary.id]
    );
  });

test('legacy claim without requestId still requires full graph tuple and returns exact source', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f);
  assert.equal((await json(await f.legacyClaim(LEGACY))).job, null);
  const result = await json(await f.legacyClaim(GRAPH));
  assert.equal(result.job.id, graph.id);
  assert.deepEqual(result.job.datasetRef, graph.datasetRef);
  assert.equal(result.job.resultTransport.version, 2);
  assert.deepEqual((await f.claims()).results, []);
});

test('durable graph retry returns original job/lease and freshly rejects every withdrawn capability', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    requestId = randomUUID();
  const first = await json(await f.claim(GRAPH, requestId)),
    state = await f.read(graph.id);
  const next = await seedBasic(f);
  assert.deepEqual(await json(await f.claim(GRAPH, requestId)), first);
  for (const field of Object.keys(GRAPH)) {
    const missing = { ...GRAPH, [field]: [] };
    const blocked = await json(await f.claim(missing, requestId), 409);
    assert.equal(blocked.error.code, 'RUNNER_UPGRADE_REQUIRED');
    assert.equal((await f.read(graph.id)).lease_token, state.lease_token);
    assert.equal((await f.read(graph.id)).lease_until, state.lease_until);
    assert.equal((await f.read(next.id)).status, 'queued');
  }
  assert.equal(
    (await json(await f.claim(LEGACY, requestId), 409)).error.code,
    'RUNNER_UPGRADE_REQUIRED'
  );
  assert.deepEqual(await json(await f.claim(GRAPH, requestId)), first);
  assert.equal((await f.claims()).results.length, 1);
});

test('durable graph retry checks current server flags but does not rotate or allocate leases', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    requestId = randomUUID();
  const first = await json(await f.claim(GRAPH, requestId));
  const next = await seedBasic(f);
  for (const field of ['RESEARCH_DATASET_GRAPHS_ENABLED', 'FINANCIAL_GRAPH_RESEARCH_ENABLED']) {
    await f.flags({ ...ON, [field]: 'false' });
    assert.equal(
      (await json(await f.claim(GRAPH, requestId), 409)).error.code,
      'RUNNER_UPGRADE_REQUIRED'
    );
    assert.equal((await f.read(graph.id)).lease_token, first.job.leaseToken);
    assert.equal((await f.read(next.id)).status, 'queued');
  }
  await f.flags(ON);
  assert.deepEqual(await json(await f.claim(GRAPH, requestId)), first);
});

for (const mutation of ['missing', 'unknown', 'crossowner', 'orphan'])
  test(`durable running ${mutation} relation never releases graph payload or picks another job`, async (t) => {
    const f = await fixture(t),
      graph = await seedGraph(f),
      requestId = randomUUID();
    const first = await json(await f.claim(GRAPH, requestId)),
      next = await seedBasic(f);
    if (mutation === 'missing')
      await f.db.prepare('DELETE FROM quant_run_datasets WHERE job_id=?').bind(graph.id).run();
    if (mutation === 'unknown')
      await f.db
        .prepare('UPDATE quant_run_datasets SET profile=? WHERE job_id=?')
        .bind('unknown', graph.id)
        .run();
    if (mutation === 'crossowner')
      await f.db
        .prepare('UPDATE quant_run_datasets SET owner=? WHERE job_id=?')
        .bind('different-owner', graph.id)
        .run();
    if (mutation === 'orphan')
      await f.db.prepare("UPDATE jobs SET data_source='demo' WHERE id=?").bind(graph.id).run();
    assert.equal(
      (await json(await f.claim(GRAPH, requestId), 409)).error.code,
      'RUNNER_UPGRADE_REQUIRED'
    );
    assert.equal((await f.read(graph.id)).lease_token, first.job.leaseToken);
    assert.equal((await f.read(next.id)).status, 'queued');
  });

for (const terminal of ['completed', 'failed', 'cancelled'])
  test(`graph ${terminal} durable receipt remains recoverable without capability or enabled flag`, async (t) => {
    const f = await fixture(t),
      graph = await seedGraph(f),
      requestId = randomUUID();
    const first = await json(await f.claim(GRAPH, requestId)),
      next = await seedBasic(f);
    await f.db.prepare('UPDATE jobs SET status=? WHERE id=?').bind(terminal, graph.id).run();
    await f.flags({});
    assert.deepEqual(await json(await f.claim({}, requestId)), {
      job: null,
      claim: { requestId, status: terminal, jobId: graph.id }
    });
    assert.equal((await f.read(next.id)).status, 'queued');
    assert.equal((await f.read(graph.id)).lease_token, first.job.leaseToken);
  });

test('expired graph lease returns failed receipt without source read or new reservation', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    requestId = randomUUID();
  await json(await f.claim(GRAPH, requestId));
  const next = await seedBasic(f);
  await f.db
    .prepare("UPDATE jobs SET lease_until='2000-01-01T00:00:00Z' WHERE id=?")
    .bind(graph.id)
    .run();
  await f.db.prepare('DELETE FROM quant_run_datasets WHERE job_id=?').bind(graph.id).run();
  assert.deepEqual(await json(await f.claim({}, requestId)), {
    job: null,
    claim: { requestId, status: 'failed', jobId: graph.id }
  });
  assert.equal(JSON.parse((await f.read(graph.id)).error).code, 'RUNNER_INTERRUPTED');
  assert.equal((await f.read(next.id)).status, 'queued');
});

test('concurrent graph request retries reserve one job and keep one immutable lease', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    next = await seedGraph(f),
    requestId = randomUUID();
  const results = await Promise.all(
    Array.from({ length: 8 }, () => f.claim(GRAPH, requestId).then(json))
  );
  assert.equal(new Set(results.map((r) => r.job.id)).size, 1);
  assert.equal(new Set(results.map((r) => r.job.leaseToken)).size, 1);
  assert.equal((await f.claims()).results.length, 1);
  assert.deepEqual([(await f.read(graph.id)).status, (await f.read(next.id)).status].sort(), [
    'queued',
    'running'
  ]);
});

test('malformed graph capabilities and unauthenticated claims cannot reserve any job', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f);
  for (const field of Object.keys(GRAPH)) {
    const wrong = await json(await f.claim({ ...GRAPH, [field]: GRAPH[field][0] }), 400);
    assert.equal(wrong.error.code, 'INVALID_RUNNER_CAPABILITY');
  }
  await json(
    await f.call({ engineVersion: '0.9.0', ...GRAPH, requestId: randomUUID() }, false),
    401
  );
  assert.equal((await f.read(graph.id)).status, 'queued');
  assert.deepEqual((await f.claims()).results, []);
});

test('source recheck after reservation is fail-closed and exact request recovers the same lease once source is restored', async (t) => {
  const f = await fixture(t),
    graph = await seedGraph(f),
    requestId = randomUUID();
  await f.db
    .prepare('UPDATE quant_run_datasets SET dataset_root=? WHERE job_id=?')
    .bind('f'.repeat(64), graph.id)
    .run();
  await json(await f.claim(GRAPH, requestId), 404);
  const reserved = await f.read(graph.id);
  assert.equal(reserved.status, 'running');
  assert.ok(reserved.lease_token);
  const next = await seedBasic(f);
  await json(await f.claim(GRAPH, requestId), 404);
  assert.equal((await f.read(next.id)).status, 'queued');
  await f.db
    .prepare('UPDATE quant_run_datasets SET dataset_root=? WHERE job_id=?')
    .bind(graph.root, graph.id)
    .run();
  const recovered = await json(await f.claim(GRAPH, requestId));
  assert.equal(recovered.job.id, graph.id);
  assert.equal(recovered.job.leaseToken, reserved.lease_token);
  assert.equal((await f.read(graph.id)).lease_until, reserved.lease_until);
});
