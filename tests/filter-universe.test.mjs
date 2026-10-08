import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { universeRegistryStatements } from "../scripts/universe-registry.mjs";
import { createMarketPlan } from "../edge/market-preparation/planner.mjs";
const schema = await fs.readFile(
  new URL("../edge/schema.sql", import.meta.url),
  "utf8",
);
const script = await buildWorkerSource({ buildId: "whole-filter-test" });
const symbols = Array.from(
  { length: 1000 },
  (_, i) => `${String(i + 1).padStart(6, "0")}.${i % 2 ? "SH" : "SZ"}`,
).sort();
const source = {
  source: "SYNTHETIC_FIXTURE",
  fetchedAt: "20261008",
  securities: symbols.map((ts_code) => ({
    ts_code,
    name: "SYNTHETIC",
    area: "北京",
    industry: "材料",
  })),
  items: [{ id: "all1000", name: "SYNTHETIC 1000", symbols }],
};
const selection = {
  version: 1,
  includeGroups: [
    { id: "all", filters: [{ field: "universe", value: "all1000" }] },
  ],
};
async function fixture() {
  const mf = new Miniflare({
      modules: true,
      script,
      compatibilityDate: "2026-08-01",
      d1Databases: ["DB"],
      r2Buckets: ["ARTIFACTS"],
      bindings: {
        RUNNER_SECRET: "test-only",
        MARKET_ACQUISITION_AUTH_SCOPE: "synthetic-scope-test",
      },
    }),
    db = await mf.getD1Database("DB");
  await db.exec(schema.replaceAll("\n", " "));
  const item = source.items[0];
  await db
    .prepare(
      "INSERT INTO research_universes(id,name,category,symbol_count,search_text,metadata) VALUES(?,?,?,?,?,?)",
    )
    .bind(item.id, item.name, "fixture", 1000, item.name, JSON.stringify(item))
    .run();
  for (const sql of universeRegistryStatements(source).statements)
    await db.exec(sql);
  const request = (path, method = "GET", value, cookie) =>
    mf.dispatchFetch("https://whole.test/quant/api" + path, {
      method,
      headers: {
        ...(cookie ? { cookie } : {}),
        ...(value ? { "content-type": "application/json" } : {}),
      },
      ...(value ? { body: JSON.stringify(value) } : {}),
    });
  const session = await request("/session"),
    cookie = session.headers.get("set-cookie").split(";")[0];
  return { mf, db, request, cookie };
}
async function freeze(x, override = {}) {
  const resolved = await (
    await x.request(
      "/universes/resolve",
      "POST",
      { selection: override.selection ?? selection },
      x.cookie,
    )
  ).json();
  const input = {
    selection,
    expectedResolutionHash: resolved.resolutionHash,
    expectedSnapshotHash: resolved.snapshotHash,
    start: "20240101",
    end: "20241231",
    ...override,
  };
  const response = await x.request("/universe-scopes", "POST", input, x.cookie);
  return { response, input, value: await response.json(), resolved };
}
function strategy(scope) {
  return {
    schemaVersion: 2,
    name: "SYNTHETIC whole-pool scope",
    universe: { symbols: scope.symbols, start: scope.start, end: scope.end },
    research: { mode: "statistical_quant", observationDays: 1 },
    target: { kind: "asset_price", horizonSessions: 5 },
    model: {
      family: "mean_reversion",
      estimator: "auto",
      trainWindow: 120,
      refitDays: 20,
    },
    factors: [
      { id: "f", expression: "rank(returns(close,20))", role: "predictor" },
    ],
    execution: { enabled: false },
  };
}

