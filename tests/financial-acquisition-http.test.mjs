/** Actual D1/R2/Worker transport tests, without a provider or numerical claims. */
import test, { after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { canonical } from "../edge/financial-acquisition/planner.mjs";
import definitions from "../edge/financial/definitions.json" with { type: "json" };
const hash = (v) =>
  createHash("sha256")
    .update(typeof v === "string" || Buffer.isBuffer(v) ? v : canonical(v))
    .digest("hex");
const mf = new Miniflare({
  modules: true,
  script: await buildWorkerSource({ buildId: "acquisition-http-test" }),
  compatibilityDate: "2026-08-01",
  d1Databases: ["DB"],
  r2Buckets: ["ARTIFACTS"],
  bindings: {
    RUNNER_SECRET: "test-acquire-only",
    FINANCIAL_WORKSPACE_ENABLED: "true",
    FINANCIAL_ACQUISITION_ENABLED: "true",
    FINANCIAL_ACQUISITION_AUDIENCE: "public",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    FINANCIAL_ACQUISITION_AUTH_SCOPE: "test-only-synthetic-v1",
    ALLOW_ACQUISITION_FIXTURES: "true",
  },
});
after(() => mf.dispose());
const db = await mf.getD1Database("DB"),
  bucket = await mf.getR2Bucket("ARTIFACTS");
await db.exec(
  (
    await fs.readFile(new URL("../edge/schema.sql", import.meta.url), "utf8")
  ).replaceAll("\n", " "),
);
const base = "https://acquire.test/quant/api",
  runner = "/runner/financial-acquire";
async function req(
  path,
  {
    method = "GET",
    data,
    raw,
    cookie,
    service = false,
    lease,
    headers = {},
  } = {},
) {
  if (cookie) headers.cookie = cookie;
  if (service) headers.authorization = "Bearer test-acquire-only";
  if (lease) headers["X-Acquisition-Lease"] = lease;
  if (data !== undefined || raw !== undefined)
    headers["content-type"] = "application/json";
  return mf.dispatchFetch(base + path, {
    method,
    headers,
    body: raw ?? (data === undefined ? undefined : JSON.stringify(data)),
  });
}
async function json(path, args = {}, expected = 200) {
  const r = await req(path, args),
    v = await r.json();
  assert.equal(r.status, expected, JSON.stringify(v));
  return v;
}
async function session() {
  return (await req("/session")).headers.get("set-cookie").split(";")[0];
}
const owner = await session(),
  other = await session();
const beat = {
  capability: "financial-acquire/v1",
  engineVersion: "test",
  state: "ready",
};
const payload = (day = "01") => ({
  requestId: randomUUID(),
  profile: "annual_statements_2_v1",
  name: "Explicit synthetic protocol test",
  symbols: ["600690.SH"],
  period: "20231231",
  announcementStart: "202401" + day,
  start: "202401" + day,
  end: "202401" + day,
  selectedStateIds: [definitions.items[0].id],
});
async function planned(day = "01", cookie = owner) {
  return (
    await json(
      "/financial/acquisition-plans",
      { method: "POST", cookie, data: payload(day) },
      201,
    )
  ).plan;
}
async function started(plan, cookie = owner) {
  await json(runner + "/heartbeat", {
    method: "POST",
    service: true,
    data: beat,
  });
  return (
    await json(
      `/financial/acquisition-plans/${plan.id}/start`,
      {
        method: "POST",
        cookie,
        data: { requestId: randomUUID(), expectedPlanRoot: plan.planRoot },
      },
      202,
    )
  ).job;
}
async function claimed(job) {
  const value = await json(runner + "/claim", {
    method: "POST",
    service: true,
    data: {
      requestId: randomUUID(),
      capability: beat.capability,
      engineVersion: "test",
    },
  });
  assert.equal(value.job?.id, job.id);
  return value.job;
}
async function failed(job) {
  return json(`${runner}/jobs/${job.id}/fail`, {
    method: "POST",
    service: true,
    data: {
      leaseToken: job.leaseToken,
      error: {
        code: "TEST_STOP",
        message: "Stopped after explicit protocol test",
      },
    },
  });
}
async function receive(job, request, day) {
  const key = request.requestKey,
    attemptId = randomUUID();
  if (request.cache.status === "frozen")
    return { ...request.cache, requestKey: key };
  const first = await json(`${runner}/jobs/${job.id}/requests/${key}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  assert.equal(first.maySend, true);
  const second = await json(`${runner}/jobs/${job.id}/requests/${key}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  assert.equal(second.maySend, false);
  const values =
    request.endpoint === "trade_cal"
      ? {
          exchange: "SSE",
          cal_date: "202401" + day,
          is_open: 1,
          pretrade_date: "20231229",
        }
      : {
          ts_code: "600690.SH",
          end_date: "20231231",
          report_type: "1",
          comp_type: "1",
          ann_date: "202401" + day,
          f_ann_date: "202401" + day,
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
  const meta = {
    attemptId,
    sha256: hash(raw),
    byteLength: raw.length,
    httpStatus: 200,
    retrievedAt: "2026-10-08T00:00:00Z",
    sourceKind: "fixture",
  };
  const headers = {
    "X-Acquisition-Receipt": Buffer.from(JSON.stringify(meta)).toString(
      "base64url",
    ),
  };
  const saved = await json(`${runner}/jobs/${job.id}/requests/${key}/receipt`, {
    method: "PUT",
    service: true,
    lease: job.leaseToken,
    raw,
    headers,
  });
  const retry = await json(`${runner}/jobs/${job.id}/requests/${key}/receipt`, {
    method: "PUT",
    service: true,
    lease: job.leaseToken,
    raw,
    headers,
  });
  assert.equal(retry.receiptId, saved.receiptId);
  assert.equal(retry.idempotent, true);
  return { ...saved, raw };
}

test("idle heartbeat is advisory, default owner isolation, durable empty claims and maintenance", async () => {
  const before = await db
    .prepare("SELECT COUNT(*) n FROM financial_acquisition_claims")
    .first();
  assert.equal(
    (
      await json(runner + "/heartbeat", {
        method: "POST",
        service: true,
        data: beat,
      })
    ).canClaim,
    false,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT COUNT(*) n FROM financial_acquisition_claims")
        .first()
    ).n,
    before.n,
  );
  const requestId = randomUUID();
  const empty = await json(runner + "/claim", {
    method: "POST",
    service: true,
    data: { requestId, capability: beat.capability, engineVersion: "test" },
  });
  assert.equal(empty.claim.status, "empty");
  const plan = await planned("01");
  await json("/financial/acquisition-plans/" + plan.id, { cookie: other }, 404);
  const job = await started(plan);
  assert.equal(
    (
      await json(runner + "/heartbeat", {
        method: "POST",
        service: true,
        data: beat,
      })
    ).canClaim,
    true,
  );
  await db
    .prepare(
      "INSERT INTO meta(key,value,updated_at) VALUES('financial_acquisition_maintenance','paused',?)",
    )
    .bind(new Date().toISOString())
    .run();
  assert.equal(
    (
      await json(runner + "/heartbeat", {
        method: "POST",
        service: true,
        data: beat,
      })
    ).canClaim,
    false,
  );
  const replay = await json(runner + "/claim", {
    method: "POST",
    service: true,
    data: { requestId, capability: beat.capability, engineVersion: "test" },
  });
  assert.equal(replay.job, null);
  await db
    .prepare("DELETE FROM meta WHERE key='financial_acquisition_maintenance'")
    .run();
  const lease = await claimed(job);
  await json("/financial/acquisition-jobs/" + job.id, { cookie: other }, 404);
  await failed(lease);
  assert.equal(
    (await db.prepare("SELECT COUNT(*) n FROM financial_jobs").first()).n,
    0,
  );
});

test("unknown provider ACK is sticky across a new plan and another workspace", async () => {
  const plan = await planned("02"),
    job = await claimed(await started(plan)),
    request = plan.requests[0],
    attemptId = randomUUID();
  await json(`${runner}/jobs/${job.id}/requests/${request.requestKey}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  await json(
    `${runner}/jobs/${job.id}/requests/${request.requestKey}/unknown`,
    {
      method: "POST",
      service: true,
      data: {
        leaseToken: job.leaseToken,
        attemptId,
        reason: "No durable response after network intent",
      },
    },
  );
  await failed(job);
  const again = await planned("02", other);
  assert.deepEqual(again.blockedReasons, ["REQUEST_REQUIRES_REVIEW"]);
  assert.equal(again.requests[0].cache.status, "outcome_unknown");
  await json(
    `/financial/acquisition-plans/${again.id}/start`,
    {
      method: "POST",
      cookie: other,
      data: { requestId: randomUUID(), expectedPlanRoot: again.planRoot },
    },
    409,
  );
});

test("frozen receipts, bounded output and exact owner calendar grant produce uploaded input only", async () => {
  const plan = await planned("03"),
    job = await claimed(await started(plan));
  const input = await json(`${runner}/jobs/${job.id}/input`, {
    service: true,
    lease: job.leaseToken,
  });
  assert.equal(
    hash(
      Object.fromEntries(
        Object.entries(input.reviewedPlan).filter(
          ([k]) => !["id", "createdAt", "ownerScope", "planRoot"].includes(k),
        ),
      ),
    ),
    input.reviewedPlan.planRoot,
  );
  const receipts = [];
  for (const request of input.executionPlan.requests)
    receipts.push(await receive(job, request, "03"));
  await json(
    `${runner}/jobs/${job.id}/requests/${"a".repeat(64)}/receipt`,
    { service: true, lease: job.leaseToken },
    404,
  );
  const reference = `SYNTHETIC_FIXTURE:tushare:trade_cal:SSE:20240103:20240103:receipt-sha256:${receipts[0].sha256}`;
  const p = {
    sessions: ["20240103"],
    coverage_start: "20240103",
    coverage_end: "20240103",
    complete: true,
    evidence_reference: reference,
    kind: "fixture",
  };
  const evidence = {
      sessions_hash: hash(p.sessions),
      coverage_start: p.coverage_start,
      coverage_end: p.coverage_end,
      complete: true,
      kind: "fixture",
      evidence_reference: reference,
    },
    calendarRoot = hash(evidence);
  const calendar = Buffer.from(
    canonical({
      kind: "calendar",
      registryVersion: 1,
      payload: p,
      scope: { calendarRoot },
      evidenceLevel: "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE",
    }),
  );
  // This deliberately is only a transport fixture; the existing input validator
  // must still validate it. No ready/prepared state is manufactured here.
  const rawPackage = Buffer.from('{"explicitProtocolFixture":true}');
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
    package: descriptor(rawPackage),
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
  const begun = await json(`${runner}/jobs/${job.id}/publication`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, manifest },
  });
  for (const [name, raw] of [
    ["package", rawPackage],
    ["calendar", calendar],
  ])
    await json(
      `${runner}/jobs/${job.id}/publication/${name}/0?manifestSha256=${begun.manifestSha256}`,
      { method: "PUT", service: true, lease: job.leaseToken, raw },
    );
  const done = await json(`${runner}/jobs/${job.id}/complete`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, manifestSha256: begun.manifestSha256 },
  });
  assert.equal(done.job.status, "completed");
  assert.equal(done.result.researchBinding, false);
  const read = await json("/financial/inputs/" + done.result.inputId, {
    cookie: owner,
  });
  assert.equal(read.input.status, "uploaded");
  assert.equal(read.input.unitVerified, false);
  assert.equal(read.input.acquisition.sourceReceipts.length, 4);
  await json(
    "/financial/inputs/" + done.result.inputId,
    { cookie: other },
    404,
  );
  const grant = await db
    .prepare("SELECT owner FROM financial_registry_entries WHERE id=?")
    .bind(done.result.calendarRef)
    .first();
  assert.notEqual(grant.owner, "*");
  const readRaw = await req(
    `/financial/inputs/${done.result.inputId}/source-download?uploadSha256=${hash(rawPackage)}`,
    { cookie: owner },
  );
  assert.equal(
    hash(Buffer.from(await readRaw.arrayBuffer())),
    hash(rawPackage),
  );
  const again = await planned("03", other);
  assert.equal(again.budget.cachedRequests, 4);
  assert.equal(again.budget.newRequests, 0);
  const retry = await json(`${runner}/jobs/${job.id}/complete`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, manifestSha256: begun.manifestSha256 },
  });
  assert.equal(retry.result.inputId, done.result.inputId);
  assert.equal(retry.idempotent, true);
});

test("lease expiry preserves unknown intents, never revives by a later heartbeat", async () => {
  const plan = await planned("04"),
    job = await claimed(await started(plan)),
    key = plan.requests[0].requestKey,
    attemptId = randomUUID();
  await json(`${runner}/jobs/${job.id}/requests/${key}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  await db
    .prepare(
      "UPDATE financial_acquisition_jobs SET lease_until='2000-01-01T00:00:00.000Z' WHERE id=?",
    )
    .bind(job.id)
    .run();
  await json(
    runner + "/heartbeat",
    {
      method: "POST",
      service: true,
      data: {
        ...beat,
        jobId: job.id,
        leaseToken: job.leaseToken,
        phase: "fetching_sources",
      },
    },
    409,
  );
  const read = await json("/financial/acquisition-jobs/" + job.id, {
    cookie: owner,
  });
  assert.equal(read.job.status, "failed");
  assert.equal(read.receipts[0].status, "outcome_unknown");
  const retry = await planned("04");
  assert.equal(retry.requests[0].cache.status, "outcome_unknown");
});

test("cancellation preserves an already-read raw receipt but disallows any next provider intent", async () => {
  const plan = await planned("05"),
    job = await claimed(await started(plan)),
    key = plan.requests[0].requestKey,
    attemptId = randomUUID();
  await json(`${runner}/jobs/${job.id}/requests/${key}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  await json("/financial/acquisition-jobs/" + job.id + "/cancel", {
    method: "POST",
    cookie: owner,
    data: {},
  });
  const raw = Buffer.from('{"code":0,"data":{"fields":[],"items":[]}}'),
    meta = {
      attemptId,
      sha256: hash(raw),
      byteLength: raw.length,
      httpStatus: 200,
      retrievedAt: "2026-10-08T00:00:00Z",
      sourceKind: "fixture",
    };
  const headers = {
    "X-Acquisition-Receipt": Buffer.from(JSON.stringify(meta)).toString(
      "base64url",
    ),
  };
  await json(`${runner}/jobs/${job.id}/requests/${key}/receipt`, {
    method: "PUT",
    service: true,
    lease: job.leaseToken,
    raw,
    headers,
  });
  await json(
    `${runner}/jobs/${job.id}/requests/${plan.requests[1].requestKey}/begin`,
    {
      method: "POST",
      service: true,
      data: { leaseToken: job.leaseToken, attemptId: randomUUID() },
    },
    409,
  );
  const read = await json("/financial/acquisition-jobs/" + job.id, {
    cookie: owner,
  });
  assert.equal(read.receipts[0].status, "received");
  const stopped = await failed(job);
  assert.equal(stopped.job.status, "cancelled");
});

test("cache content cannot be read by arbitrary key or silently used after R2 corruption", async () => {
  const plan = await planned("03", other),
    job = await claimed(await started(plan, other)),
    key = plan.requests[0].requestKey;
  const row = await db
    .prepare("SELECT * FROM financial_acquisition_cache WHERE request_key=?")
    .bind(key)
    .first();
  const original = await (await bucket.get(row.object_key)).arrayBuffer();
  await bucket.put(row.object_key, "corrupt");
  await json(
    `${runner}/jobs/${job.id}/requests/${key}/receipt`,
    { service: true, lease: job.leaseToken },
    409,
  );
  await bucket.put(row.object_key, original);
  const response = await req(
    `${runner}/jobs/${job.id}/requests/${key}/receipt`,
    { service: true, lease: job.leaseToken },
  );
  const descriptor = JSON.parse(
    Buffer.from(
      response.headers.get("X-Acquisition-Receipt"),
      "base64url",
    ).toString(),
  );
  assert.equal(descriptor.receiptId, row.id);
  assert.equal(descriptor.sourceKind, "fixture");
  assert.equal(hash(Buffer.from(await response.arrayBuffer())), row.sha256);
  await failed(job);
});

test("actual HTTP false provider authorization blocks planning and leaves queues empty", async () => {
  const isolated = new Miniflare({
    modules: true,
    script: await buildWorkerSource({ buildId: "acquisition-disabled-test" }),
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
    r2Buckets: ["ARTIFACTS"],
    bindings: {
      RUNNER_SECRET: "isolated-acquire-test",
      FINANCIAL_ACQUISITION_ENABLED: "true",
      FINANCIAL_ACQUISITION_AUDIENCE: "public",
      TUSHARE_PUBLIC_AUTHORIZED: "false",
      FINANCIAL_ACQUISITION_AUTH_SCOPE: "isolated-scope",
    },
  });
  try {
    const d = await isolated.getD1Database("DB");
    await d.exec(
      (await fs.readFile("edge/schema.sql", "utf8")).replaceAll("\n", " "),
    );
    const sessionResponse = await isolated.dispatchFetch(base + "/session"),
      cookie = sessionResponse.headers.get("set-cookie").split(";")[0];
    const cap = await isolated.dispatchFetch(
      base + "/financial/acquisition-capabilities",
      { headers: { cookie } },
    );
    assert.equal((await cap.json()).enabled, false);
    const plan = await isolated.dispatchFetch(
      base + "/financial/acquisition-plans",
      {
        method: "POST",
        headers: { cookie, "content-type": "application/json" },
        body: JSON.stringify(payload("06")),
      },
    );
    assert.equal(plan.status, 503);
    assert.equal(
      (
        await d
          .prepare("SELECT COUNT(*) n FROM financial_acquisition_plans")
          .first()
      ).n,
      0,
    );
    assert.equal(
      (
        await d
          .prepare("SELECT COUNT(*) n FROM financial_acquisition_requests")
          .first()
      ).n,
      0,
    );
    const migration = (
      await fs.readFile(
        "edge/migrations/0007_financial_acquisition.sql",
        "utf8",
      )
    ).replaceAll("\n", " ");
    await d.exec(migration);
    await d.exec(migration);
  } finally {
    await isolated.dispose();
  }
});

test("complete empty HTTP error is a frozen known response, never an unknown outcome or successful cache", async () => {
  const plan = await planned("07"),
    job = await claimed(await started(plan)),
    key = plan.requests[0].requestKey,
    attemptId = randomUUID();
  await json(`${runner}/jobs/${job.id}/requests/${key}/begin`, {
    method: "POST",
    service: true,
    data: { leaseToken: job.leaseToken, attemptId },
  });
  const raw = Buffer.alloc(0),
    meta = {
      attemptId,
      sha256: hash(raw),
      byteLength: 0,
      httpStatus: 500,
      retrievedAt: "2026-10-08T00:00:00Z",
      sourceKind: "fixture",
    };
  const saved = await json(`${runner}/jobs/${job.id}/requests/${key}/receipt`, {
    method: "PUT",
    service: true,
    lease: job.leaseToken,
    raw,
    headers: {
      "X-Acquisition-Receipt": Buffer.from(JSON.stringify(meta)).toString(
        "base64url",
      ),
    },
  });
  assert.equal(saved.byteLength, 0);
  assert.equal(saved.httpStatus, 500);
  const read = await req(`${runner}/jobs/${job.id}/requests/${key}/receipt`, {
    service: true,
    lease: job.leaseToken,
  });
  assert.equal(read.headers.get("X-Acquisition-Http-Status"), "500");
  assert.equal((await read.arrayBuffer()).byteLength, 0);
  const detail = await json("/financial/acquisition-jobs/" + job.id, {
    cookie: owner,
  });
  assert.equal(detail.receipts[0].status, "received");
  assert.equal(detail.receipts[0].byteLength, 0);
  await failed(job);
  const again = await planned("07");
  assert.equal(again.requests[0].cache.status, "failed");
  assert(again.blockedReasons.includes("PROVIDER_RESPONSE_FAILED"));
  await json(
    `/financial/acquisition-plans/${again.id}/start`,
    {
      method: "POST",
      cookie: owner,
      data: { requestId: randomUUID(), expectedPlanRoot: again.planRoot },
    },
    409,
  );
});
