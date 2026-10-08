/** Real isolated D1/R2 tests: heartbeat hints never create claim receipts. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';

const script = await buildWorkerSource({ buildId: 'financial-heartbeat-tests' });
const schema = (
  await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8')
).replaceAll('\n', ' ');
const now = '2026-01-01T00:00:00.000Z';

async function fixture(t, enabled = true) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: {
      RUNNER_SECRET: 'isolated-financial-heartbeat-test',
      FINANCIAL_WORKSPACE_ENABLED: String(enabled),
    },
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB');
  await db.exec(schema);
  async function post(action, extra = {}) {
    const response = await mf.dispatchFetch(
      `https://financial.test/quant/api/runner/financial/${action}`,
      {
        method: 'POST',
        headers: {
          authorization: 'Bearer isolated-financial-heartbeat-test',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          capability: 'financial-input/v1',
          engineVersion: 'heartbeat-test',
          ...(action === 'heartbeat' ? { state: 'ready' } : {}),
          ...extra,
        }),
      }
    );
    const body = await response.json();
    assert.equal(response.status, 200, JSON.stringify(body));
    return body;
  }
  async function seed(status, kind = 'financial_validate') {
    const inputId = randomUUID(),
      jobId = randomUUID(),
      owner = randomUUID();
    await db
      .prepare(
        `INSERT INTO financial_inputs
      (id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at)
      VALUES(?,?,?,'uploaded',?,'[]',2,?,?,?,?)`
      )
      .bind(
        inputId,
        owner,
        'Explicit queue test',
        randomUUID(),
        randomUUID(),
        'a'.repeat(64),
        now,
        now
      )
      .run();
    await db
      .prepare(
        `INSERT INTO financial_jobs
      (id,owner,input_id,kind,status,spec,request_id,request_hash,lease_token,lease_until,deadline,created_at,updated_at)
      VALUES(?,?,?,?,?,'{}',?,?,?,?,?,?,?)`
      )
      .bind(
        jobId,
        owner,
        inputId,
        kind,
        status,
        randomUUID(),
        'b'.repeat(64),
        randomUUID(),
        now,
        now,
        now,
        now
      )
      .run();
    return jobId;
  }
  async function snapshot() {
    const tables = [
      'financial_jobs',
      'financial_claims',
      'financial_inputs',
      'jobs',
      'runner_claims',
    ];
    const records = {};
    for (const table of tables)
      records[table] = (await db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all()).results;
    return records;
  }
  return { db, post, seed, snapshot };
}

test('idle heartbeat returns false without creating empty receipts or changing expired work', async (t) => {
  const f = await fixture(t);
  await f.seed('running'); // Deliberately expired: the advisory must not call expireJobs.
  await f.seed('cancel_requested');
  await f.seed('completed');
  const before = await f.snapshot();
  for (let i = 0; i < 8; i++)
    assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: false });
  assert.deepEqual(await f.snapshot(), before);
  const runner = await f.db.prepare("SELECT value FROM meta WHERE key='financial_runner'").first();
  assert.equal(JSON.parse(runner.value).capability, 'financial-input/v1');
});

test('queued financial work is advisory true; atomic claim still decides and retries stay durable', async (t) => {
  const f = await fixture(t);
  const jobId = await f.seed('queued', 'financial_prepare');
  const before = await f.snapshot();
  for (let i = 0; i < 3; i++)
    assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: true });
  assert.deepEqual(await f.snapshot(), before);
  const requestId = randomUUID();
  const claim = await f.post('claim', { requestId });
  assert.equal(claim.job.id, jobId);
  assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: false });
  assert.deepEqual(await f.post('claim', { requestId }), claim);
  assert.equal((await f.db.prepare('SELECT COUNT(*) AS n FROM financial_claims').first()).n, 1);
  const leased = await f.post('heartbeat', {
    jobId,
    leaseToken: claim.job.leaseToken,
    phase: 'preparing_states',
  });
  assert.equal(leased.leaseValid, true);
  assert.equal(Object.hasOwn(leased, 'canClaim'), false);
});

test('financial maintenance pauses both advisory and actual new claims without touching queued work', async (t) => {
  const f = await fixture(t);
  await f.seed('queued', 'financial_revise');
  await f.db
    .prepare("INSERT INTO meta(key,value,updated_at) VALUES('financial_maintenance','paused',?)")
    .bind(now)
    .run();
  const before = await f.snapshot();
  assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: false });
  assert.deepEqual(await f.snapshot(), before);
  const requestId = randomUUID();
  assert.equal((await f.post('claim', { requestId })).claim.status, 'empty');
  await f.db.prepare("UPDATE meta SET value='resumed' WHERE key='financial_maintenance'").run();
  assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: true });
  // Maintenance release cannot turn an earlier durable empty receipt into work.
  assert.equal((await f.post('claim', { requestId })).claim.status, 'empty');
});

test('disabled financial capability advertises false even with queued work and remains backward compatible', async (t) => {
  const f = await fixture(t, false);
  await f.seed('queued');
  const before = await f.snapshot();
  assert.deepEqual(await f.post('heartbeat'), { ok: true, canClaim: false });
  assert.deepEqual(await f.snapshot(), before);
});