test("real D1 freezes all 1000 members idempotently, owner-isolates and refuses forged preview/subset", async () => {
  const x = await fixture();
  try {
    const f = await freeze(x);
    assert.equal(f.response.status, 201);
    assert.equal(f.value.scope.symbolCount, 1000);
    assert.deepEqual(f.value.scope.symbols, symbols);
    assert.equal(f.resolved.requiresSubset, false);
    assert.equal(f.resolved.membershipPolicy, "complete_filtered_set");
    assert.equal(f.resolved.maxRunSymbols, undefined);
    const again = await x.request(
      "/universe-scopes",
      "POST",
      f.input,
      x.cookie,
    );
    assert.equal(again.status, 200);
    assert.deepEqual((await again.json()).scopeRef, f.value.scopeRef);
    assert.equal(
      (
        await x.request(
          "/universe-scopes",
          "POST",
          { ...f.input, expectedResolutionHash: "a".repeat(64) },
          x.cookie,
        )
      ).status,
      409,
    );
    assert.equal(
      (
        await x.request(
          "/universe-scopes",
          "POST",
          { ...f.input, symbols: symbols.slice(0, 50) },
          x.cookie,
        )
      ).status,
      400,
    );
    const other = await x.request("/session"),
      otherCookie = other.headers.get("set-cookie").split(";")[0];
    assert.equal(
      (
        await x.request(
          "/universe-scopes/" + f.value.scopeRef.scopeId,
          "GET",
          undefined,
          otherCookie,
        )
      ).status,
      404,
    );
    await x.db.prepare("DELETE FROM meta WHERE key='universe_registry'").run();
    const stored = await x.request(
      "/universe-scopes/" + f.value.scopeRef.scopeId,
      "GET",
      undefined,
      x.cookie,
    );
    assert.equal(stored.status, 200);
    assert.deepEqual((await stored.json()).scope.symbols, symbols);
  } finally {
    await x.mf.dispose();
  }
});

test("experiment create/update/copy/run persist exact complete scope; missing member/date or alternate ref cannot escape", async () => {
  const x = await fixture();
  try {
    const { value: f } = await freeze(x),
      s = strategy(f.scope);
    const saved = await x.request(
      "/statistical-quant/experiments",
      "POST",
      { strategy: s, universeScopeRef: f.scopeRef },
      x.cookie,
    );
    assert.equal(saved.status, 201, await saved.clone().text());
    const e = (await saved.json()).experiment;
    assert.deepEqual(e.universeScopeRef, f.scopeRef);
    assert.equal(e.strategy.universe.symbols.length, 1000);
    assert.equal(
      e.strategy.model.estimator,
      "auto",
      "save must not silently change model",
    );
    assert.equal(e.strategy.universe.subsetPolicy, "all");
    for (const universe of [
      { ...s.universe, symbols: symbols.slice(0, 50) },
      { ...s.universe, end: "20241230" },
      { ...s.universe, subsetPolicy: "explicit" },
    ]) {
      const r = await x.request(
        "/statistical-quant/experiments/" + e.id,
        "PUT",
        { strategy: { ...s, universe }, version: 1 },
        x.cookie,
      );
      assert.equal(r.status, 400);
      assert.equal((await r.json()).error.code, "WHOLE_UNIVERSE_MISMATCH");
    }
    const update = await x.request(
      "/statistical-quant/experiments/" + e.id,
      "PUT",
      { strategy: { ...s, name: "New version" }, version: 1 },
      x.cookie,
    );
    assert.equal(update.status, 200, await update.clone().text());
    assert.deepEqual(
      (await update.json()).experiment.universeScopeRef,
      f.scopeRef,
    );
    const links = await x.db
      .prepare(
        "SELECT version,scope_id FROM quant_experiment_scopes WHERE experiment_id=? ORDER BY version",
      )
      .bind(e.id)
      .all();
    assert.deepEqual(
      links.results.map((x) => x.version),
      [1, 2],
    );
    assert(links.results.every((x) => x.scope_id === f.scopeRef.scopeId));
    const copy = await x.request(
      "/statistical-quant/experiments/" + e.id + "/copy",
      "POST",
      {},
      x.cookie,
    );
    assert.equal(copy.status, 201, await copy.clone().text());
    assert.deepEqual(
      (await copy.json()).experiment.universeScopeRef,
      f.scopeRef,
    );
    const list = await (
      await x.request(
        "/statistical-quant/experiments",
        "GET",
        undefined,
        x.cookie,
      )
    ).json();
    assert(
      list.items.every(
        (x) => x.universeScopeRef.scopeId === f.scopeRef.scopeId,
      ),
    );
    const rejected = await x.request(
      "/statistical-quant/experiments/" + e.id + "/run",
      "POST",
      { version: 2, dataSource: "demo" },
      x.cookie,
    );
    assert.equal(rejected.status, 409);
    assert.equal(
      (await rejected.json()).error.code,
      "WHOLE_UNIVERSE_PROFILE_NOT_READY",
    );
    assert.equal(
      (await x.db.prepare("SELECT count(*) n FROM jobs").first()).n,
      0,
    );
    const wrong = await x.request(
      "/statistical-quant/experiments/" + e.id + "/run",
      "POST",
      {
        version: 2,
        dataSource: "demo",
        universeScopeRef: { ...f.scopeRef, scopeRoot: "f".repeat(64) },
      },
      x.cookie,
    );
    assert.equal(wrong.status, 409);
    assert.equal((await wrong.json()).error.code, "UNIVERSE_RUN_BINDING");
  } finally {
    await x.mf.dispose();
  }
});

