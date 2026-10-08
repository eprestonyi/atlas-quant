import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { canonical, digest } from "../edge/market-preparation/common.mjs";
import { createMarketPlan } from "../edge/market-preparation/planner.mjs";
import {
  begin as beginRequest,
  getReceipt,
} from "../edge/market-preparation/receipts.mjs";
import {
  start as startJob,
  leased,
} from "../edge/market-preparation/queue.mjs";
const generated = spawnSync(
  process.env.PYTHON || ".venv/bin/python",
  ["tests/helpers/market-fixture.py"],
  {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "engine" },
    maxBuffer: 2 * 1024 * 1024,
  },
);
assert.equal(generated.status, 0, generated.stderr);
const fixture = JSON.parse(generated.stdout);
const source = await buildWorkerSource({ buildId: "market-preparation-test" }),
  schema = await fs.readFile(
    new URL("../edge/schema.sql", import.meta.url),
    "utf8",
  );
async function setup() {
  const bindings = {
    RUNNER_SECRET: "market-test-only",
    MARKET_ACQUISITION_ENABLED: "true",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    MARKET_ACQUISITION_AUTH_SCOPE: "SYNTHETIC_MARKET_TEST",
    ALLOW_MARKET_FIXTURES: "true",
  };
  const mf = new Miniflare({
    modules: true,
    script: source,
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
    r2Buckets: ["ARTIFACTS"],
    bindings,
  });
  try {
    const db = await mf.getD1Database("DB"),
      bucket = await mf.getR2Bucket("ARTIFACTS");
    await db.exec(schema.replaceAll("\n", " "));
    const req = (path, method = "GET", payload, extra = {}) =>
      mf.dispatchFetch("https://market.test/quant/api" + path, {
        method,
        headers: {
          "content-type": "application/json",
          ...(path.startsWith("/runner/")
            ? { authorization: "Bearer market-test-only" }
            : { cookie: extra.cookie || cookie }),
          ...extra,
        },
        ...(payload === undefined
          ? {}
          : {
              body:
                typeof payload === "string" ? payload : JSON.stringify(payload),
            }),
      });
    let cookie = "";
    const session = await req("/session");
    cookie = session.headers.get("set-cookie").split(";")[0];
    const owner = (await session.json()).workspace.id;
    const pid = randomUUID(),
      p = fixture.plan;
    const fullScope = JSON.parse(
      await fs.readFile(
        new URL("../contracts/fixtures/market-scope-v1.json", import.meta.url),
        "utf8",
      ),
    );
    await db
      .prepare(
        "INSERT INTO quant_universe_scopes(id,owner,scope_root,spec,created_at) VALUES(?,?,?,?,?)",
      )
      .bind(
        p.universeScopeRef.scopeId,
        owner,
        p.universeScopeRef.scopeRoot,
        canonical(fullScope),
        new Date().toISOString(),
      )
      .run();
    await db
      .prepare(
        "INSERT INTO quant_market_plans(id,owner,scope_id,scope_root,plan_root,profile,spec,created_at) VALUES(?,?,?,?,?,?,?,?)",
      )
      .bind(
        pid,
        owner,
        p.universeScopeRef.scopeId,
        p.universeScopeRef.scopeRoot,
        p.planRoot,
        p.profile,
        canonical(p),
        new Date().toISOString(),
      )
      .run();
    const started = await req(
      "/market-preparation-plans/" + pid + "/start",
      "POST",
      { requestId: randomUUID(), planRoot: p.planRoot },
    );
    assert.equal(started.status, 202, await started.clone().text());
    const jid = (await started.json()).job.id;
    const requestId = randomUUID(),
      body = {
        requestId,
        capability: "market-acquire/1",
        engineVersion: "test",
      };
    const c = await req("/runner/market-acquire/claim", "POST", body);
    assert.equal(c.status, 200, await c.clone().text());
    const claimed = await c.json();
    assert.equal(claimed.job.id, jid);
    return {
      mf,
      db,
      bucket,
      env: { ...bindings, DB: db, ARTIFACTS: bucket },
      req,
      owner,
      cookie,
      pid,
      job: claimed.job,
      claim: body,
    };
  } catch (error) {
    await mf.dispose();
    throw error;
  }
}
async function receipt(x, r, { lostBegin = false } = {}) {
  const attemptId = randomUUID(),
    path = `/runner/market-acquire/jobs/${x.job.id}/requests/${r.requestKey}`;
  const v = { leaseToken: x.job.leaseToken, attemptId };
  const b = await x.req(path + "/begin", "POST", v);
  assert.equal(b.status, 200, await b.clone().text());
  assert.equal((await b.json()).maySend, true);
  if (lostBegin) {
    const again = await x.req(path + "/begin", "POST", v);
    assert.equal((await again.json()).maySend, false);
    return { path, attemptId };
  }
  const original = fixture.receipts[r.requestKey],
    meta = {
      attemptId,
      ...Object.fromEntries(
        ["sha256", "byteLength", "httpStatus", "retrievedAt", "sourceKind"].map(
          (k) => [k, original[k]],
        ),
      ),
    };
  const put = await x.req(path + "/receipt", "PUT", original.rawText, {
    "X-Acquisition-Lease": x.job.leaseToken,
    "X-Acquisition-Receipt": Buffer.from(JSON.stringify(meta)).toString(
      "base64url",
    ),
  });
  assert.equal(put.status, 200, await put.clone().text());
  return put.json();
}
async function stage(x) {
  const receipts = [];
  for (const r of fixture.plan.requests) receipts.push(await receipt(x, r));
  const m = structuredClone(fixture.manifest),
    chunks = structuredClone(fixture.chunks);
  chunks.receipts["0"] = canonical(receipts);
  const b = new TextEncoder().encode(chunks.receipts["0"]);
  m.collections.receipts.chunks[0].sha256 = await crypto.subtle
    .digest("SHA-256", b)
    .then((v) => Buffer.from(v).toString("hex"));
  m.collections.receipts.chunks[0].byteLength = b.length;
  m.collections.receipts.byteLength = b.length;
  const response = await x.req(
    `/runner/market-acquire/jobs/${x.job.id}/publication`,
    "POST",
    { leaseToken: x.job.leaseToken, manifest: m },
  );
  assert.equal(response.status, 200, await response.clone().text());
  const { manifestSha256 } = await response.json();
  for (const [name, c] of Object.entries(m.collections))
    for (const p of c.chunks) {
      const put = await x.req(
        `/runner/market-acquire/jobs/${x.job.id}/publication/${name}/${p.ordinal}?manifestSha256=${manifestSha256}`,
        "PUT",
        chunks[name][p.ordinal],
        { "X-Acquisition-Lease": x.job.leaseToken },
      );
      assert.equal(put.status, 200, await put.clone().text());
      assert.equal((await put.json()).sha256, p.sha256);
    }
  return { root: manifestSha256, manifest: m, chunks };
}

