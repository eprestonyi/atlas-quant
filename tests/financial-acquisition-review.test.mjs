/** Independent revocation tests over the actual Worker, D1 and R2.
 * Source reads are explicit synthetic bytes. No provider or numerical fitting.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { canonical } from "../edge/financial-acquisition/planner.mjs";
import { reserveIntent } from "../edge/financial-acquisition/receipts.mjs";
import {
  acquisitionAdmission,
  ownerAllowed,
  budgetState,
} from "../edge/financial-acquisition/admission.mjs";
import definitions from "../edge/financial/definitions.json" with { type: "json" };

const hash = (value) =>
  createHash("sha256")
    .update(Buffer.isBuffer(value) ? value : canonical(value))
    .digest("hex");
const prefix = "/runner/financial-acquire";
// Per-request test-only bindings make revocation happen after a real task has
// acquired its lease. No production test hook or persistent config is changed.
const script = await buildWorkerSource({
  wrapper: `
export default {async fetch(request, env, ctx) {
  const mode = request.headers.get('X-Test-Authorization-Change');
  const scoped = {...env};
  if (mode === 'provider-off') scoped.TUSHARE_PUBLIC_AUTHORIZED = 'false';
  if (mode === 'feature-off') scoped.FINANCIAL_ACQUISITION_ENABLED = 'false';
  if (mode === 'scope-change') scoped.FINANCIAL_ACQUISITION_AUTH_SCOPE = 'different-entitlement-v2';
  if (mode === 'owner-removed') { scoped.FINANCIAL_ACQUISITION_AUDIENCE = 'canary'; scoped.FINANCIAL_ACQUISITION_CANARY_OWNERS = '["99999999-9999-4999-8999-999999999999"]'; }
  return productionWorker.fetch(request, scoped, ctx);
}};`,
});

async function fixture(t, { bindings = {}, setupOnly = false } = {}) {
  const baseBindings = {
    RUNNER_SECRET: "independent-acquisition-review-only",
    FINANCIAL_WORKSPACE_ENABLED: "true",
    FINANCIAL_ACQUISITION_ENABLED: "true",
    FINANCIAL_ACQUISITION_AUDIENCE: "public",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    FINANCIAL_ACQUISITION_AUTH_SCOPE: "independent-fixture-v1",
    ALLOW_ACQUISITION_FIXTURES: "true",
    ...bindings,
  };
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
    r2Buckets: ["ARTIFACTS"],
    bindings: baseBindings,
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database("DB");
  await db.exec(
    (
      await fs.readFile(new URL("../edge/schema.sql", import.meta.url), "utf8")
    ).replaceAll("\n", " "),
  );
  async function req(
    path,
    { data, raw, method = "GET", cookie, lease, change, headers = {} } = {},
  ) {
    headers = { ...headers };
    if (path.startsWith(prefix))
      headers.authorization = "Bearer independent-acquisition-review-only";
    if (cookie) headers.cookie = cookie;
    if (lease) headers["X-Acquisition-Lease"] = lease;
    if (change) headers["X-Test-Authorization-Change"] = change;
    if (data !== undefined || raw !== undefined)
      headers["content-type"] = "application/json";
    return mf.dispatchFetch("https://independent.test/quant/api" + path, {
      method,
      headers,
      body: raw ?? (data === undefined ? undefined : JSON.stringify(data)),
    });
  }
  async function json(path, options = {}, expected = 200) {
    const response = await req(path, options),
      value = await response.json();
    assert.equal(response.status, expected, JSON.stringify(value));
    return value;
  }
  const owner = (await req("/session")).headers.get("set-cookie").split(";")[0];
  const env = { ...baseBindings, DB: db };
  if (setupOnly) return { req, json, db, owner, env };
  const plan = (
    await json(
      "/financial/acquisition-plans",
      {
        method: "POST",
        cookie: owner,
        data: {
          requestId: randomUUID(),
          profile: "annual_statements_2_v1",
          name: "Independent fixture only",
          symbols: ["600690.SH"],
          period: "20231231",
          start: "20240103",
          end: "20240103",
          announcementStart: "20240103",
          selectedStateIds: [definitions.items[0].id],
        },
      },
      201,
    )
  ).plan;
  await json(prefix + "/heartbeat", {
    method: "POST",
    data: {
      capability: "financial-acquire/v1",
      engineVersion: "review",
      state: "ready",
    },
  });
  const created = (
    await json(
      `/financial/acquisition-plans/${plan.id}/start`,
      {
        method: "POST",
        cookie: owner,
        data: { requestId: randomUUID(), expectedPlanRoot: plan.planRoot },
      },
      202,
    )
  ).job;
  const claimId = randomUUID();
  const job = (
    await json(prefix + "/claim", {
      method: "POST",
      data: {
        requestId: claimId,
        capability: "financial-acquire/v1",
        engineVersion: "review",
      },
    })
  ).job;
  assert.equal(job.id, created.id);
  const input = await json(`${prefix}/jobs/${job.id}/input`, {
    lease: job.leaseToken,
  });
  const receipts = [];
  for (const request of input.executionPlan.requests) {
    const attemptId = randomUUID(),
      route = `${prefix}/jobs/${job.id}/requests/${request.requestKey}`;
    const ack = await json(route + "/begin", {
      method: "POST",
      data: { leaseToken: job.leaseToken, attemptId },
    });
    assert.equal(ack.maySend, true);
    const values =
      request.endpoint === "trade_cal"
        ? {
            exchange: "SSE",
            cal_date: "20240103",
            is_open: 1,
            pretrade_date: "20240102",
          }
        : {
            ts_code: "600690.SH",
            end_date: "20231231",
            report_type: "1",
            comp_type: "1",
            ann_date: "20240103",
            f_ann_date: "20240103",
          };
    const raw = Buffer.from(
      JSON.stringify({
        code: 0,
        data: {
          fields: request.fields,
          items: [request.fields.map((f) => values[f] ?? null)],
        },
      }),
    );
    const metadata = {
      attemptId,
      sha256: hash(raw),
      byteLength: raw.length,
      httpStatus: 200,
      retrievedAt: "2026-10-08T00:00:00Z",
      sourceKind: "fixture",
    };
    const headers = {
      "X-Acquisition-Receipt": Buffer.from(JSON.stringify(metadata)).toString(
        "base64url",
      ),
    };
    const saved = await json(route + "/receipt", {
      method: "PUT",
      lease: job.leaseToken,
      raw,
      headers,
    });
    receipts.push({ ...saved, raw, headers, route });
  }
  const payload = {
    sessions: ["20240103"],
    coverage_start: "20240103",
    coverage_end: "20240103",
    complete: true,
    evidence_reference: `SYNTHETIC_FIXTURE:tushare:trade_cal:SSE:20240103:20240103:receipt-sha256:${receipts[0].sha256}`,
    kind: "fixture",
  };
  const calendarRoot = hash({
    sessions_hash: hash(payload.sessions),
    ...Object.fromEntries(
      Object.entries(payload).filter(([k]) => k !== "sessions"),
    ),
  });
  const calendar = Buffer.from(
    canonical({
      kind: "calendar",
      registryVersion: 1,
      payload,
      scope: { calendarRoot },
      evidenceLevel: "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE",
    }),
  );
  // Edge exposes this only as uploaded/unvalidated. Existing core must reject it
  // as a financial package; no prepared or admitted data is asserted by this test.
  const packageBytes = Buffer.from('{"explicitTransportFixture":true}');
  const descriptor = (raw) => ({
    sha256: hash(raw),
    byteLength: raw.length,
    chunks: [{ ordinal: 0, sha256: hash(raw), byteLength: raw.length }],
  });
  const manifest = {
    format: "atlas.quant.financial_acquisition_output",
    version: 1,
    jobId: job.id,
    executionPlanRoot: input.executionPlan.executionPlanRoot,
    package: descriptor(packageBytes),
    calendar: descriptor(calendar),
    calendarRoot,
    inputRoot: "1".repeat(64),
    packRoot: "2".repeat(64),
    sourceReceipts: receipts.map(
      ({ requestKey, receiptId, sha256, byteLength }) => ({
        requestKey,
        receiptId,
        sha256,
        byteLength,
      }),
    ),
  };
  const publication = await json(`${prefix}/jobs/${job.id}/publication`, {
    method: "POST",
    data: { leaseToken: job.leaseToken, manifest },
  });
  for (const [name, raw] of [
    ["package", packageBytes],
    ["calendar", calendar],
  ]) {
    await json(
      `${prefix}/jobs/${job.id}/publication/${name}/0?manifestSha256=${publication.manifestSha256}`,
      { method: "PUT", raw, lease: job.leaseToken },
    );
  }
  return {
    req,
    json,
    db,
    owner,
    job,
    receipts,
    manifest,
    publication,
    packageBytes,
    claimId,
    env,
  };
}

for (const change of [
  "provider-off",
  "feature-off",
  "scope-change",
  "owner-removed",
]) {
  test(`${change} blocks cached bytes and all unpublished output paths for an existing lease`, async (t) => {
    const f = await fixture(t),
      base = `${prefix}/jobs/${f.job.id}`,
      lease = f.job.leaseToken;
    for (const [path, options] of [
      [base + "/input", {}],
      [f.receipts[0].route + "/receipt", {}],
      [
        f.receipts[0].route + "/begin",
        {
          method: "POST",
          data: { leaseToken: lease, attemptId: randomUUID() },
        },
      ],
      [
        base + "/publication",
        { method: "POST", data: { leaseToken: lease, manifest: f.manifest } },
      ],
      [
        base +
          `/publication/package/0?manifestSha256=${f.publication.manifestSha256}`,
        { method: "PUT", raw: f.packageBytes },
      ],
      [
        base + "/complete",
        {
          method: "POST",
          data: {
            leaseToken: lease,
            manifestSha256: f.publication.manifestSha256,
          },
        },
      ],
    ]) {
      const response = await f.req(path, { lease, change, ...options });
      assert.ok(
        [403, 409, 503].includes(response.status),
        `${path} bypassed ${change}: ${response.status}`,
      );
    }
    assert.equal(
      (await f.db.prepare("SELECT COUNT(*) n FROM financial_inputs").first()).n,
      0,
    );
    assert.equal(
      (
        await f.db
          .prepare("SELECT COUNT(*) n FROM financial_registry_entries")
          .first()
      ).n,
      0,
    );
    // Preserving an already completed provider response does not spend another
    // request and remains allowed after authorization changes.
    const receipt = f.receipts[0];
    const replay = await f.json(receipt.route + "/receipt", {
      method: "PUT",
      lease,
      change,
      raw: receipt.raw,
      headers: receipt.headers,
    });
    assert.equal(replay.receiptId, receipt.receiptId);
    const status = await f.json(base + "/status", { lease, change });
    assert.equal(status.job.status, "running");
    const stopped = await f.json(base + "/fail", {
      method: "POST",
      change,
      data: {
        leaseToken: lease,
        error: {
          code: "REVIEW_STOP",
          message: "Explicit isolated fixture stop",
        },
      },
    });
    assert.equal(stopped.job.status, "failed");
  });
}

test("completed ACK after revocation returns the same IDs and does not create another input", async (t) => {
  const f = await fixture(t),
    route = `${prefix}/jobs/${f.job.id}/complete`;
  const data = {
    leaseToken: f.job.leaseToken,
    manifestSha256: f.publication.manifestSha256,
  };
  const first = await f.json(route, { method: "POST", data });
  for (const change of [
    "provider-off",
    "feature-off",
    "scope-change",
    "owner-removed",
  ]) {
    const next = await f.json(route, { method: "POST", change, data });
    assert.deepEqual(next.result, first.result);
    assert.equal(next.idempotent, true);
  }
  assert.equal(
    (await f.db.prepare("SELECT COUNT(*) n FROM financial_inputs").first()).n,
    1,
  );
  assert.equal(
    (
      await f.db
        .prepare("SELECT COUNT(*) n FROM financial_registry_entries")
        .first()
    ).n,
    1,
  );
});

test("canary admission and invalid budget configuration fail closed", () => {
  const allowed = randomUUID(),
    other = randomUUID();
  assert.equal(ownerAllowed({}, allowed), false);
  const env = {
    FINANCIAL_ACQUISITION_CANARY_OWNERS: JSON.stringify([allowed]),
  };
  assert.equal(ownerAllowed(env, allowed), true);
  assert.equal(ownerAllowed(env, other), false);
  assert.equal(
    ownerAllowed(
      { ...env, FINANCIAL_ACQUISITION_CANARY_OWNERS: "[]" },
      allowed,
    ),
    false,
  );
  for (const invalid of ["0", "33", "1.5", "-1", "Infinity", " 1", 1]) {
    assert.equal(
      acquisitionAdmission({
        ...env,
        FINANCIAL_ACQUISITION_MAX_ACTIVE: invalid,
      }),
      null,
    );
  }
  for (const invalid of ["0", "1001", "-1", "NaN", "60.0", 60]) {
    assert.equal(
      acquisitionAdmission({
        ...env,
        FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS: invalid,
      }),
      null,
    );
  }
  for (const value of [
    "[",
    "null",
    JSON.stringify([allowed, allowed]),
    '["not-a-uuid"]',
  ]) {
    assert.equal(
      ownerAllowed(
        { ...env, FINANCIAL_ACQUISITION_CANARY_OWNERS: value },
        allowed,
      ),
      false,
    );
  }
});

test("the final global queue slot is atomic across owners and cancellation still occupies it", async (t) => {
  const f = await fixture(t, {
    setupOnly: true,
    bindings: { FINANCIAL_ACQUISITION_MAX_ACTIVE: "1" },
  });
  const owners = [
    f.owner,
    (await f.req("/session")).headers.get("set-cookie").split(";")[0],
  ];
  const plans = await Promise.all(
    owners.map(
      async (cookie) =>
        (
          await f.json(
            "/financial/acquisition-plans",
            {
              method: "POST",
              cookie,
              data: {
                requestId: randomUUID(),
                profile: "annual_statements_2_v1",
                name: "Concurrent synthetic admission",
                symbols: ["600690.SH"],
                period: "20231231",
                start: "20240103",
                end: "20240103",
                announcementStart: "20240103",
                selectedStateIds: [definitions.items[0].id],
              },
            },
            201,
          )
        ).plan,
    ),
  );
  await f.json(prefix + "/heartbeat", {
    method: "POST",
    data: {
      capability: "financial-acquire/v1",
      engineVersion: "review",
      state: "ready",
    },
  });
  const requests = plans.map((plan) => ({
    requestId: randomUUID(),
    expectedPlanRoot: plan.planRoot,
  }));
  const send = (i) =>
    f.req(`/financial/acquisition-plans/${plans[i].id}/start`, {
      method: "POST",
      cookie: owners[i],
      data: requests[i],
    });
  const responses = await Promise.all([send(0), send(1)]);
  assert.deepEqual(responses.map((x) => x.status).sort(), [202, 409]);
  const winner = responses.findIndex((x) => x.status === 202),
    loser = 1 - winner;
  const job = (await responses[winner].json()).job;
  assert.equal(
    (
      await f.db
        .prepare("SELECT COUNT(*) n FROM financial_acquisition_jobs")
        .first()
    ).n,
    1,
  );
  const retry = await send(winner);
  assert.equal(retry.status, 202);
  assert.equal((await retry.json()).job.id, job.id);
  await f.db
    .prepare(
      "UPDATE financial_acquisition_jobs SET status='cancel_requested' WHERE id=?",
    )
    .bind(job.id)
    .run();
  assert.equal((await send(loser)).status, 409);
  await f.db
    .prepare(
      "UPDATE financial_acquisition_jobs SET status='cancelled' WHERE id=?",
    )
    .bind(job.id)
    .run();
  await f.db
    .prepare(
      "INSERT INTO meta(key,value,updated_at) VALUES('financial_acquisition_maintenance','paused',?)",
    )
    .bind(new Date().toISOString())
    .run();
  assert.equal((await send(loser)).status, 409);
  await f.db
    .prepare("DELETE FROM meta WHERE key='financial_acquisition_maintenance'")
    .run();
  assert.equal((await send(loser)).status, 202);
  assert.equal(
    (
      await f.db
        .prepare(
          "SELECT COUNT(*) n FROM financial_acquisition_jobs WHERE status='queued'",
        )
        .first()
    ).n,
    1,
  );
});

test("daily intent budget is atomic across scopes, sticky unknowns count, and UTC rollover is exact", async (t) => {
  const f = await fixture(t),
    original = await f.db
      .prepare("SELECT * FROM financial_acquisition_jobs WHERE id=?")
      .bind(f.job.id)
      .first();
  const before = "2027-02-03T23:59:59.999Z",
    after = "2027-02-04T00:00:00.000Z";
  async function candidate(scope) {
    const request = {
      requestKey: hash(Buffer.from(randomUUID())),
      authorizationScope: scope,
      endpoint: "trade_cal",
      params: { exchange: "SSE", start_date: "20240103", end_date: "20240103" },
      fields: ["cal_date"],
    };
    const job = {
      ...original,
      id: randomUUID(),
      owner: randomUUID(),
      lease_token: randomUUID(),
      spec: JSON.stringify({ requests: [request] }),
    };
    await f.db
      .prepare(
        "INSERT INTO financial_acquisition_jobs (id,owner,plan_id,request_id,request_hash,spec,status,lease_token,lease_until,deadline,created_at,updated_at) VALUES (?,?,?,?,?,?,'running',?,?,?,?,?)",
      )
      .bind(
        job.id,
        job.owner,
        original.plan_id,
        randomUUID(),
        "a".repeat(64),
        job.spec,
        job.lease_token,
        "2027-02-04T00:02:00.000Z",
        "2027-02-04T00:03:00.000Z",
        before,
        before,
      )
      .run();
    return {
      job,
      request,
      attempt: randomUUID(),
      env: {
        ...f.env,
        FINANCIAL_ACQUISITION_AUTH_SCOPE: scope,
        FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS: "1",
      },
    };
  }
  const a = await candidate("scope-a"),
    b = await candidate("scope-b");
  const reserve = (x, time) =>
    reserveIntent(x.env, x.job, x.request, x.attempt, time);
  const results = await Promise.all([reserve(a, before), reserve(b, before)]);
  assert.deepEqual(results.map((x) => x.meta.changes).sort(), [0, 1]);
  const winner = results[0].meta.changes ? a : b;
  await f.db
    .prepare(
      "UPDATE financial_acquisition_requests SET state='outcome_unknown' WHERE request_key=?",
    )
    .bind(winner.request.requestKey)
    .run();
  const blocked = await candidate("scope-c");
  assert.equal((await reserve(blocked, before)).meta.changes, 0);
  assert.equal((await reserve(winner, before)).meta.changes, 0);
  assert.equal((await budgetState(a.env, before)).newProviderRequestsUsed, 1);
  assert.equal((await budgetState(a.env, after)).newProviderRequestsUsed, 0);
  assert.equal((await reserve(blocked, after)).meta.changes, 1);
  assert.equal((await budgetState(a.env, after)).newProviderRequestsUsed, 1);
  // A new UTC budget does not authorize an expired or cancelled lease.
  const expired = await candidate("scope-d"),
    cancelled = await candidate("scope-e");
  await f.db
    .prepare("UPDATE financial_acquisition_jobs SET lease_until=? WHERE id=?")
    .bind(before, expired.job.id)
    .run();
  await f.db
    .prepare(
      "UPDATE financial_acquisition_jobs SET status='cancel_requested' WHERE id=?",
    )
    .bind(cancelled.job.id)
    .run();
  for (const x of [expired, cancelled]) {
    x.env.FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS = "100";
    assert.equal((await reserve(x, after)).meta.changes, 0);
  }
});

test("received cache ACK and receipt retransmission spend no additional daily intent", async (t) => {
  const f = await fixture(t, {
    bindings: { FINANCIAL_ACQUISITION_MAX_DAILY_REQUESTS: "4" },
  });
  assert.equal(
    (
      await f.db
        .prepare("SELECT COUNT(*) n FROM financial_acquisition_requests")
        .first()
    ).n,
    4,
  );
  for (const receipt of f.receipts) {
    const response = await f.json(receipt.route + "/begin", {
      method: "POST",
      data: { leaseToken: f.job.leaseToken, attemptId: randomUUID() },
    });
    assert.equal(response.maySend, false);
    assert.equal(response.receiptId, receipt.receiptId);
    const repeated = await f.json(receipt.route + "/receipt", {
      method: "PUT",
      lease: f.job.leaseToken,
      raw: receipt.raw,
      headers: receipt.headers,
    });
    assert.equal(repeated.receiptId, receipt.receiptId);
  }
  assert.equal(
    (
      await f.db
        .prepare("SELECT COUNT(*) n FROM financial_acquisition_requests")
        .first()
    ).n,
    4,
  );
});