test("independent market plan declares every 2002/3002 one-attempt request without provider, supports bounded paging", async () => {
  const x = await fixture();
  try {
    const { value: f } = await freeze(x);
    const request = {
      scopeRef: f.scopeRef,
      profile: "pooled_asset_1000_v1",
      requiredFields: ["close", "vol"],
    };
    const r = await x.request(
      "/market-preparation-plans",
      "POST",
      request,
      x.cookie,
    );
    assert.equal(r.status, 201, await r.clone().text());
    const p = await r.json();
    assert.equal(p.budget.declaredRequests, 2002);
    assert.equal(p.budget.materializedRequests, 2002);
    assert.equal(p.canStart, false);
    assert.equal(p.providerCalls, 0);
    assert.equal(p.scope.symbolCount, 1000);
    assert.deepEqual(p.blockedReasons, []);
    const first = await (
      await x.request(
        "/market-preparation-plans/" +
          p.planRef.planId +
          "/requests?pageSize=2",
        "GET",
        undefined,
        x.cookie,
      )
    ).json();
    assert.equal(first.total, 2002);
    assert(
      first.items.every(
        (x) => x.apiName === "trade_cal" && x.maxAttempts === 1,
      ),
    );
    assert.deepEqual(
      first.items.map((x) => x.params.exchange),
      ["SSE", "SZSE"],
    );
    const b = await (
      await x.request(
        "/market-preparation-plans",
        "POST",
        { ...request, requiredFields: ["pb"] },
        x.cookie,
      )
    ).json();
    assert.equal(b.budget.declaredRequests, 3002);
    assert.notEqual(b.planRef.planRoot, p.planRef.planRoot);
    const again = await (
      await x.request("/market-preparation-plans", "POST", request, x.cookie)
    ).json();
    assert.deepEqual(again.planRef, p.planRef);
    const changed = await createMarketPlan(
      f.scope,
      f.scopeRef,
      request,
      "different-authorized-config",
      "20261008",
    );
    const original = await createMarketPlan(
      f.scope,
      f.scopeRef,
      request,
      "synthetic-scope-test",
      "20261008",
    );
    assert.notEqual(
      changed.requests[0].requestKey,
      original.requests[0].requestKey,
    );
  } finally {
    await x.mf.dispose();
  }
});

test("unsupported full dates/members remain intact with explicit blockers and zero generated requests", async () => {
  const x = await fixture();
  try {
    const { value: f } = await freeze(x, { start: "20220101" }),
      input = { profile: "pooled_asset_1000_v1", requiredFields: ["close"] };
    const plan = await createMarketPlan(
      f.scope,
      f.scopeRef,
      input,
      "synthetic",
      "20261008",
    );
    assert.equal(plan.scope.symbolCount, 1000);
    assert.deepEqual(plan.scope.symbols, symbols);
    assert(plan.blockedReasons.some((x) => x.code === "DATE_BUDGET"));
    assert.equal(plan.requests.length, 0);
    assert.equal(plan.scope.start, "20220101");
    await assert.rejects(
      () =>
        createMarketPlan(
          f.scope,
          f.scopeRef,
          { ...input, requiredFields: ["model_fin_roe"] },
          "synthetic",
          "20261008",
        ),
      (e) => e.code === "MARKET_FIELDS_UNSUPPORTED",
    );
  } finally {
    await x.mf.dispose();
  }
});