test("real offline normalized two-security source publishes atomically; raw and complete ACK retries preserve identity", async () => {
  const x = await setup();
  try {
    const input = await x.req(
      x.job.inputUrl.replace("/quant/api", ""),
      "GET",
      undefined,
      { "X-Acquisition-Lease": x.job.leaseToken },
    );
    assert.equal(input.status, 200);
    assert.deepEqual((await input.json()).plan, fixture.plan);
    const p = await stage(x),
      path = `/runner/market-acquire/jobs/${x.job.id}/complete`,
      payload = { leaseToken: x.job.leaseToken, manifestSha256: p.root };
    const answer = await x.req(path, "POST", payload);
    assert.equal(answer.status, 200, await answer.clone().text());
    const a = await answer.json();
    assert.equal(a.result.rowCount, 15);
    assert.equal(a.result.symbolCount, 2);
    assert.equal(a.result.marketDatasetRef.datasetRoot, p.root);
    assert.deepEqual(await (await x.req(path, "POST", payload)).json(), a);
    const again = await (
      await x.req("/runner/market-acquire/claim", "POST", x.claim)
    ).json();
    assert.equal(again.claim.status, "completed");
    assert.equal(again.job, null);
    assert.equal(
      (
        await x.db
          .prepare("SELECT count(*) n FROM quant_market_datasets")
          .first()
      ).n,
      1,
    );
    assert.equal(
      (
        await x.db
          .prepare("SELECT count(*) n FROM quant_market_job_receipts")
          .first()
      ).n,
      8,
    );
  } finally {
    await x.mf.dispose();
  }
});
test("unknown outcome remains sticky across claim retry", async () => {
  const x = await setup();
  try {
    const r = fixture.plan.requests[0],
      { path, attemptId } = await receipt(x, r, { lostBegin: true });
    const u = await x.req(path + "/unknown", "POST", {
      leaseToken: x.job.leaseToken,
      attemptId,
      reason: "SYNTHETIC unknown outcome",
    });
    assert.equal((await u.json()).manualReviewRequired, true);
    assert.equal(
      (
        await x.req(path + "/begin", "POST", {
          leaseToken: x.job.leaseToken,
          attemptId: randomUUID(),
        })
      ).status,
      409,
    );
    const again = await (
      await x.req("/runner/market-acquire/claim", "POST", x.claim)
    ).json();
    assert.equal(again.job.id, x.job.id);
    assert.equal(again.job.leaseToken, x.job.leaseToken);
    assert.equal(
      (
        await x.db
          .prepare("SELECT count(*) n FROM quant_market_requests")
          .first()
      ).n,
      1,
    );
  } finally {
    await x.mf.dispose();
  }
});
test("revoking provider scope blocks a new irreversible intent", async () => {
  const x = await setup();
  try {
    const r = fixture.plan.requests[0],
      attemptId = randomUUID(),
      j = await leased(x.env, x.job.id, x.job.leaseToken);
    await assert.rejects(
      () =>
        beginRequest(
          { ...x.env, MARKET_ACQUISITION_AUTH_SCOPE: "another" },
          j.id,
          r.requestKey,
          { leaseToken: j.lease_token, attemptId },
        ),
      (e) => e.code === "MARKET_AUTHORIZATION_CHANGED",
    );
    assert.equal(
      (
        await x.db
          .prepare("SELECT count(*) n FROM quant_market_requests")
          .first()
      ).n,
      0,
    );
  } finally {
    await x.mf.dispose();
  }
});
test("publication transaction abort rolls back dataset and job; same exact staged bytes can complete later", async () => {
  const x = await setup();
  try {
    const p = await stage(x);
    await x.db.exec(
      "CREATE TRIGGER market_abort BEFORE INSERT ON quant_market_datasets BEGIN SELECT RAISE(ABORT,'market publication rollback'); END",
    );
    const path = `/runner/market-acquire/jobs/${x.job.id}/complete`,
      body = { leaseToken: x.job.leaseToken, manifestSha256: p.root };
    assert.equal((await x.req(path, "POST", body)).status, 500);
    assert.equal(
      (
        await x.db
          .prepare("SELECT status FROM quant_market_jobs WHERE id=?")
          .bind(x.job.id)
          .first()
      ).status,
      "running",
    );
    assert.equal(
      (
        await x.db
          .prepare("SELECT count(*) n FROM quant_market_datasets")
          .first()
      ).n,
      0,
    );
    await x.db.exec("DROP TRIGGER market_abort");
    assert.equal((await x.req(path, "POST", body)).status, 200);
  } finally {
    await x.mf.dispose();
  }
});
test("cancellation and expired leases cannot publish a ready market dataset", async () => {
  for (const cause of ["cancel", "expire"]) {
    const x = await setup();
    try {
      const p = await stage(x);
      if (cause === "cancel")
        await x.req(`/market-preparation-jobs/${x.job.id}/cancel`, "POST", {});
      else
        await x.db
          .prepare(
            "UPDATE quant_market_jobs SET lease_until='2000-01-01T00:00:00Z' WHERE id=?",
          )
          .bind(x.job.id)
          .run();
      const r = await x.req(
        `/runner/market-acquire/jobs/${x.job.id}/complete`,
        "POST",
        { leaseToken: x.job.leaseToken, manifestSha256: p.root },
      );
      assert.equal(r.status, 409);
      assert.equal(
        (
          await x.db
            .prepare("SELECT count(*) n FROM quant_market_datasets")
            .first()
        ).n,
        0,
      );
    } finally {
      await x.mf.dispose();
    }
  }
});

