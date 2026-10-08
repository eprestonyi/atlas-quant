/** Independent transport review: synthetic source and records, no provider or F. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { bundleFixture, canonical, hash } from "./fixtures/bundle-fixture.mjs";
import { beginBundle, uploadChunk } from "../edge/bundles/storage.mjs";
import { validateCapacity } from "../edge/market-preparation/research.mjs";
import { bindWholeScope } from "../edge/market-preparation/scope.mjs";

const generated = spawnSync(
  process.env.PYTHON || ".venv/bin/python",
  ["tests/helpers/market-fixture.py"],
  { encoding: "utf8", env: { ...process.env, PYTHONPATH: "engine" }, maxBuffer: 2 * 1024 ** 2 },
);
assert.equal(generated.status, 0, generated.stderr);
const source = JSON.parse(generated.stdout);
const scope = JSON.parse(await fs.readFile(new URL("../contracts/fixtures/market-scope-v1.json", import.meta.url), "utf8"));
const schema = await fs.readFile(new URL("../edge/schema.sql", import.meta.url), "utf8");
const script = await buildWorkerSource({ wrapper: `
import { validateMarketBundle, verifyMarketSnapshot } from './edge/market-preparation/bundle.mjs';
export default {async fetch(req) {
  const input=await req.json();
  try {
    const parsed=await validateMarketBundle(input.manifestText,input.bundleId);
    await verifyMarketSnapshot({metadata:JSON.stringify({_marketAdmission:input.admission})},parsed,
      async(name,descriptor)=>input.chunks[name+':'+descriptor.ordinal]);
    return Response.json({ok:true});
  } catch(error) { return Response.json({code:error.code},{status:error.status||409}); }
}};` });

async function setup() {
  const mf = new Miniflare({ modules: true, script, compatibilityDate: "2026-08-01", d1Databases: ["DB"], r2Buckets: ["ARTIFACTS"] });
  const db = await mf.getD1Database("DB"), bucket = await mf.getR2Bucket("ARTIFACTS");
  await db.exec(schema.replaceAll("\n", " "));
  const env = {
    DB: db, ARTIFACTS: bucket, MARKET_RESEARCH_ENABLED: "true",
    MARKET_ACQUISITION_AUTH_SCOPE: source.plan.authorizationScope,
    BUNDLE_SNAPSHOT_SORTED_V1: "true",
  };
  const owner = randomUUID(), pid = randomUUID(), sourceJob = randomUUID(), dataset = randomUUID();
  const jid = randomUUID(), lease = randomUUID(), now = new Date().toISOString();
  const datasetRoot = hash(canonical(source.manifest));
  const sourceRows = JSON.parse(source.chunks.rows[0]);
  const rowValueRoot = hash(sourceRows.map((r) => canonical(r) + "\n").join(""));
  const admission = {
    marketDatasetRef: { datasetId: dataset, datasetRoot, format: "atlas.quant.market_dataset", version: 1 },
    universeScopeRef: source.plan.universeScopeRef,
    admissionProfile: "pooled_asset_1000_v1", rowValueRoot,
  };
  const strategy = validateCapacity(bindWholeScope({
    schemaVersion: 2, name: "SYNTHETIC security review; no fitted model",
    research: { mode: "statistical_quant" },
    universe: { symbols: scope.symbols, start: scope.start, end: scope.end },
    target: { kind: "asset_price", horizonSessions: 5 },
    model: { family: "mean_reversion", estimator: "ridge", trainWindow: 120, refitDays: 20 },
    validation: { innerFolds: 2, outerFolds: 2, minTrainDates: 40 },
    execution: { enabled: false }, factors: [],
  }, scope), admission.admissionProfile);
  const f = bundleFixture({ count: 2, rowsPerChunk: 1, mutate({ forecast, report, snapshot }) {
    forecast.sourceStrategy = strategy;
    report.strategy = strategy;
    report.provenance = { ...JSON.parse(source.chunks.provenance[0])[0], marketSource: admission };
    snapshot.provenance = structuredClone(report.provenance);
    snapshot.rows = sourceRows;
  } });
  // Seed only server-owned committed source/binding state. No source acquisition,
  // fit, actual external server, or user-supplied stage metadata is involved.
  await db.batch([
    db.prepare("INSERT INTO quant_universe_scopes VALUES(?,?,?,?,?)").bind(scopeRefId(), owner, source.plan.universeScopeRef.scopeRoot, canonical(scope), now),
    db.prepare("INSERT INTO quant_market_plans VALUES(?,?,?,?,?,?,?,?)").bind(pid, owner, scopeRefId(), source.plan.universeScopeRef.scopeRoot, source.plan.planRoot, source.plan.profile, canonical(source.plan), now),
    db.prepare("INSERT INTO quant_market_jobs(id,owner,request_id,plan_id,plan_root,authorization_scope,status,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,'completed','completed',?,?)").bind(sourceJob, owner, randomUUID(), pid, source.plan.planRoot, source.plan.authorizationScope, now, now),
    db.prepare("INSERT INTO quant_market_publications VALUES(?,?,?,?,?,'committed',?,?)").bind(sourceJob, owner, randomUUID(), canonical(source.manifest), datasetRoot, now, now),
    db.prepare("INSERT INTO quant_market_datasets VALUES(?,?,?,?,?,?,?,?,?,'ready',?)").bind(dataset, owner, sourceJob, datasetRoot, source.plan.planRoot, source.plan.universeScopeRef.scopeRoot, source.plan.profile, canonical(source.manifest), rowValueRoot, now),
    db.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,'running','ready_market',?,?,?,?,?)").bind(jid, owner, strategy.name, canonical(strategy), lease, new Date(Date.now() + 600000).toISOString(), now, now),
    db.prepare("INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,created_at) VALUES(?,?,?,1,'forecast',?)").bind(jid, owner, randomUUID(), now),
    db.prepare("INSERT INTO quant_run_scopes VALUES(?,?,?,?,?)").bind(jid, owner, scopeRefId(), source.plan.universeScopeRef.scopeRoot, now),
    db.prepare("INSERT INTO quant_run_market_datasets VALUES(?,?,?,?,?,?)").bind(jid, owner, dataset, datasetRoot, admission.admissionProfile, now),
  ]);
  const identity = { id: jid, leaseToken: lease, bundleId: f.bundleId };
  const stage = await beginBundle(env, { ...identity, manifestText: f.manifestText });
  return { mf, db, bucket, env, f, admission, identity: { ...identity, stageId: stage.stageId } };
}
function scopeRefId() { return source.plan.universeScopeRef.scopeId; }
async function put(x, key, env = x.env) {
  const [name, ordinal] = key.split(":");
  return uploadChunk(env, x.identity, name, +ordinal, x.f.chunks.get(key));
}
async function footprint(x) {
  const rows = {};
  for (const table of ["quant_bundle_chunks", "quant_bundle_records"])
    rows[table] = (await x.db.prepare(`SELECT count(*) n FROM ${table} WHERE stage_id=?`).bind(x.identity.stageId).first()).n;
  return { ...rows, objects: (await x.bucket.list()).objects.map((o) => o.key).sort() };
}

test("flag-off retains identical chunk ACK but rejects every new market chunk before any R2/index write", async () => {
  const x = await setup();
  try {
    assert.equal((await put(x, "forecasts:0")).idempotent, false);
    const before = await footprint(x), off = { ...x.env, MARKET_RESEARCH_ENABLED: "false" };
    assert.equal((await put(x, "forecasts:0", off)).idempotent, true);
    await assert.rejects(put(x, "forecasts:1", off), (e) => e.code === "MARKET_RESEARCH_DISABLED");
    assert.deepEqual(await footprint(x), before);
  } finally { await x.mf.dispose(); }
});

test("changed persisted scope cannot receive a new market chunk, while original ACK stays readable", async () => {
  const x = await setup();
  try {
    await put(x, "forecasts:0");
    const before = await footprint(x);
    await x.db.prepare("UPDATE quant_run_scopes SET scope_root=? WHERE job_id=?").bind("0".repeat(64), x.identity.id).run();
    assert.equal((await put(x, "forecasts:0")).idempotent, true);
    await assert.rejects(put(x, "forecasts:1"), (e) => ["NOT_FOUND", "MARKET_RUN_BINDING", "MARKET_BUNDLE_ADMISSION"].includes(e.code));
    assert.deepEqual(await footprint(x), before);
  } finally { await x.mf.dispose(); }
});

test("another valid ready dataset with identical content cannot replace the stage's admitted dataset identity", async () => {
  const x = await setup();
  try {
    await put(x, "forecasts:0");
    const before = await footprint(x), sourceJob = randomUUID(), dataset = randomUUID();
    const original = await x.db.prepare("SELECT * FROM quant_market_datasets WHERE id=?")
      .bind(x.admission.marketDatasetRef.datasetId).first();
    await x.db.batch([
      x.db.prepare("INSERT INTO quant_market_jobs(id,owner,request_id,plan_id,plan_root,authorization_scope,status,phase,created_at,updated_at) SELECT ?,owner,?,plan_id,plan_root,authorization_scope,status,phase,created_at,updated_at FROM quant_market_jobs WHERE id=?")
        .bind(sourceJob, randomUUID(), original.job_id),
      x.db.prepare("INSERT INTO quant_market_publications SELECT ?,owner,lease_token,manifest,manifest_hash,status,created_at,updated_at FROM quant_market_publications WHERE job_id=?")
        .bind(sourceJob, original.job_id),
      x.db.prepare("INSERT INTO quant_market_datasets SELECT ?,owner,?,dataset_root,plan_root,scope_root,profile,manifest,row_value_root,status,created_at FROM quant_market_datasets WHERE id=?")
        .bind(dataset, sourceJob, original.id),
      x.db.prepare("UPDATE quant_run_market_datasets SET dataset_id=? WHERE job_id=?")
        .bind(dataset, x.identity.id),
    ]);
    assert.equal((await put(x, "forecasts:0")).idempotent, true);
    await assert.rejects(put(x, "forecasts:1"), (e) => e.code === "MARKET_BUNDLE_ADMISSION");
    assert.deepEqual(await footprint(x), before);
  } finally { await x.mf.dispose(); }
});

test("server row-value root covers all chunks, order, values, nulls and exact columns despite rehashed runner manifests", async () => {
  const x = await setup();
  try {
    const original = [
      { ts_code: "000001.SZ", trade_date: "20250102", close: 10, value: null },
      { ts_code: "000001.SZ", trade_date: "20250103", close: 11, value: 0 },
      { ts_code: "000001.SZ", trade_date: "20250106", close: 12, value: 1 },
    ];
    const admission = { ...x.admission, rowValueRoot: hash(original.map((r) => canonical(r) + "\n").join("")) };
    for (const variant of ["exact", "last-value", "null", "omit", "order", "extra-column"]) {
      const f = bundleFixture({ count: 1, rowsPerChunk: 1, mutate({ snapshot }) {
        snapshot.rows = structuredClone(original);
        if (variant === "last-value") snapshot.rows[2].close += 1;
        if (variant === "null") snapshot.rows[0].value = 0;
        if (variant === "omit") snapshot.rows.pop();
        if (variant === "order") snapshot.rows.reverse();
        if (variant === "extra-column") snapshot.rows[1].unobserved = 1;
      } });
      const response = await x.mf.dispatchFetch("https://review.invalid/snapshot", { method: "POST", body: JSON.stringify({ manifestText: f.manifestText, bundleId: f.bundleId, admission, chunks: Object.fromEntries(f.chunks) }) });
      const result = await response.json();
      if (variant === "exact") assert.deepEqual(result, { ok: true });
      else assert.equal(result.code, "MARKET_SNAPSHOT_SOURCE", variant);
    }
  } finally { await x.mf.dispose(); }
});

test("ordinary job cannot select expanded numerical gate through profile, report, or top-level upload metadata", async () => {
  const x = await setup();
  try {
    await x.db.prepare("UPDATE jobs SET data_source='demo' WHERE id=?").bind(x.identity.id).run();
    const f = bundleFixture({ count: 1, mutate({ report }) {
      report._marketAdmission = x.admission;
      report.admissionProfile = x.admission.admissionProfile;
      report.provenance.marketSource = x.admission;
    } });
    const m = structuredClone(f.manifest);
    for (const c of m.collections) {
      if (!["forecasts", "plannedOrigins"].includes(c.id)) continue;
      const descriptor = c.chunks[0];
      c.rowCount = 25001;
      c.chunks = [10000, 10000, 5001].map((count, ordinal) => ({ ...descriptor, count, ordinal, start: ordinal * 10000 }));
    }
    for (const part of m.documents.forecast.parts)
      if (part.literal) part.literal = part.literal.replace('"totalRows":1', '"totalRows":25001');
    const chunks = m.collections.flatMap((c) => c.chunks);
    m.totals = { chunkCount: chunks.length, chunkBytes: chunks.reduce((n, c) => n + c.byteLength, 0) };
    const text = canonical(m), before = await footprint(x);
    await assert.rejects(beginBundle(x.env, { ...x.identity, bundleId: hash(text), manifestText: text, _marketAdmission: x.admission, admissionProfile: x.admission.admissionProfile }), (e) => e.code === "BUNDLE_BUDGET");
    assert.deepEqual(await footprint(x), before);
  } finally { await x.mf.dispose(); }
});