test("scope binding and experiment revision roll back atomically; concurrent edits preserve one exact version", async () => {
  const x = await fixture();
  try {
    const { value: f } = await freeze(x),
      s = strategy(f.scope);
    const e = (
      await (
        await x.request(
          "/statistical-quant/experiments",
          "POST",
          { strategy: s, universeScopeRef: f.scopeRef },
          x.cookie,
        )
      ).json()
    ).experiment;
    await x.db.exec(
      "CREATE TRIGGER scope_abort BEFORE INSERT ON quant_experiment_scopes WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT,'scope rollback test'); END",
    );
    const failed = await x.request(
      "/statistical-quant/experiments/" + e.id,
      "PUT",
      { strategy: { ...s, name: "Aborted version" }, version: 1 },
      x.cookie,
    );
    assert.equal(failed.status, 500);
    assert.equal(
      (
        await x.db
          .prepare("SELECT version FROM quant_experiments WHERE id=?")
          .bind(e.id)
          .first()
      ).version,
      1,
    );
    assert.equal(
      (
        await x.db
          .prepare(
            "SELECT count(*) n FROM quant_experiment_versions WHERE experiment_id=?",
          )
          .bind(e.id)
          .first()
      ).n,
      1,
    );
    await x.db.exec("DROP TRIGGER scope_abort");
    const replies = await Promise.all(
      ["Writer A", "Writer B"].map((name) =>
        x.request(
          "/statistical-quant/experiments/" + e.id,
          "PUT",
          { strategy: { ...s, name }, version: 1 },
          x.cookie,
        ),
      ),
    );
    assert.deepEqual(replies.map((r) => r.status).sort(), [200, 409]);
    const versions = await x.db
      .prepare(
        "SELECT v.version,v.spec,s.scope_id,s.scope_root FROM quant_experiment_versions v JOIN quant_experiment_scopes s ON s.experiment_id=v.experiment_id AND s.version=v.version WHERE v.experiment_id=? ORDER BY v.version",
      )
      .bind(e.id)
      .all();
    assert.deepEqual(
      versions.results.map((r) => r.version),
      [1, 2],
    );
    assert(
      versions.results.every(
        (r) =>
          r.scope_id === f.scopeRef.scopeId &&
          r.scope_root === f.scopeRef.scopeRoot,
      ),
    );
    const head = await x.db
      .prepare("SELECT spec FROM quant_experiments WHERE id=?")
      .bind(e.id)
      .first();
    assert.equal(head.spec, versions.results[1].spec);
  } finally {
    await x.mf.dispose();
  }
});