test("admissions require fresh explicit capability and enabled flag; preferred auto never implies another family", async () => {
  const { marketResearchAdmissions } = await import(
    "../edge/market-preparation/admissions.mjs"
  );
  const x = await setup();
  try {
    const write = async (value, stamp = new Date().toISOString()) =>
      x.db
        .prepare(
          "INSERT INTO meta(key,value,updated_at) VALUES('runner',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        )
        .bind(JSON.stringify(value), stamp)
        .run();
    const cap = {
      marketResearchProfiles: [
        "pooled_asset_1000_v1",
        "pooled_asset_1000_auto_candidate_v1",
      ],
      transportFormats: ["atlas.quant.bundle/1"],
    };
    await write(cap);
    const off = await marketResearchAdmissions(x.env);
    assert.equal(off.preferredResearchAdmission, null);
    assert.ok(
      off.researchAdmissions.every(
        (r) => r.reason === "MARKET_RESEARCH_DISABLED",
      ),
    );
    const env = { ...x.env, MARKET_RESEARCH_ENABLED: "true" };
    const ready = await marketResearchAdmissions(env);
    assert.equal(
      ready.preferredResearchAdmission,
      "pooled_asset_1000_auto_candidate_v1",
    );
    assert.deepEqual(ready.researchAdmissions[1].families, ["mean_reversion"]);
    await write({ ...cap, marketResearchProfiles: ["pooled_asset_1000_v1"] });
    assert.equal(
      (await marketResearchAdmissions(env)).preferredResearchAdmission,
      "pooled_asset_1000_v1",
    );
    await write(cap, "2000-01-01T00:00:00Z");
    assert.equal(
      (await marketResearchAdmissions(env)).preferredResearchAdmission,
      null,
    );
    const http = await (
      await x.req("/market-preparation-plans/" + x.pid)
    ).json();
    assert.equal(http.preferredResearchAdmission, null);
    assert.equal(http.researchAdmissions.length, 2);
  } finally {
    await x.mf.dispose();
  }
});

test("immutable authorized receipt may be cached by another owner only through its own matching plan; fixture caches fail closed in production", async () => {
  const x = await setup();
  try {
    const r = fixture.plan.requests[0];
    const saved = await receipt(x, r);
    // The second owner receives its own plan and job; it does not gain direct access to the first job.
    const session = await x.mf.dispatchFetch(
      "https://market.test/quant/api/session",
    );
    const cookie = session.headers.get("set-cookie").split(";")[0],
      owner = (await session.json()).workspace.id,
      pid = randomUUID(),
      scopeId = randomUUID();
    const plan = structuredClone(fixture.plan);
    plan.universeScopeRef.scopeId = scopeId;
    plan.planRoot = await digest(
      Object.fromEntries(
        Object.entries(plan).filter(([k]) => k !== "planRoot"),
      ),
    );
    await x.db
      .prepare(
        "INSERT INTO quant_universe_scopes(id,owner,scope_root,spec,created_at) SELECT ?,?,scope_root,spec,created_at FROM quant_universe_scopes WHERE id=?",
      )
      .bind(scopeId, owner, fixture.plan.universeScopeRef.scopeId)
      .run();
    await x.db
      .prepare(
        "INSERT INTO quant_market_plans(id,owner,scope_id,scope_root,plan_root,profile,spec,created_at) VALUES(?,?,?,?,?,?,?,?)",
      )
      .bind(
        pid,
        owner,
        scopeId,
        plan.universeScopeRef.scopeRoot,
        plan.planRoot,
        plan.profile,
        canonical(plan),
        new Date().toISOString(),
      )
      .run();
    const started = await startJob(x.env, owner, pid, {
      requestId: randomUUID(),
      planRoot: plan.planRoot,
    });
    const claimed = await (
      await x.req("/runner/market-acquire/claim", "POST", {
        requestId: randomUUID(),
        capability: "market-acquire/1",
        engineVersion: "test",
      })
    ).json();
    assert.equal(claimed.job.id, started.job.id);
    const j = await leased(x.env, claimed.job.id, claimed.job.leaseToken);
    const ack = await beginRequest(x.env, j.id, r.requestKey, {
      leaseToken: j.lease_token,
      attemptId: randomUUID(),
    });
    assert.equal(ack.maySend, false);
    assert.equal(ack.receiptId, saved.receiptId);
    await assert.rejects(
      () =>
        getReceipt(
          { ...x.env, ALLOW_MARKET_FIXTURES: "false" },
          j,
          r.requestKey,
        ),
      (e) => e.code === "MARKET_SOURCE_KIND",
    );
    const old = await x.req(
      "/market-preparation-jobs/" + x.job.id,
      "GET",
      undefined,
      { cookie },
    );
    assert.equal(old.status, 404);
  } finally {
    await x.mf.dispose();
  }
});
