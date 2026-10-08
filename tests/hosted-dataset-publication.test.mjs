/** Real pure-core SYNTHETIC bytes through actual Worker, D1 and R2. No provider/fit. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
const directory = new URL('./fixtures/dataset-v2-core/', import.meta.url),
  read = (p) => fs.readFile(new URL(p, directory)),
  h = (x) => createHash('sha256').update(x).digest('hex');
const text = (await read('dataset/manifest.json')).toString(),
  manifest = JSON.parse(text),
  root = h(text),
  summary = JSON.parse(await read('summary.json')),
  coverage = JSON.parse(await read('dataset/parts/coverage/0.bin')),
  packageValue = JSON.parse(await read('dataset/parts/financialInput0/0.bin'));
const canonical = (v) =>
  Array.isArray(v)
    ? '[' + v.map(canonical).join(',') + ']'
    : v && typeof v === 'object'
      ? '{' +
        Object.keys(v)
          .sort()
          .map((k) => JSON.stringify(k) + ':' + canonical(v[k]))
          .join(',') +
        '}'
      : JSON.stringify(v);
const script = await buildWorkerSource();
async function environment(t) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: {
      RUNNER_SECRET: 'fixture-only',
      RESEARCH_DATASETS_ENABLED: 'true',
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
  const response = await mf.dispatchFetch(
      'https://fixture.test/quant/api/session',
    ),
    session = await response.json(),
    cookie = response.headers.get('set-cookie').split(';')[0];
  const owner = session.workspace.id,
    jobId = randomUUID(),
    planId = randomUUID(),
    lease = randomUUID(),
    now = new Date().toISOString(),
    until = new Date(Date.now() + 600000).toISOString(),
    calendarRef = summary.registryRefs[0],
    raw = await read('registry/' + calendarRef + '.json');
  await bucket.put('registry', raw);
  await db
    .prepare(
      "INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,status,created_at) VALUES(?,'calendar',?,'registry',?,?,'{}','active',?)",
    )
    .bind(calendarRef, owner, h(raw), raw.length, now)
    .run();
  const financialRoot = coverage.financial[0],
    selection = {
      ...packageValue.selection.universe,
      selectedStateIds: packageValue.selection.selectedStates,
      announcementStart: packageValue.selection.announcementStart,
      scope: packageValue.selection.scope,
      flowBasis: packageValue.selection.flowBasis,
    };
  const spec = {
    profile: 'financial_snapshot_view_50_v1',
    request: { profile: 'financial_snapshot_view_50_v1' },
    sources: {
      market: {
        scope: summary.scope,
        bundleId: summary.sourceBundleId,
        snapshot: { sha256: summary.sourceSnapshotSha256 },
      },
      marketCalendarRef: calendarRef,
      registry: [
        {
          ref: calendarRef,
          kind: 'calendar',
          sha256: h(raw),
          byteLength: raw.length,
        },
      ],
      financial: [
        {
          inputId: randomUUID(),
          preparationId: randomUUID(),
          calendarRef,
          proofRefs: [],
          roots: Object.fromEntries(
            ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot'].map(
              (k) => [k, financialRoot[k]],
            ),
          ),
          selection,
          unitPolicy: packageValue.unitPolicy,
          package: {
            sha256: manifest.components.find(
              (c) => c.componentId === 'financialInput0',
            ).payloadSha256,
            byteLength: manifest.components.find(
              (c) => c.componentId === 'financialInput0',
            ).byteLength,
          },
        },
      ],
    },
  };
  await db
    .prepare(
      'INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)',
    )
    .bind(
      planId,
      owner,
      randomUUID(),
      'a'.repeat(64),
      'b'.repeat(64),
      'SYNTHETIC real core',
      JSON.stringify(spec),
      now,
    )
    .run();
  await db
    .prepare(
      "INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,lease_token,lease_until,deadline,created_at,updated_at) VALUES(?,?,?,?,?,'running',?,?,?,?,?)",
    )
    .bind(
      jobId,
      owner,
      planId,
      randomUUID(),
      'c'.repeat(64),
      lease,
      until,
      until,
      now,
      now,
    )
    .run();
  const call = (suffix, method = 'POST', value, rawBody) =>
    mf.dispatchFetch(
      'https://fixture.test/quant/api/runner/datasets/jobs/' + jobId + suffix,
      {
        method,
        headers: {
          authorization: 'Bearer fixture-only',
          'X-Dataset-Lease': lease,
          'content-type': 'application/json',
        },
        body:
          rawBody ?? (value === undefined ? undefined : JSON.stringify(value)),
      },
    );
  return { mf, db, bucket, call, owner, jobId, lease, cookie };
}
async function response(r, status = 200) {
  assert.equal(r.status, status, await r.clone().text());
  return r.json();
}
async function publishParts(f, alternate = null) {
  const text =
      alternate?.text ?? (await read('dataset/manifest.json')).toString(),
    manifest = JSON.parse(text),
    root = h(text);
  const started = await response(
    await f.call('/publication', 'POST', {
      leaseToken: f.lease,
      datasetRoot: root,
      manifestText: text,
    }),
  );
  for (const c of manifest.components)
    for (const p of c.parts) {
      const raw =
        alternate?.componentId === c.componentId
          ? alternate.raw
          : await read(`dataset/parts/${c.componentId}/${p.ordinal}.bin`);
      const ack = await response(
        await f.call(
          `/publication/${started.publicationId}/parts/${c.componentId}/${p.ordinal}?datasetRoot=${root}`,
          'PUT',
          undefined,
          raw,
        ),
      );
      assert.equal(ack.sha256, p.sha256);
      assert.equal(ack.byteLength, p.byteLength);
    }
  return started;
}
test('actual core closure stages all bytes, publishes coverage atomically, and recovers exact ACK', async (t) => {
  const f = await environment(t),
    started = await publishParts(f);
  assert.equal(
    (
      await f.db
        .prepare('SELECT COUNT(*) n FROM quant_research_datasets')
        .first()
    ).n,
    0,
  );
  const done = await response(
    await f.call('/complete', 'POST', {
      leaseToken: f.lease,
      publicationId: started.publicationId,
      datasetRoot: root,
    }),
  );
  assert.equal(done.datasetRef.datasetRoot, root);
  const dataset = await f.db
    .prepare('SELECT * FROM quant_research_datasets')
    .first();
  assert.equal(dataset.status, 'ready');
  const rows = await f.db
    .prepare('SELECT * FROM quant_dataset_coverage ORDER BY ordinal')
    .all();
  assert.equal(rows.results.length, 16);
  assert.equal(JSON.parse(rows.results[0].metadata).okRows, 6);
  const archive = await f.mf.dispatchFetch(
    `https://fixture.test/quant/api/datasets/${dataset.id}/archive?datasetRoot=${root}`,
    { headers: { cookie: f.cookie } },
  );
  assert.equal(archive.status, 200);
  const archiveBytes = new Uint8Array(await archive.arrayBuffer());
  let offset = 0,
    entries = 0;
  while (archiveBytes[offset]) {
    const header = archiveBytes.slice(offset, offset + 512),
      name = new TextDecoder()
        .decode(header.slice(0, 100))
        .replace(/\0.*$/s, ''),
      size = parseInt(
        new TextDecoder().decode(header.slice(124, 136)).replace(/\0.*$/s, ''),
        8,
      ),
      content = archiveBytes.slice(offset + 512, offset + 512 + size);
    if (name === 'manifest.json') assert.equal(h(content), root);
    else {
      const match = /^parts\/([^/]+)\/(\d+)\.bin$/.exec(name);
      assert(match);
      assert.equal(
        h(content),
        manifest.components.find((x) => x.componentId === match[1]).parts[
          Number(match[2])
        ].sha256,
      );
    }
    offset += 512 + size + ((512 - (size % 512)) % 512);
    entries++;
  }
  assert.equal(entries, 9);
  assert.equal(archiveBytes.length - offset, 1024);
  assert.equal(JSON.parse(dataset.summary).originalAsPublishedVerified, false);
  assert.deepEqual(JSON.parse(dataset.summary).qualityFlags, [
    'USER_DECLARED_UNIT_ASSUMPTION',
  ]);
  await f.db
    .prepare("UPDATE financial_registry_entries SET status='revoked'")
    .run();
  assert.equal(
    (
      await response(
        await f.call('/complete', 'POST', {
          leaseToken: f.lease,
          publicationId: started.publicationId,
          datasetRoot: root,
        }),
      )
    ).idempotent,
    true,
  );
  const stored = await f.db.prepare('SELECT * FROM quant_dataset_parts').all();
  for (const row of stored.results) {
    const obj = await f.bucket.get(row.object_key);
    assert.equal(h(new Uint8Array(await obj.arrayBuffer())), row.sha256);
  }
});
test('missing or damaged part and expired/cancelled task cannot publish ready', async (t) => {
  const f = await environment(t),
    started = await publishParts(f),
    row = await f.db
      .prepare(
        "SELECT * FROM quant_dataset_parts WHERE component_id='researchRows'",
      )
      .first();
  await f.bucket.put(row.object_key, 'broken');
  await response(
    await f.call('/complete', 'POST', {
      leaseToken: f.lease,
      publicationId: started.publicationId,
      datasetRoot: root,
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
  await f.db
    .prepare(
      "UPDATE quant_dataset_jobs SET status='cancel_requested' WHERE id=?",
    )
    .bind(f.jobId)
    .run();
  await response(
    await f.call('/complete', 'POST', {
      leaseToken: f.lease,
      publicationId: started.publicationId,
      datasetRoot: root,
    }),
    409,
  );
});

// Rebuild every transport hash after changing a component: these are not simple
// damaged bytes. The semantic/index checks must reject an internally hashed lie.
async function alternateComponent(componentId, transform) {
  const raw = Buffer.from(
    transform((await read(`dataset/parts/${componentId}/0.bin`)).toString()),
  );
  const changed = structuredClone(manifest),
    roots = new Map();
  for (const c of changed.components) {
    const previous = c.componentRoot;
    if (c.componentId === componentId) {
      c.byteLength = raw.length;
      c.payloadSha256 = h(raw);
      c.parts = [{ ordinal: 0, byteLength: raw.length, sha256: h(raw) }];
    }
    c.dependencies = c.dependencies.map((x) => roots.get(x) ?? x).sort();
    const descriptor = { ...c };
    delete descriptor.componentRoot;
    c.componentRoot = h(canonical(descriptor));
    roots.set(previous, c.componentRoot);
  }
  return { componentId, raw, text: canonical(changed) };
}
for (const [label, componentId, transform] of [
  [
    'missing coverage coordinate',
    'coverage',
    (raw) => {
      const v = JSON.parse(raw);
      v.financial[0].securities[0].states.pop();
      return canonical(v);
    },
  ],
  [
    'self-hashed false usable count',
    'coverage',
    (raw) => {
      const v = JSON.parse(raw);
      v.financial[0].securities[0].states[0].okRows = 999;
      return canonical(v);
    },
  ],
  [
    'registry trailing separator',
    'registryEvidence',
    (raw) => raw.slice(0, -2) + ',]}',
  ],
])
  test(`self-consistent transport rejects ${label}`, async (t) => {
    const f = await environment(t),
      alternate = await alternateComponent(componentId, transform),
      started = await publishParts(f, alternate);
    const result = await f.call('/complete', 'POST', {
      leaseToken: f.lease,
      publicationId: started.publicationId,
      datasetRoot: h(alternate.text),
    });
    assert([400, 409].includes(result.status), await result.text());
    assert.equal(
      (
        await f.db
          .prepare('SELECT COUNT(*) n FROM quant_research_datasets')
          .first()
      ).n,
      0,
    );
  });
test('true HTTP archive rejects a missing R2 piece under ordinary client encoding', async (t) => {
  const f = await environment(t),
    started = await publishParts(f);
  const done = await response(
    await f.call('/complete', 'POST', {
      leaseToken: f.lease,
      publicationId: started.publicationId,
      datasetRoot: root,
    }),
  );
  const part = await f.db
    .prepare(
      "SELECT object_key FROM quant_dataset_parts WHERE component_id='researchRows'",
    )
    .first();
  await f.bucket.delete(part.object_key);
  const base = String(await f.mf.ready);
  const url = new URL(
    `/quant/api/datasets/${done.datasetRef.datasetId}/archive?datasetRoot=${root}`,
    base,
  );
  let refused = false;
  try {
    const r = await fetch(url, { headers: { cookie: f.cookie } });
    assert.equal(r.headers.get('content-encoding'), 'identity');
    await r.arrayBuffer();
    refused = r.status >= 400;
  } catch {
    refused = true;
  }
  assert.equal(
    refused,
    true,
    'A damaged attachment must not finish as a truncated successful body',
  );
});
