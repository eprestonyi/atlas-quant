/** Real Worker + D1/R2 protocol tests. Numerical runner output fixtures are
 * transport fixtures, not claims of a completed financial computation. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { spawn } from 'node:child_process';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
const digest = (x) =>
  createHash('sha256')
    .update(typeof x === 'string' || Buffer.isBuffer(x) ? x : JSON.stringify(x))
    .digest('hex');
const mf = new Miniflare({
  modules: true,
  script: await buildWorkerSource({ buildId: 'financial-http-tests' }),
  compatibilityDate: '2026-08-01',
  d1Databases: ['DB'],
  r2Buckets: ['ARTIFACTS'],
  bindings: {
    RUNNER_SECRET: 'financial-test-only',
    FINANCIAL_WORKSPACE_ENABLED: 'true',
  },
});
after(() => mf.dispose());
const db = await mf.getD1Database('DB'),
  bucket = await mf.getR2Bucket('ARTIFACTS');
await db.exec(
  (await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')).replaceAll('\n', ' ')
);
const base = 'https://financial.test/quant/api';
async function request(
  path,
  { method = 'GET', data, raw, cookie, runner = false, lease, headers = {} } = {}
) {
  if (data !== undefined || raw !== undefined) headers['content-type'] = 'application/json';
  if (cookie) headers.cookie = cookie;
  if (runner) headers.authorization = 'Bearer financial-test-only';
  if (lease) headers['X-Financial-Lease'] = lease;
  return mf.dispatchFetch(base + path, {
    method,
    headers,
    body: raw ?? (data === undefined ? undefined : JSON.stringify(data)),
  });
}
async function session() {
  return (await request('/session')).headers.get('set-cookie').split(';')[0];
}
const owner = await session(),
  other = await session();
const calendar = randomUUID(),
  reg = {
    kind: 'calendar',
    registryVersion: 1,
    payload: { sessions: ['20240102', '20240103'] },
    scope: {},
    evidenceLevel: 'EXPLICIT_PROTOCOL_FIXTURE',
  };
await bucket.put('financial-test/calendar', JSON.stringify(reg));
await db
  .prepare(
    'INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)'
  )
  .bind(
    calendar,
    'calendar',
    '*',
    'financial-test/calendar',
    digest(reg),
    Buffer.byteLength(JSON.stringify(reg)),
    JSON.stringify({
      label: 'Explicit protocol fixture',
      calendarRoot: 'a'.repeat(64),
      coverageStart: '20240101',
      coverageEnd: '20241231',
      complete: true,
    }),
    '2026-01-01T00:00:00Z'
  )
  .run();
const roots = {
  inputRoot: 'b'.repeat(64),
  packRoot: 'c'.repeat(64),
  preparedRoot: null,
  calendarRoot: 'a'.repeat(64),
};
async function alive() {
  const r = await request('/runner/financial/heartbeat', {
    method: 'POST',
    runner: true,
    data: {
      capability: 'financial-input/v1',
      engineVersion: '0.6.0',
      state: 'ready',
    },
  });
  assert.equal(r.status, 200, await r.text());
}
async function upload(text = '{"protocolFixture":true}') {
  const r = await request('/financial/inputs', {
    method: 'POST',
    cookie: owner,
    data: {
      requestId: randomUUID(),
      name: 'Explicit protocol source',
      byteLength: Buffer.byteLength(text),
      calendarRef: calendar,
      proofRefs: [],
    },
  });
  const data = await r.json();
  assert.equal(r.status, 201, JSON.stringify(data));
  const put = await request(`/financial/inputs/${data.input.id}/content`, {
    method: 'PUT',
    cookie: owner,
    raw: text,
  });
  assert.equal(put.status, 200, await put.text());
  return { id: data.input.id, text, hash: digest(text) };
}
async function queue(source, action = 'validate') {
  const data = {
      requestId: randomUUID(),
      ...(action === 'validate'
        ? { expectedUploadSha256: source.hash }
        : { expectedPackRoot: roots.packRoot }),
    },
    r = await request(`/financial/inputs/${source.id}/${action}`, {
      method: 'POST',
      cookie: owner,
      data,
    });
  const result = await r.json();
  assert.equal(r.status, 202, JSON.stringify(result));
  return result.job;
}
async function claimJob() {
  const data = {
      requestId: randomUUID(),
      capability: 'financial-input/v1',
      engineVersion: '0.6.0',
    },
    r = await request('/runner/financial/claim', {
      method: 'POST',
      runner: true,
      data,
    });
  assert.equal(r.status, 200);
  return { data, ...(await r.json()) };
}
function summary() {
  return {
    input: {
      source: {
        kind: 'fixture',
        provider: 'PROTOCOL_FIXTURE',
        snapshotRepresentation: 'normalized_provider_table_snapshot',
      },
      selection: {
        symbols: ['600690.SH'],
        start: '20240101',
        end: '20241231',
        announcementStart: '20240101',
        selectedStateIds: ['model_fin_cash_asset_share'],
        scope: 'consolidated',
        flowBasis: 'ytd',
      },
      unitPolicy: 'verified_only',
      counts: { snapshots: 1, sourceRows: 1, bindings: 0 },
      evidence: {
        availabilityEvidenceLevel: 'synthetic_disclosure_dates',
        originalAsPublishedVerified: false,
        revisionTimeVerified: false,
        unitAssumptions: false,
      },
    },
    validation: { status: 'passed', issues: [], missingPrerequisites: [] },
  };
}
function byteCollection(text) {
  return {
    encoding: 'bytes',
    rowCount: null,
    byteLength: Buffer.byteLength(text),
    sha256: digest(text),
    chunks: [
      {
        ordinal: 0,
        startRow: null,
        rowCount: null,
        byteLength: Buffer.byteLength(text),
        sha256: digest(text),
      },
    ],
  };
}
async function publishValidation(job, source) {
  const manifest = {
    format: 'atlas.quant.financial-result',
    version: 1,
    kind: 'validated',
    inputId: source.id,
    roots,
    summary: summary(),
    collections: { package: byteCollection(source.text) },
  };
  const start = await request('/runner/financial/publications/begin', {
      method: 'POST',
      runner: true,
      data: { jobId: job.id, leaseToken: job.leaseToken, manifest },
    }),
    pub = await start.json();
  assert.equal(start.status, 200, JSON.stringify(pub));
  const put = await request(
    `/runner/financial/publications/${pub.publicationId}/chunks/package/0?manifestSha256=${pub.manifestSha256}`,
    { method: 'PUT', runner: true, lease: job.leaseToken, raw: source.text }
  );
  assert.equal(put.status, 200, await put.text());
  const data = {
      jobId: job.id,
      leaseToken: job.leaseToken,
      publicationId: pub.publicationId,
      manifestSha256: pub.manifestSha256,
    },
    done = await request('/runner/financial/complete', {
      method: 'POST',
      runner: true,
      data,
    });
  const completed = await done.json();
  assert.equal(done.status, 200, JSON.stringify(completed));
  return { data, completed, pub };
}

test('definitions are real core exports; capability requires separate financial heartbeat', async () => {
  const d = await (await request('/financial/definitions', { cookie: owner })).json();
  assert.equal(d.items.length, 16);
  assert(d.items.every((x) => x.availability.status === 'definition_only'));
  const cap = await (await request('/financial/capabilities', { cookie: owner })).json();
  assert.equal(cap.operations.researchBinding, false);
  assert.equal(cap.runner.online, false);
  await alive();
  assert.equal(
    (await (await request('/financial/capabilities', { cookie: owner })).json()).runner.online,
    true
  );
});
test('upload receipts preserve exact bytes, reject replacement, unknown trust and other owner reads', async () => {
  const src = await upload();
  assert.equal((await request(`/financial/inputs/${src.id}`, { cookie: other })).status, 404);
  const retry = await request(`/financial/inputs/${src.id}/content`, {
    method: 'PUT',
    cookie: owner,
    raw: src.text,
  });
  assert.equal((await retry.json()).idempotent, true);
  assert.equal(
    (
      await request(`/financial/inputs/${src.id}/content`, {
        method: 'PUT',
        cookie: owner,
        raw: src.text.replace('true', 'null'),
      })
    ).status,
    409
  );
  const bad = await request('/financial/inputs', {
    method: 'POST',
    cookie: owner,
    data: {
      requestId: randomUUID(),
      name: 'Bad',
      byteLength: 10,
      calendarRef: calendar,
      proofRefs: [],
      trusted_unit_proofs: true,
    },
  });
  assert.equal(bad.status, 400);
  const download = await request(
    `/financial/inputs/${src.id}/source-download?uploadSha256=${src.hash}`,
    { cookie: owner }
  );
  assert.equal(await download.text(), src.text);
});
test('dedicated claims preserve lost ACK, source bytes, registry grants and terminal idempotency', async () => {
  await alive();
  const source = await upload(),
    queued = await queue(source);
  const old = await request('/runner/claim', {
    method: 'POST',
    runner: true,
    data: { engineVersion: '0.6.0', requestId: randomUUID() },
  });
  assert.equal((await old.json()).job, null);
  const c = await claimJob();
  assert.equal(c.job.id, queued.id);
  const duplicate = await (
    await request('/runner/financial/claim', {
      method: 'POST',
      runner: true,
      data: c.data,
    })
  ).json();
  assert.deepEqual(duplicate, { claim: c.claim, job: c.job });
  const input = await (
    await request(`/runner/financial/jobs/${c.job.id}/input`, {
      runner: true,
      lease: c.job.leaseToken,
    })
  ).json();
  assert.equal(input.source.sha256, source.hash);
  assert.equal(input.calendar.ref, calendar);
  assert.equal(
    await (
      await request(`/runner/financial/jobs/${c.job.id}/source`, {
        runner: true,
        lease: c.job.leaseToken,
      })
    ).text(),
    source.text
  );
  assert.equal(
    (
      await request(`/runner/financial/jobs/${c.job.id}/registry/${randomUUID()}`, {
        runner: true,
        lease: c.job.leaseToken,
      })
    ).status,
    404
  );
  const pub = await publishValidation(c.job, source);
  assert.equal(pub.completed.status, 'completed');
  const repeat = await (
    await request('/runner/financial/complete', {
      method: 'POST',
      runner: true,
      data: pub.data,
    })
  ).json();
  assert.equal(repeat.idempotent, true);
  assert.equal(
    (await (await request(`/financial/inputs/${source.id}`, { cookie: owner })).json()).input
      .status,
    'ready_to_prepare'
  );
  assert.equal(
    (
      await request(`/financial/inputs/${source.id}/download?packRoot=${roots.packRoot}`, {
        cookie: other,
      })
    ).status,
    404
  );
  const downloaded = await request(
    `/financial/inputs/${source.id}/download?packRoot=${roots.packRoot}`,
    { cookie: owner }
  );
  assert.equal(await downloaded.text(), source.text);
  const terminal = await (
    await request('/runner/financial/claim', {
      method: 'POST',
      runner: true,
      data: c.data,
    })
  ).json();
  assert.equal(terminal.job, null);
  assert.equal(terminal.claim.status, 'completed');
});
test('empty claims remain empty and queued cancellation cannot produce a result', async () => {
  const empty = await claimJob();
  assert.equal(empty.job, null);
  const source = await upload(),
    queued = await queue(source);
  assert.equal(
    (
      await (
        await request('/runner/financial/claim', {
          method: 'POST',
          runner: true,
          data: empty.data,
        })
      ).json()
    ).job,
    null
  );
  const cancelled = await request(`/financial/jobs/${queued.id}/cancel`, {
    method: 'POST',
    cookie: owner,
    data: {},
  });
  assert.equal((await cancelled.json()).status, 'cancelled');
  assert.equal((await claimJob()).job, null);
});

function recordCollection(records) {
  const raw = JSON.stringify(records);
  return {
    raw,
    descriptor: {
      encoding: 'json_records',
      rowCount: records.length,
      byteLength: records.length ? Buffer.byteLength(raw) : 0,
      chunks: records.length
        ? [
            {
              ordinal: 0,
              startRow: 0,
              rowCount: records.length,
              byteLength: Buffer.byteLength(raw),
              sha256: digest(raw),
            },
          ]
        : [],
    },
  };
}
async function preparedFixture() {
  await alive();
  const source = await upload();
  await queue(source);
  const validation = await claimJob();
  await publishValidation(validation.job, source);
  await queue(source, 'prepare');
  const claimed = await claimJob(),
    state = 'model_fin_cash_asset_share';
  const events = ['20240102', '20240103'].map((date, n) => ({
    id: digest('protocol-event-' + date),
    symbol: '600690.SH',
    stateId: state,
    computedAsOf: date,
    dependencyStart: n,
    dependencyCount: 1,
    result: {
      status: 'ok',
      decimalValue: '0.5',
      decimalPrecision: 34,
      rounding: 'ROUND_HALF_EVEN',
      unit: 'ratio',
      periodEnd: '20231231',
      availableDate: '20240102',
      reasonCodes: [],
      formulaVersion: 'financial_states_v1',
      asOf: date,
      qualityFlags: [],
      unitEvidenceLevels: ['user_declared_assumption'],
      declarationHashes: [],
      unitVerified: false,
      lineageHash: digest('protocol-lineage-' + date),
    },
  }));
  const dependencies = events.map((event, index) => ({
    eventId: event.id,
    index: 0,
    dependency: {
      field_id: 'balancesheet.total_assets',
      period_end: '20231231',
      available_date: '20240102',
      announcement_date: '20240101',
      raw_decimal: '100',
      raw_unit: 'CNY',
      currency: 'CNY',
      scope: 'consolidated',
      basis: 'point',
      source_snapshot: 'e'.repeat(64),
      record_hash: 'f'.repeat(64),
      unit_evidence: 'Explicit transport fixture',
      unit_verified: false,
      evidence_level: 'user_declared_assumption',
      quality_flags: ['USER_DECLARED_UNIT_ASSUMPTION'],
    },
  }));
  const lists = {
    panel: events.map((x) => ({
      ts_code: x.symbol,
      trade_date: x.computedAsOf,
      [state]: 0.5,
      [state + '__available_date']: '20240102',
    })),
    events,
    dependencies,
    assignments: events.map((x) => ({
      symbol: x.symbol,
      from: x.computedAsOf,
      through: x.computedAsOf,
      states: { [state]: x.id },
    })),
    coverage: [
      {
        symbol: '600690.SH',
        stateId: state,
        status: 'available',
        okRows: 2,
        missingRows: 0,
        firstAvailable: '20240102',
        lastAvailable: '20240102',
        firstObserved: '20240102',
        lastObserved: '20240103',
        latestPeriodEnd: '20231231',
        latestAvailableDate: '20240102',
        lastPreparedDate: '20240103',
        latestAgeCalendarDays: 3,
        periodEnds: ['20231231'],
        reasonCounts: {},
      },
    ],
  };
  const records = Object.fromEntries(
    Object.entries(lists).map(([k, v]) => [k, recordCollection(v)])
  );
  const manifest = {
    format: 'atlas.quant.financial-result',
    version: 1,
    kind: 'prepared',
    inputId: source.id,
    roots: { ...roots, preparedRoot: 'd'.repeat(64) },
    summary: {
      ...summary(),
      preparation: {
        formulaVersion: 'financial_states_v1',
        policyVersion: 'statement_asof_v1',
        qualityFlags: [],
        availabilityEvidenceLevel: 'synthetic_disclosure_dates',
        originalAsPublishedVerified: false,
        revisionTimeVerified: false,
        resourceAccounting: 'conservative_serialized_expansion_v1_not_rss',
      },
      hasUsableStates: true,
    },
    collections: {
      package: byteCollection(source.text),
      ...Object.fromEntries(Object.entries(records).map(([k, v]) => [k, v.descriptor])),
    },
  };
  const begin = await request('/runner/financial/publications/begin', {
      method: 'POST',
      runner: true,
      data: {
        jobId: claimed.job.id,
        leaseToken: claimed.job.leaseToken,
        manifest,
      },
    }),
    pub = await begin.json();
  assert.equal(begin.status, 200, JSON.stringify(pub));
  for (const [collection, raw] of [
    ['package', source.text],
    ...Object.entries(records).map(([k, v]) => [k, v.raw]),
  ]) {
    const r = await request(
      `/runner/financial/publications/${pub.publicationId}/chunks/${collection}/0?manifestSha256=${pub.manifestSha256}`,
      { method: 'PUT', runner: true, lease: claimed.job.leaseToken, raw }
    );
    assert.equal(r.status, 200, await r.text());
  }
  return { source, job: claimed.job, pub, events, records, manifest };
}
test('prepared collection pages pin roots, retain disclosed and observed dates, reconstruct complete event evidence', async () => {
  const f = await preparedFixture(),
    data = {
      jobId: f.job.id,
      leaseToken: f.job.leaseToken,
      publicationId: f.pub.publicationId,
      manifestSha256: f.pub.manifestSha256,
    };
  const completed = await request('/runner/financial/complete', {
      method: 'POST',
      runner: true,
      data,
    }),
    result = await completed.json();
  assert.equal(completed.status, 200, JSON.stringify(result));
  const p = `/financial/preparations/${result.preparationId}`,
    root = 'd'.repeat(64),
    info = await (await request(p, { cookie: owner })).json();
  assert.equal(info.readiness.researchBindingEnabled, false);
  assert.equal(info.collections.events.total, 2);
  assert.equal((await request(p, { cookie: other })).status, 404);
  assert.equal(
    (
      await request(p + '/events?preparedRoot=' + roots.inputRoot, {
        cookie: owner,
      })
    ).status,
    409
  );
  const coverage = await (
    await request(p + '/coverage?preparedRoot=' + root, { cookie: owner })
  ).json();
  assert.equal(coverage.items[0].lastAvailable, '20240102');
  assert.equal(coverage.items[0].lastObserved, '20240103');
  const first = await (
    await request(p + '/events?preparedRoot=' + root + '&limit=1', {
      cookie: owner,
    })
  ).json();
  assert.equal(first.items.length, 1);
  assert(first.nextCursor);
  const second = await (
    await request(
      p +
        '/events?preparedRoot=' +
        root +
        '&limit=1&cursor=' +
        encodeURIComponent(first.nextCursor),
      { cookie: owner }
    )
  ).json();
  assert.equal(second.items[0].eventId, f.events[1].id);
  assert.equal(second.nextCursor, null);
  assert.equal(
    (
      await request(
        p +
          '/events?preparedRoot=' +
          root +
          '&symbol=600000.SH&cursor=' +
          encodeURIComponent(first.nextCursor),
        { cookie: owner }
      )
    ).status,
    409
  );
  const deps = await (
    await request(p + '/events/' + f.events[0].id + '/dependencies?preparedRoot=' + root, {
      cookie: owner,
    })
  ).json();
  assert.equal(deps.items[0].rawDecimal, '100');
  const full = await (
    await request(p + '/events/' + f.events[0].id + '/download?preparedRoot=' + root, {
      cookie: owner,
    })
  ).json();
  assert.equal(full.id, f.events[0].id);
  assert.equal(full.result.dependencies.length, 1);
  assert.equal(full.result.dependencies[0].raw_decimal, '100');
  assert(!Object.hasOwn(full, 'dependencyStart'));
});
test('cancel after staging prevents publication and late heartbeat cannot resurrect expired leases', async () => {
  const f = await preparedFixture();
  const cancel = await request(`/financial/jobs/${f.job.id}/cancel`, {
    method: 'POST',
    cookie: owner,
    data: {},
  });
  assert.equal((await cancel.json()).status, 'cancel_requested');
  const done = await request('/runner/financial/complete', {
    method: 'POST',
    runner: true,
    data: {
      jobId: f.job.id,
      leaseToken: f.job.leaseToken,
      publicationId: f.pub.publicationId,
      manifestSha256: f.pub.manifestSha256,
    },
  });
  assert.equal(done.status, 409);
  const failed = await request('/runner/financial/fail', {
    method: 'POST',
    runner: true,
    data: {
      jobId: f.job.id,
      leaseToken: f.job.leaseToken,
      error: { code: 'CANCELLED', message: 'Explicit test cancellation' },
    },
  });
  assert.equal((await failed.json()).status, 'cancelled');
  const row = await db
    .prepare('SELECT status FROM financial_publications WHERE id=?')
    .bind(f.pub.publicationId)
    .first();
  assert.equal(row.status, 'staging');
  const source = await upload();
  await queue(source);
  const claimed = await claimJob();
  await db
    .prepare("UPDATE financial_jobs SET lease_until='2000-01-01T00:00:00.000Z' WHERE id=?")
    .bind(claimed.job.id)
    .run();
  const heartbeat = await request('/runner/financial/heartbeat', {
    method: 'POST',
    runner: true,
    data: {
      capability: 'financial-input/v1',
      engineVersion: '0.6.0',
      state: 'working',
      jobId: claimed.job.id,
      leaseToken: claimed.job.leaseToken,
      phase: 'checking_inputs',
    },
  });
  assert.equal(heartbeat.status, 409);
  assert.equal(
    (await db.prepare('SELECT status FROM financial_jobs WHERE id=?').bind(claimed.job.id).first())
      .status,
    'failed'
  );
});

async function core(payload) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.env.PYTHON || '.venv/bin/python',
      ['tests/fixtures/financial-publication.py'],
      {
        env: { ...process.env, PYTHONPATH: 'engine:engine/tests' },
        stdio: ['pipe', 'pipe', 'pipe'],
      }
    );
    let output = '',
      error = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(Error('Bounded core fixture timed out'));
    }, 30000);
    child.stdout.on('data', (part) => {
      output += part;
      if (output.length > 4 * 1024 * 1024) {
        child.kill();
        reject(Error('Core fixture output budget'));
      }
    });
    child.stderr.on('data', (part) => {
      error += part;
    });
    child.on('error', reject);
    child.on('close', (code) => {
      clearTimeout(timer);
      try {
        assert.equal(code, 0, error);
        resolve(JSON.parse(output));
      } catch (err) {
        reject(err);
      }
    });
    child.stdin.on('error', reject);
    child.stdin.end(JSON.stringify(payload));
  });
}
async function consumeWithCore(job) {
    const metadata = await (
      await request(`/runner/financial/jobs/${job.id}/input`, {
        runner: true,
        lease: job.leaseToken,
      })
    ).json();
    const source = Buffer.from(
      await (
        await request(`/runner/financial/jobs/${job.id}/source`, {
          runner: true,
          lease: job.leaseToken,
        })
      ).arrayBuffer()
    );
    const registry = {};
    for (const descriptor of [metadata.calendar, ...metadata.proofs])
      registry[descriptor.ref] = Buffer.from(
        await (
          await request(`/runner/financial/jobs/${job.id}/registry/${descriptor.ref}`, {
            runner: true,
            lease: job.leaseToken,
          })
        ).arrayBuffer()
      ).toString('base64');
    const payload = { command: 'compute', job, metadata, raw: source.toString('base64'), registry };
    const publication = await core(payload);
    const begin = await request('/runner/financial/publications/begin', {
        method: 'POST',
        runner: true,
        data: { jobId: job.id, leaseToken: job.leaseToken, manifest: publication.manifest },
      }),
      pub = await begin.json();
    assert.equal(begin.status, 200, JSON.stringify(pub));
    for (const chunk of publication.chunks) {
      const put = await request(
        `/runner/financial/publications/${pub.publicationId}/chunks/${chunk.collection}/${chunk.ordinal}?manifestSha256=${pub.manifestSha256}`,
        {
          method: 'PUT',
          runner: true,
          lease: job.leaseToken,
          raw: Buffer.from(chunk.raw, 'base64'),
        }
      );
      assert.equal(put.status, 200, await put.text());
    }
    const complete = await request('/runner/financial/complete', {
        method: 'POST',
        runner: true,
        data: {
          jobId: job.id,
          leaseToken: job.leaseToken,
          publicationId: pub.publicationId,
          manifestSha256: pub.manifestSha256,
        },
      }),
      done = await complete.json();
    assert.equal(complete.status, 200, JSON.stringify(done));
    return { ...publication, done, pub };
}
test('actual financial Python core publishes sixteen synthetic states through D1/R2 and exact lineage readback', async () => {
  const fixture = await core({ command: 'source' }),
    cal = fixture.metadata.calendar.ref;
  for (const [ref, data] of Object.entries(fixture.registry)) {
    const raw = Buffer.from(data, 'base64'),
      value = JSON.parse(raw);
    await bucket.put('actual-core-registry/' + ref, raw);
    await db
      .prepare(
        'INSERT OR REPLACE INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)'
      )
      .bind(
        ref,
        value.kind,
        '*',
        'actual-core-registry/' + ref,
        digest(raw),
        raw.length,
        JSON.stringify({
          label: 'Actual core / explicitly synthetic calendar',
          calendarRoot: value.scope.calendarRoot,
        }),
        '2026-01-01T00:00:00Z'
      )
      .run();
  }
  const raw = Buffer.from(fixture.raw, 'base64');
  const created = await request('/financial/inputs', {
      method: 'POST',
      cookie: owner,
      data: {
        requestId: randomUUID(),
        name: 'Actual core synthetic financial evidence',
        byteLength: raw.length,
        calendarRef: cal,
        proofRefs: [],
      },
    }),
    input = await created.json();
  assert.equal(created.status, 201, JSON.stringify(input));
  assert.equal(
    (
      await request(`/financial/inputs/${input.input.id}/content`, {
        method: 'PUT',
        cookie: owner,
        raw,
      })
    ).status,
    200
  );
  await alive();
  let previous;
  for (const action of ['validate', 'prepare']) {
    const queued = await request(`/financial/inputs/${input.input.id}/${action}`, {
      method: 'POST',
      cookie: owner,
      data: {
        requestId: randomUUID(),
        ...(action === 'validate'
          ? { expectedUploadSha256: digest(raw) }
          : { expectedPackRoot: previous.manifest.roots.packRoot }),
      },
    });
    assert.equal(queued.status, 202, await queued.text());
    const claimed = await claimJob(),
      job = claimed.job;
    previous = await consumeWithCore(job);
  }
  const p = `/financial/preparations/${previous.done.preparationId}`,
    root = previous.manifest.roots.preparedRoot;
  const coverage = await (
    await request(`${p}/coverage?preparedRoot=${root}`, { cookie: owner })
  ).json();
  assert.equal(coverage.total, 16);
  assert.equal(coverage.items.filter((x) => x.status === 'available').length, 16);
  const eventPage = await (
    await request(`${p}/events?preparedRoot=${root}&limit=1`, { cookie: owner })
  ).json();
  assert.equal(eventPage.items.length, 1);
  assert(eventPage.nextCursor);
  const expectedEvents = previous.chunks
      .filter((x) => x.collection === 'events')
      .flatMap((x) => JSON.parse(Buffer.from(x.raw, 'base64'))),
    expectedDependencies = previous.chunks
      .filter((x) => x.collection === 'dependencies')
      .flatMap((x) => JSON.parse(Buffer.from(x.raw, 'base64')));
  const wanted = expectedEvents.find((x) => x.id === eventPage.items[0].eventId),
    downloaded = await (
      await request(`${p}/events/${wanted.id}/download?preparedRoot=${root}`, { cookie: owner })
    ).json();
  const { dependencyStart, dependencyCount, ...expected } = wanted;
  expected.result = {
    ...expected.result,
    dependencies: expectedDependencies
      .slice(dependencyStart, dependencyStart + dependencyCount)
      .map((x) => x.dependency),
  };
  assert.deepEqual(downloaded, expected);
  assert.equal(downloaded.result.unitVerified, false);
  assert(downloaded.result.qualityFlags.includes('USER_DECLARED_UNIT_ASSUMPTION'));
  const packageDownload = Buffer.from(
    await (
      await request(
        `/financial/inputs/${input.input.id}/download?packRoot=${previous.manifest.roots.packRoot}`,
        { cookie: owner }
      )
    ).arrayBuffer()
  );
  assert.equal(digest(packageDownload), previous.manifest.collections.package.sha256);
  assert.equal(
    (await (await request(p, { cookie: owner })).json()).readiness.researchBindingEnabled,
    false
  );
});

test('concurrent identical upload, publication and chunk receipts retain one immutable winner', async () => {
  const text = '{"concurrency":"same immutable bytes"}',
    requestId = randomUUID();
  const create = () =>
    request('/financial/inputs', {
      method: 'POST',
      cookie: owner,
      data: {
        requestId,
        name: 'Concurrent upload receipt',
        byteLength: Buffer.byteLength(text),
        calendarRef: calendar,
        proofRefs: [],
      },
    });
  const created = await Promise.all([create(), create()]);
  assert(created.every((x) => x.status === 201));
  const receipts = await Promise.all(created.map((x) => x.json()));
  assert.equal(receipts[0].input.id, receipts[1].input.id);
  const source = { id: receipts[0].input.id, text, hash: digest(text) };
  const uploads = await Promise.all(
    [1, 2].map(() =>
      request(`/financial/inputs/${source.id}/content`, { method: 'PUT', cookie: owner, raw: text })
    )
  );
  assert(uploads.every((x) => x.status === 200));
  await queue(source);
  const { job } = await claimJob(),
    manifest = {
      format: 'atlas.quant.financial-result',
      version: 1,
      kind: 'validated',
      inputId: source.id,
      roots,
      summary: summary(),
      collections: { package: byteCollection(text) },
    };
  const begin = () =>
    request('/runner/financial/publications/begin', {
      method: 'POST',
      runner: true,
      data: { jobId: job.id, leaseToken: job.leaseToken, manifest },
    });
  const begun = await Promise.all([begin(), begin()]);
  assert(begun.every((x) => x.status === 200));
  const pubs = await Promise.all(begun.map((x) => x.json()));
  assert.equal(pubs[0].publicationId, pubs[1].publicationId);
  const pub = pubs[0],
    chunk = `/runner/financial/publications/${pub.publicationId}/chunks/package/0?manifestSha256=${pub.manifestSha256}`;
  const parts = await Promise.all(
    [1, 2].map(() =>
      request(chunk, { method: 'PUT', runner: true, lease: job.leaseToken, raw: text })
    )
  );
  assert(parts.every((x) => x.status === 200));
  const complete = () =>
    request('/runner/financial/complete', {
      method: 'POST',
      runner: true,
      data: {
        jobId: job.id,
        leaseToken: job.leaseToken,
        publicationId: pub.publicationId,
        manifestSha256: pub.manifestSha256,
      },
    });
  const completed = await Promise.all([complete(), complete()]);
  assert(completed.every((x) => x.status === 200));
  assert.equal(
    await (
      await request(`/financial/inputs/${source.id}/download?packRoot=${roots.packRoot}`, {
        cookie: owner,
      })
    ).text(),
    text
  );
  const row = await db
    .prepare('SELECT COUNT(*) n FROM financial_chunks WHERE publication_id=?')
    .bind(pub.publicationId)
    .first();
  assert.equal(row.n, 1);
});
test('corrupted committed R2 chunks fail the download stream instead of returning a truncated valid package', async () => {
  const source = await upload();
  await queue(source);
  const { job } = await claimJob(),
    published = await publishValidation(job, source);
  const chunk = await db
    .prepare("SELECT * FROM financial_chunks WHERE publication_id=? AND collection='package'")
    .bind(published.pub.publicationId)
    .first();
  await bucket.put(chunk.object_key, '!'.repeat(chunk.byte_length));
  let rejected=false;
  try {
    const url=new URL(`/quant/api/financial/inputs/${source.id}/download?packRoot=${roots.packRoot}`,await mf.ready);
    const response=await fetch(url,{headers:{cookie:owner}});
    rejected=response.status!==200;
    await response.arrayBuffer();
  }catch{rejected=true;}
  assert(rejected,'actual HTTP must reject a corrupted stream, including automatic compression paths');
  assert.equal(
    (await db.prepare('SELECT status FROM financial_jobs WHERE id=?').bind(job.id).first()).status,
    'completed',
    'read corruption cannot rewrite immutable successful job history'
  );
});
test('calendar filtering is exact and dependency pages reject unsupported filters or absent events', async () => {
  const no = await (
    await request('/financial/calendars?dateFrom=18990101&dateTo=20250101', { cookie: owner })
  ).json();
  assert.equal(no.error.code, 'INVALID_INPUT');
  const empty = await (
    await request('/financial/calendars?dateFrom=20240101&dateTo=20280101', { cookie: owner })
  ).json();
  assert.equal(empty.total, 0);
  const prep = await db
    .prepare('SELECT id,roots FROM financial_preparations ORDER BY created_at LIMIT 1')
    .first();
  const root = JSON.parse(prep.roots).preparedRoot;
  assert.equal(
    (
      await request(
        `/financial/preparations/${prep.id}/events/${'9'.repeat(64)}/dependencies?preparedRoot=${root}`,
        { cookie: owner }
      )
    ).status,
    404
  );
  assert.equal(
    (
      await request(
        `/financial/preparations/${prep.id}/coverage?preparedRoot=${root}&dateFrom=20240101`,
        { cookie: owner }
      )
    ).status,
    400
  );
});


test('a frozen parent/quarter package validates and an explicit revision preserves its exact scope',async()=>{
 const fixture=await core({command:'source',scope:'parent',flowBasis:'quarter'}),cal=fixture.metadata.calendar.ref;
 for(const [ref,data] of Object.entries(fixture.registry)){
  const raw=Buffer.from(data,'base64'),value=JSON.parse(raw);await bucket.put('actual-core-registry/'+ref,raw);
  await db.prepare("INSERT OR REPLACE INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)").bind(ref,value.kind,'*','actual-core-registry/'+ref,digest(raw),raw.length,JSON.stringify({calendarRoot:value.scope.calendarRoot}),'2026-01-01T00:00:00Z').run();
 }
 const raw=Buffer.from(fixture.raw,'base64'),created=await (await request('/financial/inputs',{method:'POST',cookie:owner,data:{requestId:randomUUID(),name:'Explicit parent/quarter contract',byteLength:raw.length,calendarRef:cal,proofRefs:[]}})).json();
 assert.equal((await request(`/financial/inputs/${created.input.id}/content`,{method:'PUT',cookie:owner,raw})).status,200);
 await queue({id:created.input.id,hash:digest(raw)});let claimed=await claimJob();const validated=await consumeWithCore(claimed.job);
 const initial=(await (await request(`/financial/inputs/${created.input.id}`,{cookie:owner})).json()).input;
 assert.equal(initial.selection.scope,'parent');assert.equal(initial.selection.flowBasis,'quarter');
 const revised=await request(`/financial/inputs/${initial.id}/revisions`,{method:'POST',cookie:owner,data:{requestId:randomUUID(),expectedPackRoot:initial.packRoot,selection:initial.selection,unitPolicy:'verified_only',declarations:[]}}),data=await revised.json();assert.equal(revised.status,202,JSON.stringify(data));
 claimed=await claimJob();const result=await consumeWithCore(claimed.job);
 assert.equal(result.manifest.summary.input.selection.scope,'parent');assert.equal(result.manifest.summary.input.selection.flowBasis,'quarter');assert.equal(result.manifest.roots.inputRoot,validated.manifest.roots.inputRoot);assert.notEqual(result.manifest.roots.packRoot,validated.manifest.roots.packRoot);
 const read=(await (await request(`/financial/inputs/${data.input.id}`,{cookie:owner})).json()).input;assert.equal(read.parentId,initial.id);assert.equal(read.selection.scope,'parent');assert.equal(read.selection.flowBasis,'quarter');
});
