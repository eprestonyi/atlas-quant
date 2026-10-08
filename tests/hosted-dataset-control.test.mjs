/** Actual Worker/D1/R2 control-plane checks. The source is explicitly a transport
 * fixture; no dataset is published, no numerical acceptance/provider/fit claimed. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import { bundleFixture, canonical } from './fixtures/bundle-fixture.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import { assertRegistry } from '../edge/datasets/transport.mjs';
import { validateTransform } from '../edge/datasets/sources.mjs';
const h = (x) => createHash('sha256').update(x).digest('hex');
const script = await buildWorkerSource();
async function fixture(t, enabled = true, financialResearch = false) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: {
      RUNNER_SECRET: 'dataset-fixture-only',
      RESEARCH_DATASETS_ENABLED: enabled ? 'true' : 'false',
      FINANCIAL_DATASET_RESEARCH_ENABLED: financialResearch ? 'true' : 'false',
    },
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB'),
    bucket = await mf.getR2Bucket('ARTIFACTS');
  await db.exec(
    (
      await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')
    ).replaceAll('\n', ' '),
  );
  async function call(path, { cookie, data, method = 'GET', lease } = {}) {
    return mf.dispatchFetch('https://dataset.test/quant/api' + path, {
      method,
      headers: {
        ...(cookie ? { cookie } : {}),
        ...(path.startsWith('/runner/')
          ? { authorization: 'Bearer dataset-fixture-only' }
          : {}),
        ...(lease ? { 'X-Dataset-Lease': lease } : {}),
        ...(data ? { 'content-type': 'application/json' } : {}),
      },
      body: data ? JSON.stringify(data) : undefined,
    });
  }
  async function owner() {
    const r = await call('/session'),
      data = await r.json();
    return {
      cookie: r.headers.get('set-cookie').split(';')[0],
      id: data.workspace.id,
    };
  }
  return { mf, db, bucket, call, owner };
}
async function json(r, status = 200) {
  assert.equal(r.status, status, await r.clone().text());
  return r.json();
}
test('dataset feature is default off and old financial/research claim remains empty', async (t) => {
  const f = await fixture(t, false),
    o = await f.owner();
  const cap = await json(
    await f.call('/dataset-capabilities', { cookie: o.cookie }),
  );
  assert.equal(cap.enabled, false);
  assert.equal(cap.researchBindingEnabled, false);
  await json(
    await f.call('/dataset-plans', {
      cookie: o.cookie,
      method: 'POST',
      data: {},
    }),
    503,
  );
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM quant_dataset_jobs').first()).n,
    0,
  );
});
test('explicit subset must be genuine, sorted, within source; no implicit truncation', () => {
  const scope = {
    symbols: ['000001.SZ', '000002.SZ'],
    start: '20230101',
    end: '20251231',
  };
  const exact = {
    kind: 'snapshot_scope_view',
    version: 1,
    mode: 'exact',
    ...scope,
  };
  assert.deepEqual(validateTransform(exact, scope), scope);
  assert.throws(() =>
    validateTransform({ ...exact, symbols: ['000001.SZ'] }, scope),
  );
  assert.throws(() =>
    validateTransform({ ...exact, mode: 'explicit_subset' }, scope),
  );
  assert.deepEqual(
    validateTransform(
      {
        ...exact,
        mode: 'explicit_subset',
        symbols: ['000001.SZ'],
        start: '20250101',
      },
      scope,
    ),
    { symbols: ['000001.SZ'], start: '20250101', end: '20251231' },
  );
  assert.throws(() =>
    validateTransform(
      { ...exact, mode: 'explicit_subset', start: '20220101' },
      scope,
    ),
  );
});
test('dataset claims isolated, empty receipt durable, idle heartbeat has no claim mutation', async (t) => {
  const f = await fixture(t),
    o = await f.owner(),
    heartbeat = {
      capability: 'research-dataset/1',
      engineVersion: 'fixture',
      state: 'idle',
    };
  assert.equal(
    (
      await json(
        await f.call('/runner/datasets/heartbeat', {
          method: 'POST',
          data: heartbeat,
        }),
      )
    ).canClaim,
    false,
  );
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM quant_dataset_claims').first())
      .n,
    0,
  );
  const requestId = randomUUID(),
    claimed = await json(
      await f.call('/runner/datasets/claim', {
        method: 'POST',
        data: {
          requestId,
          capability: 'research-dataset/1',
          engineVersion: 'fixture',
        },
      }),
    );
  assert.equal(claimed.job, null);
  assert.equal(claimed.claim.status, 'empty');
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM quant_dataset_claims').first())
      .n,
    1,
  );
  await json(
    await f.call('/runner/datasets/claim', {
      method: 'POST',
      data: {
        requestId,
        capability: 'financial-input/v1',
        engineVersion: 'fixture',
      },
    }),
    409,
  );
});
test('same-owner pinned sources plan and lease delivery; cross-owner refs and changed roots rejected', async (t) => {
  const f = await fixture(t),
    o = await f.owner(),
    other = await f.owner(),
    now = new Date().toISOString(),
    runId = randomUUID(),
    stageId = randomUUID(),
    calendarRef = randomUUID(),
    inputId = randomUUID(),
    preparationId = randomUUID(),
    publicationId = randomUUID();
  const b = bundleFixture({
      mutate: ({ snapshot }) => {
        snapshot.fingerprintVersion = 'research_input_v1';
      },
    }),
    parsed = await validateManifest(b.manifestText, b.bundleId);
  await f.db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'completed','demo',?,?,?)",
    )
    .bind(
      runId,
      o.id,
      'SYNTHETIC transport source',
      JSON.stringify(b.strategy),
      now,
      now,
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed',?,?)",
    )
    .bind(
      stageId,
      o.id,
      runId,
      randomUUID(),
      b.bundleId,
      b.manifestText,
      'source-manifest',
      JSON.stringify(parsed.metadata),
      now,
      now,
    )
    .run();
  await f.db
    .prepare(
      'INSERT INTO quant_bundle_runs(job_id,owner,stage_id) VALUES(?,?,?)',
    )
    .bind(runId, o.id, stageId)
    .run();
  const calendar = {
      kind: 'calendar',
      registryVersion: 1,
      scope: { calendarRoot: 'd'.repeat(64) },
      payload: {
        complete: true,
        coverage_start: '20250101',
        coverage_end: '20250103',
        sessions: ['20250102', '20250103'],
      },
      evidenceLevel: 'EXPLICIT_SYNTHETIC_TRANSPORT_FIXTURE',
    },
    raw = canonical(calendar);
  await f.bucket.put('calendar', raw);
  await f.db
    .prepare(
      "INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,status,created_at) VALUES(?,'calendar',?,'calendar',?,?,?,'active',?)",
    )
    .bind(calendarRef, o.id, h(raw), Buffer.byteLength(raw), '{}', now)
    .run();
  const roots = {
      inputRoot: 'a'.repeat(64),
      packRoot: 'b'.repeat(64),
      preparedRoot: 'c'.repeat(64),
      calendarRoot: 'd'.repeat(64),
    },
    selection = {
      symbols: ['000001.SZ'],
      start: '20250101',
      end: '20250103',
      selectedStateIds: ['model_fin_cash_asset_share'],
      announcementStart: '20250101',
      scope: 'consolidated',
      flowBasis: 'ytd',
    },
    packRaw = '{}',
    manifest = {
      summary: { input: { selection, unitPolicy: 'verified_only' } },
      collections: {
        package: {
          sha256: h(packRaw),
          byteLength: 2,
          chunks: [
            {
              ordinal: 0,
              startRow: null,
              rowCount: null,
              sha256: h(packRaw),
              byteLength: 2,
            },
          ],
        },
      },
    };
  await f.db
    .prepare(
      "INSERT INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'prepared',?,'[]',2,?,?,?,?)",
    )
    .bind(
      inputId,
      o.id,
      'SYNTHETIC metadata fixture',
      calendarRef,
      randomUUID(),
      'f'.repeat(64),
      now,
      now,
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'financial_prepare','completed','{}',?,?,?,?)",
    )
    .bind(randomUUID(), o.id, inputId, randomUUID(), 'f'.repeat(64), now, now)
    .run();
  const fj = await f.db
    .prepare('SELECT id FROM financial_jobs WHERE input_id=?')
    .bind(inputId)
    .first();
  await f.db
    .prepare(
      "INSERT INTO financial_publications(id,owner,job_id,manifest_text,manifest_hash,status,total_bytes,created_at,updated_at) VALUES(?,?,?,?,?,'committed',2,?,?)",
    )
    .bind(
      publicationId,
      o.id,
      fj.id,
      JSON.stringify(manifest),
      h(JSON.stringify(manifest)),
      now,
      now,
    )
    .run();
  await f.db
    .prepare(
      'INSERT INTO financial_preparations(id,owner,input_id,publication_id,roots,metadata,created_at) VALUES(?,?,?,?,?,?,?)',
    )
    .bind(
      preparationId,
      o.id,
      inputId,
      publicationId,
      JSON.stringify(roots),
      '{}',
      now,
    )
    .run();
  const request = {
    requestId: randomUUID(),
    name: 'SYNTHETIC metadata plan',
    profile: 'financial_snapshot_view_50_v1',
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
        start: '20250101',
        end: '20250103',
      },
    },
    financialInputs: [{ inputId, preparationId, ...roots }],
  };
  await json(
    await f.call('/dataset-plans', {
      cookie: other.cookie,
      method: 'POST',
      data: request,
    }),
    404,
  );
  const created = await json(
    await f.call('/dataset-plans', {
      cookie: o.cookie,
      method: 'POST',
      data: request,
    }),
    201,
  );
  assert.equal(created.plan.checks.semantic, 'pending');
  assert.equal(created.plan.marketCalendarRef, calendarRef);
  await json(
    await f.call('/dataset-plans', {
      cookie: o.cookie,
      method: 'POST',
      data: {
        ...request,
        requestId: randomUUID(),
        financialInputs: [
          { ...request.financialInputs[0], preparedRoot: 'e'.repeat(64) },
        ],
      },
    }),
    409,
  );
  await json(
    await f.call('/runner/datasets/heartbeat', {
      method: 'POST',
      data: {
        capability: 'research-dataset/1',
        engineVersion: 'fixture',
        state: 'idle',
      },
    }),
  );
  const started = await json(
    await f.call(`/dataset-plans/${created.plan.id}/start`, {
      cookie: o.cookie,
      method: 'POST',
      data: {
        requestId: randomUUID(),
        expectedPlanRoot: created.plan.planRoot,
      },
    }),
    202,
  );
  assert.equal(
    (
      await f.db
        .prepare("SELECT COUNT(*) n FROM jobs WHERE status='queued'")
        .first()
    ).n,
    0,
  );
  const claimed = await json(
    await f.call('/runner/datasets/claim', {
      method: 'POST',
      data: {
        requestId: randomUUID(),
        capability: 'research-dataset/1',
        engineVersion: 'fixture',
      },
    }),
  );
  assert.equal(claimed.job.id, started.preparation.id);
  const input = await json(
    await f.call(`/runner/datasets/jobs/${claimed.job.id}/input`, {
      lease: claimed.job.leaseToken,
    }),
  );
  assert.equal(input.plan.marketCalendarRef, calendarRef);
  assert.equal(input.registry.count, 1);
  const r = await f.call(
    `/runner/datasets/jobs/${claimed.job.id}/registry/${calendarRef}`,
    { lease: claimed.job.leaseToken },
  );
  assert.equal(r.status, 200, await r.clone().text());
  assert.equal(r.headers.get('cache-control'), 'no-store, no-transform');
  assert.equal(h(Buffer.from(await r.arrayBuffer())), h(raw));
  await json(
    await f.call(
      `/runner/datasets/jobs/${claimed.job.id}/registry/${randomUUID()}`,
      { lease: claimed.job.leaseToken },
    ),
    404,
  );
  await f.db
    .prepare(
      "UPDATE financial_registry_entries SET status='revoked' WHERE id=?",
    )
    .bind(calendarRef)
    .run();
  await json(
    await f.call(`/runner/datasets/jobs/${claimed.job.id}/input`, {
      lease: claimed.job.leaseToken,
    }),
    409,
  );
  assert.equal(
    (
      await f.db
        .prepare('SELECT COUNT(*) n FROM quant_research_datasets')
        .first()
    ).n,
    0,
  );
});

// Direct metadata seeding isolates admission/claim invariants. Actual numerical
// closure acceptance is tested separately through the compose publication path.
async function readyFixture(f, o) {
  const now = new Date().toISOString(),
    planId = randomUUID(),
    jobId = randomUUID(),
    stageId = randomUUID(),
    datasetId = randomUUID(),
    scope = { symbols: ['000001.SZ'], start: '20240101', end: '20241231' },
    manifest = {
      format: 'atlas.quant.research_dataset',
      version: 2,
      profile: 'financial_snapshot_view_50_v1',
      components: [],
    },
    text = canonical(manifest),
    root = h(text),
    spec = {
      sources: {
        registry: [],
        financial: [
          {
            selection: {
              selectedStateIds: [
                'model_fin_cash_asset_share',
                'model_fin_operating_margin',
              ],
            },
          },
        ],
      },
    };
  await f.db
    .prepare(
      'INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)',
    )
    .bind(
      planId,
      o.id,
      randomUUID(),
      'a'.repeat(64),
      'b'.repeat(64),
      'CONTROL FIXTURE',
      JSON.stringify(spec),
      now,
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?)",
    )
    .bind(jobId, o.id, planId, randomUUID(), 'c'.repeat(64), now, now)
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed',?,?)",
    )
    .bind(
      stageId,
      jobId,
      o.id,
      randomUUID(),
      datasetId,
      root,
      text,
      Buffer.byteLength(text),
      now,
      now,
    )
    .run();
  await f.db
    .prepare(
      "INSERT INTO quant_research_datasets(id,owner,name,dataset_root,stage_id,status,scope,summary,created_at) VALUES(?,?,?,?,?,'ready',?,? ,?)",
    )
    .bind(
      datasetId,
      o.id,
      'CONTROL FIXTURE',
      root,
      stageId,
      JSON.stringify(scope),
      JSON.stringify({
        selectedStateIds: [
          'model_fin_cash_asset_share',
          'model_fin_operating_margin',
        ],
      }),
      now,
    )
    .run();
  return {
    datasetRef: {
      datasetId,
      datasetRoot: root,
      format: 'atlas.quant.research_dataset',
      version: 2,
    },
    scope,
  };
}
const financialStrategy = (scope) => ({
  schemaVersion: 2,
  name: 'SYNTHETIC ADMISSION FIXTURE',
  universe: scope,
  research: { mode: 'statistical_quant', observationDays: 1 },
  factors: [
    { id: 'cash', expression: 'model_fin_cash_asset_share', role: 'predictor' },
  ],
  target: { kind: 'asset_price', horizonSessions: 5 },
  model: { family: 'fundamental', estimator: 'ridge' },
  execution: { enabled: false },
  portfolio: {},
  costs: {},
});
const financialCapabilities = {
  datasetFormats: ['atlas.quant.research_dataset/2'],
  snapshotFormats: ['financial_json_v1'],
  transportFormats: ['atlas.quant.financial_bundle/1'],
  engineVersion: '0.9.0',
};
test('ready financial dataset has exact owner/scope/profile, isolated claims, root-bound reads and no legacy completion', async (t) => {
  const f = await fixture(t, true, true),
    o = await f.owner(),
    other = await f.owner(),
    ready = await readyFixture(f, o),
    strategy = financialStrategy(ready.scope),
    request = {
      strategy,
      dataSource: 'ready_dataset',
      datasetRef: ready.datasetRef,
      admissionProfile: 'financial_snapshot_view_50_v1',
    };
  await json(
    await f.call('/runs', {
      cookie: other.cookie,
      method: 'POST',
      data: request,
    }),
    404,
  );
  await json(
    await f.call('/runs', {
      cookie: o.cookie,
      method: 'POST',
      data: {
        ...request,
        strategy: { ...strategy, execution: { enabled: true } },
      },
    }),
    400,
  );
  await json(
    await f.call('/runs', {
      cookie: o.cookie,
      method: 'POST',
      data: {
        ...request,
        strategy: {
          ...strategy,
          universe: { ...ready.scope, end: '20241230' },
        },
      },
    }),
    400,
  );
  await json(
    await f.call('/runs', {
      cookie: o.cookie,
      method: 'POST',
      data: { strategy, dataSource: 'demo' },
    }),
    400,
  );
  const queued = await json(
    await f.call('/runs', { cookie: o.cookie, method: 'POST', data: request }),
    202,
  );
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM quant_run_datasets').first()).n,
    1,
  );
  for (const partial of [
    {},
    { transportFormats: ['atlas.quant.bundle/1'] },
    { ...financialCapabilities, snapshotFormats: 'financial_json_v1' },
  ]) {
    const old = await json(
      await f.call('/runner/claim', {
        method: 'POST',
        data: { engineVersion: '0.9.0', requestId: randomUUID(), ...partial },
      }),
    );
    assert.equal(old.job, null);
  }
  const requestId = randomUUID(),
    claimed = await json(
      await f.call('/runner/claim', {
        method: 'POST',
        data: { ...financialCapabilities, requestId },
      }),
    );
  assert.equal(claimed.job.id, queued.job.id);
  assert.equal(claimed.job.dataset, null);
  assert.equal(
    claimed.job.resultTransport.format,
    'atlas.quant.financial_bundle',
  );
  assert.deepEqual(claimed.job.datasetRef, ready.datasetRef);
  await json(
    await f.call('/runner/claim', {
      method: 'POST',
      data: { requestId, engineVersion: '0.9.0' },
    }),
    409,
  );
  const path = '/runner/research-datasets/' + queued.job.id,
    lease = claimed.job.leaseToken;
  const input = await json(await f.call(path + '/input', { lease }));
  assert.deepEqual(input.sourceEvidence.datasetRef, ready.datasetRef);
  await json(
    await f.call(path + '/manifest?datasetRoot=' + 'f'.repeat(64), { lease }),
    409,
  );
  const manifest = await f.call(
    path + '/manifest?datasetRoot=' + ready.datasetRef.datasetRoot,
    { lease },
  );
  assert.equal(h(await manifest.text()), ready.datasetRef.datasetRoot);
  await json(await f.call(path + '/input', { lease: randomUUID() }), 409);
  await json(
    await f.call('/runner/snapshot', {
      method: 'POST',
      data: { id: queued.job.id, leaseToken: lease, snapshot: {} },
    }),
    409,
  );
  await json(
    await f.call('/runner/complete', {
      method: 'POST',
      data: { id: queued.job.id, leaseToken: lease, result: {} },
    }),
    409,
  );
  await f.db
    .prepare("UPDATE jobs SET status='cancelled' WHERE id=?")
    .bind(queued.job.id)
    .run();
  await json(await f.call(path + '/input', { lease }), 409);
});
test('financial forecast admission remains disabled independently of dataset preparation', async (t) => {
  const f = await fixture(t, true, false),
    o = await f.owner(),
    ready = await readyFixture(f, o);
  await json(
    await f.call('/runs', {
      cookie: o.cookie,
      method: 'POST',
      data: {
        strategy: financialStrategy(ready.scope),
        dataSource: 'ready_dataset',
        datasetRef: ready.datasetRef,
        admissionProfile: 'financial_snapshot_view_50_v1',
      },
    }),
    503,
  );
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM jobs').first()).n,
    0,
  );
});

test('dataset bindings persist per saved experiment version, copy and readback without a run', async (t) => {
  const f = await fixture(t, true, true),
    o = await f.owner(),
    other = await f.owner(),
    ready = await readyFixture(f, o),
    strategy = financialStrategy(ready.scope),
    binding = {
      datasetRef: ready.datasetRef,
      admissionProfile: 'financial_snapshot_view_50_v1',
    };
  const created = await json(
      await f.call('/statistical-quant/experiments', {
        cookie: o.cookie,
        method: 'POST',
        data: { strategy, ...binding },
      }),
      201,
    ),
    id = created.experiment.id;
  assert.deepEqual(
    created.experiment.datasetBinding.datasetRef,
    ready.datasetRef,
  );
  const loaded = await json(
    await f.call('/statistical-quant/experiments/' + id, { cookie: o.cookie }),
  );
  assert.deepEqual(
    loaded.experiment.datasetBinding.datasetRef,
    ready.datasetRef,
  );
  assert.equal(loaded.experiment.strategy.factors.length, 1);
  assert.deepEqual(loaded.experiment.datasetBinding.selectedStateIds, [
    'model_fin_cash_asset_share',
    'model_fin_operating_margin',
  ]);
  assert.equal(loaded.experiment.datasetBinding.stateDefinitions.length, 2);
  assert(
    loaded.experiment.datasetBinding.stateDefinitions.every(
      (x) => x.name && x.name !== x.id,
    ),
  );
  await json(
    await f.call('/statistical-quant/experiments/' + id, {
      cookie: other.cookie,
    }),
    404,
  );
  const updated = await json(
    await f.call('/statistical-quant/experiments/' + id, {
      cookie: o.cookie,
      method: 'PUT',
      data: { strategy: { ...strategy, name: 'new version' }, version: 1 },
    }),
  );
  assert.equal(updated.experiment.version, 2);
  assert.deepEqual(
    updated.experiment.datasetBinding.datasetRef,
    ready.datasetRef,
  );
  const bindings = await f.db
    .prepare('SELECT version FROM quant_experiment_datasets ORDER BY version')
    .all();
  assert.deepEqual(
    bindings.results.map((x) => x.version),
    [1, 2],
  );
  await json(
    await f.call('/statistical-quant/experiments/' + id, {
      cookie: o.cookie,
      method: 'PUT',
      data: { strategy, version: 1 },
    }),
    409,
  );
  const copy = await json(
    await f.call('/statistical-quant/experiments/' + id + '/copy', {
      cookie: o.cookie,
      method: 'POST',
      data: { name: 'Copied bound source' },
    }),
    201,
  );
  assert.deepEqual(copy.experiment.datasetBinding.datasetRef, ready.datasetRef);
  await json(
    await f.call('/statistical-quant/experiments/' + id + '/run', {
      cookie: o.cookie,
      method: 'POST',
      data: { version: 2, dataSource: 'demo' },
    }),
    409,
  );
  assert.equal(
    (await f.db.prepare('SELECT COUNT(*) n FROM jobs').first()).n,
    0,
  );
});

test('all grant freshness checks use one D1 query, including beyond the previous 64-reference page', async (t) => {
  const f = await fixture(t),
    owner = (await f.owner()).id,
    descriptors = Array.from({ length: 129 }, () => ({
      ref: randomUUID(),
      kind: 'unit_proof',
      sha256: 'a'.repeat(64),
      byteLength: 1,
    }));
  await f.db.batch(
    descriptors.map((d) =>
      f.db
        .prepare(
          "INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,status,created_at) VALUES(?,?,?,'fixture',?,1,'{}','active',?)",
        )
        .bind(d.ref, d.kind, owner, d.sha256, new Date().toISOString()),
    ),
  );
  let reads = 0;
  const env = {
    DB: {
      prepare(sql) {
        reads++;
        return f.db.prepare(sql);
      },
    },
  };
  await assertRegistry(env, owner, descriptors);
  assert.equal(reads, 1);
  await assert.rejects(assertRegistry(env, randomUUID(), descriptors), /授权/);
  await f.db
    .prepare(
      "UPDATE financial_registry_entries SET status='revoked' WHERE id=?",
    )
    .bind(descriptors[77].ref)
    .run();
  await assert.rejects(assertRegistry(env, owner, descriptors), /授权/);
});