test("run scope identity is key-order independent but rejects changed identity, schema and owner before enqueue", async () => {
  const x = await fixture();
  try {
    const { value: f } = await freeze(x, {
      selection: {
        version: 1,
        includeGroups: [],
        includeSymbols: symbols.slice(0, 3),
      },
    });
    const saved = await x.request(
      "/statistical-quant/experiments",
      "POST",
      { strategy: strategy(f.scope), universeScopeRef: f.scopeRef },
      x.cookie,
    );
    assert.equal(saved.status, 201, await saved.clone().text());
    const e = (await saved.json()).experiment,
      path = "/statistical-quant/experiments/" + e.id + "/run",
      base = { version: 1, dataSource: "demo" },
      reordered = Object.fromEntries(
        Object.entries(f.scopeRef).sort(([a], [b]) => a.localeCompare(b)),
      );
    assert.notEqual(JSON.stringify(reordered), JSON.stringify(f.scopeRef));
    assert.deepEqual(reordered, f.scopeRef);
    const accepted = await x.request(
      path,
      "POST",
      { ...base, universeScopeRef: reordered },
      x.cookie,
    );
    assert.equal(accepted.status, 202, await accepted.clone().text());
    const job = (await accepted.json()).job;
    const jobCount = async () =>
      (await x.db.prepare("SELECT count(*) n FROM jobs").first()).n;
    assert.equal(await jobCount(), 1);
    const invalid = [
      [
        { ...reordered, scopeId: "00000000-0000-0000-0000-000000000000" },
        409,
        "UNIVERSE_RUN_BINDING",
      ],
      [
        { ...reordered, scopeRoot: "f".repeat(64) },
        409,
        "UNIVERSE_RUN_BINDING",
      ],
      [
        { ...reordered, format: "atlas.quant.market_dataset" },
        400,
        "INVALID_SCOPE",
      ],
      [{ ...reordered, version: 2 }, 400, "INVALID_SCOPE"],
      [{ ...reordered, version: "1" }, 400, "INVALID_SCOPE"],
      [{ ...reordered, extra: true }, 400, "INVALID_SCOPE"],
      [{ ...reordered, owner: "foreign-owner" }, 400, "INVALID_SCOPE"],
      [{ ...reordered, scopeId: 1 }, 400, "INVALID_SCOPE"],
      [{ ...reordered, scopeRoot: null }, 400, "INVALID_SCOPE"],
      [
        Object.fromEntries(
          Object.entries(reordered).filter(([key]) => key !== "version"),
        ),
        400,
        "INVALID_SCOPE",
      ],
      [null, 400, "INVALID_STATISTICAL_QUANT"],
      [[], 400, "INVALID_SCOPE"],
    ];
    for (const [universeScopeRef, status, code] of invalid) {
      const rejected = await x.request(
        path,
        "POST",
        { ...base, universeScopeRef },
        x.cookie,
      );
      assert.equal(rejected.status, status, JSON.stringify(universeScopeRef));
      assert.equal(
        (await rejected.json()).error.code,
        code,
        JSON.stringify(universeScopeRef),
      );
      assert.equal(await jobCount(), 1);
    }
    const other = await x.request("/session"),
      otherCookie = other.headers.get("set-cookie").split(";")[0];
    const foreign = await x.request(
      path,
      "POST",
      { ...base, universeScopeRef: reordered },
      otherCookie,
    );
    assert.equal(foreign.status, 404);
    assert.equal(await jobCount(), 1);
    const binding = await x.db
      .prepare("SELECT scope_id,scope_root FROM quant_run_scopes WHERE job_id=?")
      .bind(job.id)
      .first();
    assert.deepEqual(binding, {
      scope_id: f.scopeRef.scopeId,
      scope_root: f.scopeRef.scopeRoot,
    });
  } finally {
    await x.mf.dispose();
  }
});

test("small complete scope survives enqueue and claim with full immutable evidence", async () => {
  const x = await fixture();
  try {
    const chosen = {
      version: 1,
      includeGroups: [],
      includeSymbols: symbols.slice(0, 3),
    };
    const resolved = await (
      await x.request(
        "/universes/resolve",
        "POST",
        { selection: chosen },
        x.cookie,
      )
    ).json();
    const f = await (
      await x.request(
        "/universe-scopes",
        "POST",
        {
          selection: chosen,
          expectedResolutionHash: resolved.resolutionHash,
          expectedSnapshotHash: resolved.snapshotHash,
          start: "20240101",
          end: "20241231",
        },
        x.cookie,
      )
    ).json();
    const e = (
      await (
        await x.request(
          "/statistical-quant/experiments",
          "POST",
          { strategy: strategy(f.scope), universeScopeRef: f.scopeRef },
          x.cookie,
        )
      ).json()
    ).experiment;
    const response = await x.request(
      "/statistical-quant/experiments/" + e.id + "/run",
      "POST",
      { version: 1, dataSource: "demo" },
      x.cookie,
    );
    assert.equal(response.status, 202, await response.clone().text());
    const queued = await response.json();
    await x.db.prepare("DELETE FROM meta WHERE key='universe_registry'").run();
    const claimed = await x.mf.dispatchFetch(
      "https://whole.test/quant/api/runner/claim",
      {
        method: "POST",
        headers: {
          authorization: "Bearer test-only",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          engineVersion: "0.8.0",
          transportFormats: ["atlas.quant.bundle/1"],
        }),
      },
    );
    assert.equal(claimed.status, 200, await claimed.clone().text());
    const { job } = await claimed.json();
    assert.equal(job.id, queued.job.id);
    assert.deepEqual(job.universeScopeRef, f.scopeRef);
    assert.deepEqual(job.universeScope, f.scope);
    assert.deepEqual(job.strategy.universe.symbols, f.scope.symbols);
  } finally {
    await x.mf.dispose();
  }
});
