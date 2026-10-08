import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import fsPath from "node:path";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { canonical, digest } from "../edge/market-preparation/common.mjs";
import { createMarketPlan } from "../edge/market-preparation/planner.mjs";
import { marketTarHeader } from "../edge/market-preparation/archive.mjs";
import { bundleFixture } from "./fixtures/bundle-fixture.mjs";
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
async function setup(extraBindings = {}) {
  const bindings = {
    RUNNER_SECRET: "market-test-only",
    MARKET_ACQUISITION_ENABLED: "true",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    MARKET_ACQUISITION_AUTH_SCOPE: "SYNTHETIC_MARKET_TEST",
    ALLOW_MARKET_FIXTURES: "true",
    ...extraBindings,
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
async function stage(x, transform) {
  const receipts = [];
  for (const r of fixture.plan.requests) receipts.push(await receipt(x, r));
  const m = structuredClone(fixture.manifest),
    chunks = structuredClone(fixture.chunks);
  const oldReceipts = JSON.parse(chunks.receipts["0"]);
  chunks.receipts["0"] = canonical(
    receipts.map((r, i) => ({ ...r, rawLocation: oldReceipts[i].rawLocation })),
  );
  const b = new TextEncoder().encode(chunks.receipts["0"]);
  m.collections.receipts.chunks[0].sha256 = await crypto.subtle
    .digest("SHA-256", b)
    .then((v) => Buffer.from(v).toString("hex"));
  m.collections.receipts.chunks[0].byteLength = b.length;
  m.collections.receipts.byteLength = b.length;
  if (transform) await transform(m, chunks);
  const response = await x.req(
    `/runner/market-acquire/jobs/${x.job.id}/publication`,
    "POST",
    { leaseToken: x.job.leaseToken, manifest: m },
  );
  assert.equal(response.status, 200, await response.clone().text());
  const { manifestSha256 } = await response.json();
  for (const [name, c] of Object.entries({
    ...m.collections,
    raw: m.rawArchive,
  }))
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

async function marketForecastJob(x) {
  const p = await stage(x);
  const done = await (
    await x.req(`/runner/market-acquire/jobs/${x.job.id}/complete`, "POST", {
      leaseToken: x.job.leaseToken,
      manifestSha256: p.root,
    })
  ).json();
  const binding = {
    marketDatasetRef: done.result.marketDatasetRef,
    universeScopeRef: fixture.plan.universeScopeRef,
    admissionProfile: "pooled_asset_1000_v1",
  };
  const strategy = {
    schemaVersion: 2,
    name: "SYNTHETIC market transport fixture; no fitted model",
    research: { mode: "statistical_quant" },
    universe: {
      symbols: fixture.plan.scope.symbols,
      start: fixture.plan.scope.start,
      end: fixture.plan.scope.end,
    },
    target: { kind: "asset_price", horizonSessions: 5 },
    model: {
      family: "mean_reversion",
      estimator: "ridge",
      trainWindow: 120,
      refitDays: 20,
    },
    validation: { innerFolds: 2, outerFolds: 2, minTrainDates: 40 },
    execution: { enabled: false },
    factors: [],
  };
  const e = await (
    await x.req("/statistical-quant/experiments", "POST", {
      strategy,
      ...binding,
    })
  ).json();
  const queued = await x.req(
    `/statistical-quant/experiments/${e.experiment.id}/run`,
    "POST",
    { version: 1, dataSource: "ready_market", ...binding },
  );
  assert.equal(queued.status, 202, await queued.clone().text());
  const claim = await x.req("/runner/claim", "POST", {
    requestId: randomUUID(),
    engineVersion: "0.8.0",
    transportFormats: ["atlas.quant.bundle/1"],
    marketResearchProfiles: ["pooled_asset_1000_v1"],
  });
  assert.equal(claim.status, 200, await claim.clone().text());
  return { p, job: (await claim.json()).job };
}

test("server-bound market bundle pins complete rows; ordinary profile claims and rehashed changed snapshots cannot replace it", async () => {
  for (const variant of ["incomplete-clock", "changed-row", "forged-source"]) {
    const x = await setup({
      MARKET_RESEARCH_ENABLED: "true",
      BUNDLE_SNAPSHOT_SORTED_V1: "true",
    });
    try {
      const { p, job } = await marketForecastJob(x);
      const f = bundleFixture({
        count: 1,
        rowsPerChunk: 100,
        mutate({ forecast, report, snapshot }) {
          forecast.sourceStrategy = job.strategy;
          report.strategy = job.strategy;
          report.provenance = {
            ...JSON.parse(p.chunks.provenance[0])[0],
            marketSource: job.sourceEvidence,
          };
          snapshot.provenance = {
            ...JSON.parse(p.chunks.provenance[0])[0],
            marketSource: job.sourceEvidence,
          };
          snapshot.rows = JSON.parse(p.chunks.rows[0]);
          if (variant === "changed-row") snapshot.rows[0].raw_close += 1;
          if (variant === "forged-source")
            snapshot.provenance.marketSource = {
              ...job.sourceEvidence,
              rowValueRoot: "0".repeat(64),
            };
        },
      });
      const identity = {
        id: job.id,
        leaseToken: job.leaseToken,
        bundleId: f.bundleId,
      };
      const begin = await x.req("/runner/bundles/begin", "POST", {
        ...identity,
        manifestText: f.manifestText,
      });
      if (variant === "forged-source") {
        assert.equal(begin.status, 409, await begin.clone().text());
        assert.equal((await begin.json()).error.code, "MARKET_BUNDLE_SOURCE");
        continue;
      }
      assert.equal(begin.status, 200, await begin.clone().text());
      const staged = await begin.json();
      for (const [key, raw] of f.chunks) {
        const [name, i] = key.split(":");
        const put = await x.req(
          `/runner/bundles/${f.bundleId}/chunks/${name}/${i}`,
          "PUT",
          raw,
          {
            "X-Quant-Job": job.id,
            "X-Quant-Lease": job.leaseToken,
            "X-Quant-Stage": staged.stageId,
          },
        );
        assert.equal(put.status, 200, await put.clone().text());
      }
      const packet = { ...identity, stageId: staged.stageId };
      const verify = await x.req("/runner/bundles/finalize", "POST", packet);
      if (variant === "changed-row") {
        assert.equal(verify.status, 409, await verify.clone().text());
        assert.equal(
          (await verify.json()).error.code,
          "MARKET_SNAPSHOT_SOURCE",
        );
        continue;
      }
      // This intentionally tiny source has no reportable research window. The
      // unchanged source alone cannot justify a fabricated forecast calendar.
      assert.equal(verify.status, 409, await verify.clone().text());
      assert.equal(
        (await verify.json()).error.code,
        "MARKET_FORECAST_COVERAGE",
      );
      assert.equal(
        (
          await x.db
            .prepare("SELECT status FROM quant_bundle_stages WHERE id=?")
            .bind(staged.stageId)
            .first()
        ).status,
        "staging",
      );
    } finally {
      await x.mf.dispose();
    }
  }
});

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
    const archivePath = `/market-datasets/${a.result.marketDatasetRef.datasetId}/download?datasetRoot=${p.root}`;
    const archive = await fetch(
      new URL("/quant/api" + archivePath, await x.mf.ready),
      { headers: { cookie: x.cookie, "accept-encoding": "identity" } },
    );
    assert.equal(archive.status, 200, await archive.clone().text());
    assert.equal(archive.headers.get("x-atlas-market-root"), p.root);
    assert.equal(archive.headers.get("content-encoding"), "identity");
    assert.match(archive.headers.get("cache-control"), /no-transform/);
    const tar = new Uint8Array(await archive.arrayBuffer());
    const expected = [
      ["manifest.json", canonical(p.manifest)],
      ["plan.json", canonical(fixture.plan)],
      [
        "scope.json",
        canonical(
          JSON.parse(
            await fs.readFile(
              new URL(
                "../contracts/fixtures/market-scope-v1.json",
                import.meta.url,
              ),
              "utf8",
            ),
          ),
        ),
      ],
      ...Object.keys(p.manifest.collections)
        .sort()
        .flatMap((name) =>
          p.manifest.collections[name].chunks.map((c) => [
            `parts/${name}/${c.ordinal}.bin`,
            p.chunks[name][c.ordinal],
          ]),
        ),
      ...p.manifest.rawArchive.chunks.map((c) => [
        `parts/raw/${c.ordinal}.bin`,
        p.chunks.raw[c.ordinal],
      ]),
    ];
    let at = 0;
    for (const [name, text] of expected) {
      const raw = new TextEncoder().encode(text);
      assert.deepEqual(
        tar.slice(at, at + 512),
        marketTarHeader(name, raw.length),
      );
      at += 512;
      assert.deepEqual(tar.slice(at, at + raw.length), raw);
      at += raw.length;
      const padding = (512 - (raw.length % 512)) % 512;
      assert.deepEqual(tar.slice(at, at + padding), new Uint8Array(padding));
      at += padding;
    }
    assert.deepEqual(tar.slice(at), new Uint8Array(1024));
    const temporary = await fs.mkdtemp(
      fsPath.join(os.tmpdir(), "atlas-market-http-audit-"),
    );
    try {
      const archiveFile = fsPath.join(temporary, "source.tar");
      await fs.writeFile(archiveFile, tar, { mode: 0o600 });
      const audited = spawnSync(
        process.env.PYTHON || ".venv/bin/python",
        [
          "scripts/audit-market-dataset.py",
          archiveFile,
          "--expected-root",
          p.root,
        ],
        { encoding: "utf8", maxBuffer: 256 * 1024 },
      );
      assert.equal(audited.status, 0, audited.stdout + audited.stderr);
      const report = JSON.parse(audited.stdout);
      assert.equal(report.status, "PASS");
      assert.equal(report.normalizationVerified, true);
      assert.equal(report.sourceAuthorityVerified, false);
      assert.equal(report.providerCalls, 0);
    } finally {
      await fs.rm(temporary, { recursive: true, force: true });
    }
    assert.equal(
      (
        await x.req(archivePath, "GET", undefined, {
          cookie: "aq_session=other-owner",
        })
      ).status,
      401,
    );
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

test("a rehashed raw group or raw offset cannot change the exact saved response", async () => {
  for (const kind of ["body", "offset", "tail"]) {
    const x = await setup();
    try {
      const p = await stage(x, async (m, chunks) => {
        let name = "raw";
        if (kind === "body")
          chunks.raw["0"] = chunks.raw["0"].replace('"code":0', '"code":1');
        if (kind === "tail") chunks.raw["0"] += " ";
        if (kind === "offset") {
          name = "receipts";
          const rows = JSON.parse(chunks.receipts["0"]);
          rows[0].rawLocation.offset = 1;
          chunks.receipts["0"] = canonical(rows);
        }
        const raw = new TextEncoder().encode(chunks[name]["0"]),
          c = name === "raw" ? m.rawArchive : m.collections.receipts;
        c.chunks[0].sha256 = Buffer.from(
          await crypto.subtle.digest("SHA-256", raw),
        ).toString("hex");
        c.chunks[0].byteLength = raw.length;
        c.byteLength = raw.length;
      });
      const response = await x.req(
        `/runner/market-acquire/jobs/${x.job.id}/complete`,
        "POST",
        { leaseToken: x.job.leaseToken, manifestSha256: p.root },
      );
      assert.equal(response.status, 409, await response.clone().text());
      assert.equal((await response.json()).error.code, "MARKET_RAW_ARCHIVE");
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
test("source progress counts retained receipts once without calling them completed research", async () => {
  const x = await setup();
  const read = async () => (await (await x.req('/market-preparation-jobs/' + x.job.id)).json()).job;
  try {
    const empty = await read();
    assert.deepEqual(empty.sourceProgress, {
      declaredRequests: fixture.plan.budget.declaredRequests,
      receiptsSaved: 0, rawBytesSaved: 0, outcomeUnknown: 0,
    });
    const request = fixture.plan.requests[0];
    const saved = await receipt(x, request);
    const first = await read();
    assert.equal(first.sourceProgress.receiptsSaved, 1);
    assert.equal(first.sourceProgress.rawBytesSaved, fixture.receipts[request.requestKey].byteLength);
    assert.equal(first.status, 'running');
    const recovered = await beginRequest(x.env, x.job.id, request.requestKey, {
      leaseToken: x.job.leaseToken, attemptId: randomUUID(),
    });
    assert.equal(recovered.receiptId, saved.receiptId);
    assert.equal(recovered.maySend, false);
    assert.deepEqual((await read()).sourceProgress, first.sourceProgress);
  } finally { await x.mf.dispose(); }
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
    const progress = (await (await x.req('/market-preparation-jobs/' + x.job.id)).json()).job.sourceProgress;
    assert.equal(progress.outcomeUnknown, 1);
    assert.equal(progress.receiptsSaved, 0);
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
    const cachedProgress = (await (await x.req('/market-preparation-jobs/' + j.id, 'GET', undefined, { cookie })).json()).job.sourceProgress;
    assert.equal(cachedProgress.receiptsSaved, 1, 'owner-authorized cached source is a retained receipt, not a new provider attempt');
    assert.equal(cachedProgress.rawBytesSaved, fixture.receipts[r.requestKey].byteLength);
    assert.equal(cachedProgress.outcomeUnknown, 0);
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

test("ready market binding survives create/update/copy/readback and exact-profile claim; no source shrinking or legacy bypass", async () => {
  const x = await setup({ MARKET_RESEARCH_ENABLED: "true" });
  try {
    // A dedicated feature flag is used only in this isolated Worker instance.
    const p = await stage(x),
      complete = await (
        await x.req(
          `/runner/market-acquire/jobs/${x.job.id}/complete`,
          "POST",
          { leaseToken: x.job.leaseToken, manifestSha256: p.root },
        )
      ).json();
    const ref = complete.result.marketDatasetRef,
      scope = fixture.plan.universeScopeRef;
    const strategy = {
      schemaVersion: 2,
      name: "SYNTHETIC source-binding protocol test",
      research: { mode: "statistical_quant" },
      universe: {
        symbols: fixture.plan.scope.symbols,
        start: fixture.plan.scope.start,
        end: fixture.plan.scope.end,
      },
      target: { kind: "asset_price", horizonSessions: 5 },
      model: {
        family: "mean_reversion",
        estimator: "ridge",
        trainWindow: 120,
        refitDays: 20,
      },
      validation: { innerFolds: 2, outerFolds: 2, minTrainDates: 40 },
      execution: { enabled: false },
      factors: [{ id: "pb", expression: "pb", role: "predictor" }],
    };
    const binding = {
      marketDatasetRef: ref,
      universeScopeRef: scope,
      admissionProfile: "pooled_asset_1000_v1",
    };
    let r = await x.req("/statistical-quant/experiments", "POST", {
      strategy,
      ...binding,
    });
    assert.equal(r.status, 201, await r.clone().text());
    let e = (await r.json()).experiment;
    assert.deepEqual(e.marketDatasetBinding.marketDatasetRef, ref);
    assert.deepEqual(
      e.marketDatasetBinding.scope.symbols,
      strategy.universe.symbols,
    );
    r = await x.req("/statistical-quant/experiments/" + e.id, "PUT", {
      version: 1,
      strategy: { ...strategy, name: "Changed label" },
    });
    assert.equal(r.status, 200, await r.clone().text());
    e = (await r.json()).experiment;
    assert.equal(e.version, 2);
    assert.deepEqual(e.marketDatasetBinding.marketDatasetRef, ref);
    const detail = await (
      await x.req("/statistical-quant/experiments/" + e.id)
    ).json();
    assert.deepEqual(
      detail.experiment.marketDatasetBinding,
      e.marketDatasetBinding,
    );
    const list = await (await x.req("/statistical-quant/experiments")).json();
    assert.deepEqual(
      list.items[0].marketDatasetBinding,
      e.marketDatasetBinding,
    );
    r = await x.req(
      "/statistical-quant/experiments/" + e.id + "/copy",
      "POST",
      {},
    );
    assert.equal(r.status, 201, await r.clone().text());
    assert.deepEqual(
      (await r.json()).experiment.marketDatasetBinding,
      e.marketDatasetBinding,
    );
    // Equal strategy bytes cannot let the losing CAS attach its different source.
    const unbound = (
      await (
        await x.req("/statistical-quant/experiments", "POST", {
          strategy,
          universeScopeRef: scope,
        })
      ).json()
    ).experiment;
    const racers = await Promise.all([
      x.req("/statistical-quant/experiments/" + unbound.id, "PUT", {
        version: 1,
        strategy,
        ...binding,
      }),
      x.req("/statistical-quant/experiments/" + unbound.id, "PUT", {
        version: 1,
        strategy,
      }),
    ]);
    assert.deepEqual(racers.map((r) => r.status).sort(), [200, 409]);
    const winner = (await racers.find((r) => r.status === 200).json())
      .experiment;
    const readback = (
      await (await x.req("/statistical-quant/experiments/" + unbound.id)).json()
    ).experiment;
    assert.deepEqual(
      readback.marketDatasetBinding,
      winner.marketDatasetBinding,
    );
    const shrunk = structuredClone(strategy);
    shrunk.universe.symbols.pop();
    assert.equal(
      (
        await x.req("/statistical-quant/experiments/" + e.id, "PUT", {
          version: 2,
          strategy: shrunk,
        })
      ).status,
      400,
    );
    assert.equal(
      (
        await x.req("/statistical-quant/experiments/" + e.id + "/run", "POST", {
          version: 2,
          dataSource: "demo",
        })
      ).status,
      409,
    );
    r = await x.req("/statistical-quant/experiments/" + e.id + "/run", "POST", {
      version: 2,
      dataSource: "ready_market",
      ...binding,
    });
    assert.equal(r.status, 202, await r.clone().text());
    const job = (await r.json()).job;
    let c = await (
      await x.req("/runner/claim", "POST", {
        requestId: randomUUID(),
        engineVersion: "0.8.0",
        transportFormats: ["atlas.quant.bundle/1"],
      })
    ).json();
    assert.equal(c.job, null);
    const requestId = randomUUID();
    r = await x.req("/runner/claim", "POST", {
      requestId,
      engineVersion: "0.8.0",
      transportFormats: ["atlas.quant.bundle/1"],
      marketResearchProfiles: ["pooled_asset_1000_v1"],
    });
    assert.equal(r.status, 200, await r.clone().text());
    c = await r.json();
    assert.equal(c.job.id, job.id);
    assert.deepEqual(c.job.marketDatasetRef, ref);
    assert.equal(c.job.dataset, null);
    assert.equal(c.job.strategy.universe.symbols.length, 2);
    const header = { "X-Dataset-Lease": c.job.leaseToken };
    const inputResponse = await x.req(
      c.job.marketInputUrl.replace("/quant/api", ""),
      "GET",
      undefined,
      header,
    );
    assert.equal(inputResponse.status, 200, await inputResponse.clone().text());
    const sourceInput = await inputResponse.json();
    assert.deepEqual(sourceInput.marketDatasetRef, ref);
    assert.equal(sourceInput.sourceEvidence.rowValueRoot.length, 64);
    const rawManifest = await x.req(
      sourceInput.documents.manifest.url.replace("/quant/api", ""),
      "GET",
      undefined,
      header,
    );
    assert.equal(rawManifest.status, 200);
    assert.equal(rawManifest.headers.get("x-content-sha256"), ref.datasetRoot);
    assert.equal(await rawManifest.text(), canonical(p.manifest));
    const part = sourceInput.partUrlTemplate
      .replace("{collection}", "rows")
      .replace("{ordinal}", "0")
      .replace("/quant/api", "");
    const rowsResponse = await x.req(part, "GET", undefined, header);
    assert.equal(rowsResponse.status, 200);
    assert.equal((await rowsResponse.json()).length, 15);
    assert.equal(
      (
        await x.req(
          part.replace(ref.datasetRoot, "0".repeat(64)),
          "GET",
          undefined,
          header,
        )
      ).status,
      409,
    );
    assert.equal(
      (await x.req(part, "GET", undefined, { "X-Dataset-Lease": randomUUID() }))
        .status,
      409,
    );
    const datasets = await (await x.req("/market-datasets")).json();
    assert.deepEqual(datasets.items[0].marketDatasetRef, ref);
    const sourceDetail = await (
      await x.req(
        "/market-datasets/" + ref.datasetId + "?datasetRoot=" + ref.datasetRoot,
      )
    ).json();
    assert.deepEqual(sourceDetail.scope.symbols, strategy.universe.symbols);
    r = await x.req("/runner/claim", "POST", {
      requestId,
      engineVersion: "0.8.0",
      transportFormats: ["atlas.quant.bundle/1"],
    });
    assert.equal(r.status, 409);
    assert.equal((await r.json()).error.code, "RUNNER_UPGRADE_REQUIRED");
  } finally {
    await x.mf.dispose();
  }
});
