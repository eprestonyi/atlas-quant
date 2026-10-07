import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { validateManifest } from "../edge/bundles/manifest.mjs";
import { bundleFixture, canonical, hash } from "./fixtures/bundle-fixture.mjs";

const script = await buildWorkerSource({
  wrapper: `export default {async fetch(req,env,ctx){
 const wrapped={...env,ARTIFACTS:{get:async(key)=>{await env.DB.prepare("INSERT INTO meta(key,value,updated_at) VALUES('test_r2_reads','1','test') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1").run();return env.ARTIFACTS.get(key);},put:(...args)=>env.ARTIFACTS.put(...args),delete:(...args)=>env.ARTIFACTS.delete(...args)}};
 return productionWorker.fetch(req,wrapped,ctx);
}};`,
});
const mf = new Miniflare({
  modules: true,
  script,
  compatibilityDate: "2026-08-01",
  d1Databases: ["DB"],
  r2Buckets: ["ARTIFACTS"],
  bindings: { RUNNER_SECRET: "isolated-bundle-test" },
});
const db = await mf.getD1Database("DB"),
  bucket = await mf.getR2Bucket("ARTIFACTS");
await db.exec(
  (
    await fs.readFile(new URL("../edge/schema.sql", import.meta.url), "utf8")
  ).replaceAll("\n", " "),
);
const call = (
  path,
  { method = "GET", data, cookie, runner = false, raw, headers = {} } = {},
) =>
  mf.dispatchFetch("https://atlas.test/quant/api" + path, {
    method,
    headers: {
      ...(data !== undefined || raw !== undefined
        ? { "content-type": "application/json" }
        : {}),
      ...(cookie ? { cookie } : {}),
      ...(runner ? { authorization: "Bearer isolated-bundle-test" } : {}),
      ...headers,
    },
    ...(data !== undefined
      ? { body: JSON.stringify(data) }
      : raw !== undefined
        ? { body: raw }
        : {}),
  });
async function payload(response, status = 200) {
  const value = await response.json();
  assert.equal(response.status, status, JSON.stringify(value));
  return value;
}
async function session() {
  return (await call("/session")).headers.get("set-cookie").split(";")[0];
}
async function queued(fixture) {
  const cookie = await session();
  const created = await payload(
    await call("/runs", {
      method: "POST",
      cookie,
      data: { strategy: fixture.strategy, dataSource: "demo" },
    }),
    202,
  );
  const claim = (
    await payload(
      await call("/runner/claim", {
        method: "POST",
        runner: true,
        data: {
          engineVersion: "0.4.0",
          transportFormats: ["atlas.quant.bundle/1"],
        },
      }),
    )
  ).job;
  assert.equal(claim.id, created.job.id);
  return { cookie, claim };
}
async function begin(fixture, claim) {
  return payload(
    await call("/runner/bundles/begin", {
      method: "POST",
      runner: true,
      data: {
        id: claim.id,
        leaseToken: claim.leaseToken,
        bundleId: fixture.bundleId,
        manifestText: fixture.manifestText,
      },
    }),
  );
}
async function upload(fixture, claim, stage, except = null) {
  for (const [key, raw] of fixture.chunks) {
    if (key === except) continue;
    const [collection, ordinal] = key.split(":");
    await payload(
      await call(
        `/runner/bundles/${fixture.bundleId}/chunks/${collection}/${ordinal}`,
        {
          method: "PUT",
          runner: true,
          raw,
          headers: {
            "X-Quant-Job": claim.id,
            "X-Quant-Lease": claim.leaseToken,
            "X-Quant-Stage": stage.stageId,
          },
        },
      ),
    );
  }
}
const packet = (fixture, claim, stage) => ({
  id: claim.id,
  leaseToken: claim.leaseToken,
  bundleId: fixture.bundleId,
  stageId: stage.stageId,
});
async function finish(fixture, claim, stage) {
  await payload(
    await call("/runner/bundles/finalize", {
      method: "POST",
      runner: true,
      data: packet(fixture, claim, stage),
    }),
  );
  return payload(
    await call("/runner/complete", {
      method: "POST",
      runner: true,
      data: packet(fixture, claim, stage),
    }),
  );
}
test.after(() => mf.dispose());

test("strict manifest rejects false layout, missing collections, sensitive keys and duplicate keys", async () => {
  const f = bundleFixture();
  assert.equal(
    (await validateManifest(f.manifestText, f.bundleId)).rowCount,
    11,
  );
  await assert.rejects(
    validateManifest(
      f.manifestText.replace('"version":1', '"version":1,"version":1'),
    ),
  );
  const m = structuredClone(f.manifest);
  m.collections.find((c) => c.id === "forecasts").path = "/diagnostics/rows";
  await assert.rejects(validateManifest(canonical(m)));
  const secret = bundleFixture({
    mutate: ({ snapshot }) =>
      (snapshot.provenance.token = "SYNTHETIC_FORBIDDEN_KEY"),
  });
  await assert.rejects(validateManifest(secret.manifestText));
  const hidden = structuredClone(f.manifest);
  hidden.documents.coverage.parts[0].literal =
    hidden.documents.coverage.parts[0].literal.replace(
      '"origins":',
      '"hidden":',
    );
  await assert.rejects(validateManifest(canonical(hidden)));
});

let good, owner, claim, stage;
test("actual D1/R2 staged upload verifies hashes, fencing, complete coverage and atomic publication", async () => {
  good = bundleFixture();
  ({ cookie: owner, claim } = await queued(good));
  assert.deepEqual(claim.resultTransport, {
    format: "atlas.quant.bundle",
    version: 1,
  });
  stage = await begin(good, claim);
  assert.equal(stage.missing.length, good.chunks.size);
  const retry = await begin(good, claim);
  assert.equal(retry.stageId, stage.stageId);
  await upload(good, claim, stage, "forecasts:0");
  assert.equal(
    (
      await call("/runner/bundles/finalize", {
        method: "POST",
        runner: true,
        data: packet(good, claim, stage),
      })
    ).status,
    409,
  );
  const raw = good.chunks.get("forecasts:0");
  const url = `/runner/bundles/${good.bundleId}/chunks/forecasts/0`;
  const headers = {
    "X-Quant-Job": claim.id,
    "X-Quant-Lease": claim.leaseToken,
    "X-Quant-Stage": stage.stageId,
  };
  assert.equal(
    (await call(url, { method: "PUT", runner: true, raw: raw + " ", headers }))
      .status,
    400,
  );
  assert.equal(
    (
      await call(url, {
        method: "PUT",
        runner: true,
        raw,
        headers: { ...headers, "X-Quant-Lease": "wrong" },
      })
    ).status,
    409,
  );
  await payload(await call(url, { method: "PUT", runner: true, raw, headers }));
  assert.equal(
    (
      await payload(
        await call(url, { method: "PUT", runner: true, raw, headers }),
      )
    ).idempotent,
    true,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT status FROM jobs WHERE id=?")
        .bind(claim.id)
        .first()
    ).status,
    "running",
  );
  await finish(good, claim, stage);
  assert.equal(
    (
      await payload(
        await call("/runner/complete", {
          method: "POST",
          runner: true,
          data: packet(good, claim, stage),
        }),
      )
    ).idempotent,
    true,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT count(*) n FROM quant_bundle_runs WHERE job_id=?")
        .bind(claim.id)
        .first()
    ).n,
    1,
  );
});

test("summary, exact indexed pages, isolated detail and canonical streamed downloads", async () => {
  const base = `/runs/${claim.id}/report`,
    summary = await payload(await call(base, { cookie: owner }));
  assert.equal(summary.report.forecasts.rows, undefined);
  assert.equal(summary.report.equity, undefined);
  assert.equal(summary.transport.collections.forecasts.total, 4);
  assert.equal(
    (
      await call(base + "/pages?collection=forecasts&limit=1", {
        cookie: owner,
      })
    ).status,
    409,
  );
  const params = `bundleId=${good.bundleId}`;
  await db.prepare("UPDATE meta SET value='0' WHERE key='test_r2_reads'").run();
  const page = await payload(
    await call(base + `/pages?${params}&collection=forecasts&limit=1`, {
      cookie: owner,
    }),
  );
  assert.equal(page.items[0].forecastId, "f3");
  assert.equal(page.total, 4);
  assert.equal(page.nextOffset, 1);
  assert.equal(page.related.targets[0].id, "t");
  assert.equal(page.related.modelFits[0].trainStart, "20230101");
  assert.equal(
    Number(
      (
        await db
          .prepare("SELECT value FROM meta WHERE key='test_r2_reads'")
          .first()
      ).value,
    ),
    1,
  );
  const filtered = await payload(
    await call(
      base +
        `/pages?${params}&collection=forecasts&dateFrom=20250103&dateTo=20250104`,
      { cookie: owner },
    ),
  );
  assert.equal(filtered.total, 2);
  const latest = await payload(
    await call(base + `/pages?${params}&collection=forecasts&scope=latest`, {
      cookie: owner,
    }),
  );
  assert.equal(latest.items[0].forecastId, "f3");
  assert.equal(latest.total, 1);
  assert.equal(
    (
      await call(base + `/pages?${params}&collection=snapshotRows`, {
        cookie: owner,
      })
    ).status,
    404,
  );
  assert.equal(
    (await call(base + `/pages?${params}&q=unindexed`, { cookie: owner }))
      .status,
    400,
  );
  assert.equal((await call(base, { cookie: await session() })).status, 404);
  assert.equal(
    (
      await payload(
        await call(base + `/detail?${params}&collection=forecasts&id=f2`, {
          cookie: owner,
        }),
      )
    ).item.forecastId,
    "f2",
  );
  const download = await call(base + `/download?${params}`, { cookie: owner });
  assert.equal(
    hash(await download.text()),
    good.manifest.documents.report.sha256,
  );
  const forecast = await payload(
    await call(
      `/statistical-quant/forecasts/${good.manifest.forecastArtifactId}/download`,
      { cookie: owner },
    ),
  );
  assert.equal(forecast.artifact.artifactId, good.manifest.forecastArtifactId);
  assert.equal(forecast.artifact.rows.length, 4);
});

test("bundle execution requires advertised capability and source-bound replay under current lease", async () => {
  const execute = await payload(
    await call("/statistical-quant/executions", {
      method: "POST",
      cookie: owner,
      data: { forecastArtifactId: good.manifest.forecastArtifactId },
    }),
    202,
  );
  assert.equal(
    (
      await payload(
        await call("/runner/claim", {
          method: "POST",
          runner: true,
          data: { engineVersion: "0.4.0" },
        }),
      )
    ).job,
    null,
  );
  const execution = (
    await payload(
      await call("/runner/claim", {
        method: "POST",
        runner: true,
        data: {
          engineVersion: "0.4.0",
          transportFormats: ["atlas.quant.bundle/1"],
        },
      }),
    )
  ).job;
  assert.equal(execution.id, execute.job.id);
  assert.equal(execution.sourceTransport.bundleId, good.bundleId);
  const replay = await payload(
    await call("/runner/replay", {
      method: "POST",
      runner: true,
      data: {
        id: execution.id,
        leaseToken: execution.leaseToken,
        kind: "bundle",
      },
    }),
  );
  assert.equal(replay.manifestText, good.manifestText);
  const raw = await call(
    `/runner/bundles/${good.bundleId}/chunks/snapshotRows/0`,
    {
      runner: true,
      headers: {
        "X-Quant-Job": execution.id,
        "X-Quant-Lease": execution.leaseToken,
      },
    },
  );
  assert.equal(
    hash(await raw.text()),
    good.manifest.collections.find((c) => c.id === "snapshotRows").chunks[0]
      .sha256,
  );
  assert.equal(
    (
      await call(`/runner/bundles/${good.bundleId}/chunks/snapshotRows/0`, {
        runner: true,
        headers: { "X-Quant-Job": claim.id, "X-Quant-Lease": claim.leaseToken },
      })
    ).status,
    200,
  );
  await call(`/runs/${execution.id}/cancel`, {
    method: "POST",
    cookie: owner,
    data: {},
  });
});

test("missing invalid tail and dangling model references cannot finalize despite self-consistent hashes", async () => {
  const f = bundleFixture({
    mutate: ({ forecast }) => (forecast.rows[0].modelFitId = "missing"),
  });
  const q = await queued(f),
    s = await begin(f, q.claim);
  await upload(f, q.claim, s);
  const response = await call("/runner/bundles/finalize", {
    method: "POST",
    runner: true,
    data: packet(f, q.claim, s),
  });
  assert.equal(response.status, 409);
  assert.equal(
    (
      await db
        .prepare("SELECT count(*) n FROM quant_bundle_runs WHERE job_id=?")
        .bind(q.claim.id)
        .first()
    ).n,
    0,
  );
  await call(`/runs/${q.claim.id}/cancel`, {
    method: "POST",
    cookie: q.cookie,
    data: {},
  });
  const discard = await payload(
    await call("/runner/complete", {
      method: "POST",
      runner: true,
      data: packet(f, q.claim, s),
    }),
  );
  assert.equal(discard.terminalDiscard, true);
});

test("actual committed chunk corruption is rejected before it can appear on a new page", async () => {
  const row = await db
    .prepare(
      "SELECT object_key FROM quant_bundle_chunks WHERE stage_id=? AND collection='forecasts' AND ordinal=0",
    )
    .bind(stage.stageId)
    .first();
  await bucket.put(row.object_key, "[]");
  const response = await call(
    `/runs/${claim.id}/report/pages?bundleId=${good.bundleId}&collection=forecasts&id=f0`,
    { cookie: owner },
  );
  assert.equal(response.status, 503);
});

test("a page reads at most eight required chunks and never loses the remaining records", async () => {
  const f = bundleFixture({ count: 10, rowsPerChunk: 1 });
  const q = await queued(f),
    s = await begin(f, q.claim);
  await upload(f, q.claim, s);
  await finish(f, q.claim, s);
  await db.prepare("UPDATE meta SET value='0' WHERE key='test_r2_reads'").run();
  const page = await payload(
    await call(
      `/runs/${q.claim.id}/report/pages?collection=forecasts&limit=100&bundleId=${f.bundleId}`,
      { cookie: q.cookie },
    ),
  );
  assert.equal(page.items.length, 8);
  assert.equal(page.nextOffset, 8);
  assert.equal(page.total, 10);
  assert.equal(
    Number(
      (
        await db
          .prepare("SELECT value FROM meta WHERE key='test_r2_reads'")
          .first()
      ).value,
    ),
    8,
  );
  const last = await payload(
    await call(
      `/runs/${q.claim.id}/report/pages?collection=forecasts&limit=100&offset=8&bundleId=${f.bundleId}`,
      { cookie: q.cookie },
    ),
  );
  assert.equal(last.items.length, 2);
  assert.equal(last.hasMore, false);
  assert.equal(
    new Set([...page.items, ...last.items].map((r) => r.forecastId)).size,
    10,
  );
});

test("declared invalid tail rows remain complete and unavailable fit cannot masquerade as a valid forecast", async () => {
  const f = bundleFixture({
    mutate: ({ forecast, coverage }) => {
      const last = forecast.rows.at(-1);
      Object.assign(last, {
        status: "invalid",
        invalidReason: "tail_without_mature_label",
        targetDate: null,
        labelMaturedAt: null,
      });
      coverage.origins.at(-1).targetDate = null;
    },
  });
  const q = await queued(f),
    s = await begin(f, q.claim);
  await upload(f, q.claim, s);
  await finish(f, q.claim, s);
  const invalid = await payload(
    await call(
      `/runs/${q.claim.id}/report/pages?collection=forecasts&status=invalid&bundleId=${f.bundleId}`,
      { cookie: q.cookie },
    ),
  );
  assert.equal(invalid.total, 1);
  assert.equal(invalid.items[0].forecastId, "f3");
  const bad = bundleFixture({
    mutate: ({ forecast }) => (forecast.modelFits[0].status = "invalid"),
  });
  const b = await queued(bad),
    bs = await begin(bad, b.claim);
  await upload(bad, b.claim, bs);
  assert.equal(
    (
      await call("/runner/bundles/finalize", {
        method: "POST",
        runner: true,
        data: packet(bad, b.claim, bs),
      })
    ).status,
    409,
  );
  await call(`/runs/${b.claim.id}/cancel`, {
    method: "POST",
    cookie: b.cookie,
    data: {},
  });
});

test("retention deletes only abandoned old terminal stages and preserves committed research", async () => {
  const { cleanupAbandonedBundles } = await import(
    "../edge/bundles/retention.mjs"
  );
  const f = bundleFixture(),
    q = await queued(f),
    s = await begin(f, q.claim);
  await upload(f, q.claim, s);
  await call(`/runs/${q.claim.id}/cancel`, {
    method: "POST",
    cookie: q.cookie,
    data: {},
  });
  await db
    .prepare(
      "UPDATE jobs SET updated_at='2000-01-01T00:00:00Z' WHERE id IN (?,?)",
    )
    .bind(q.claim.id, claim.id)
    .run();
  await db
    .prepare(
      "UPDATE quant_bundle_stages SET updated_at='2000-01-01T00:00:00Z' WHERE id IN (?,?)",
    )
    .bind(s.stageId, stage.stageId)
    .run();
  const key = (
    await db
      .prepare(
        "SELECT object_key FROM quant_bundle_chunks WHERE stage_id=? LIMIT 1",
      )
      .bind(s.stageId)
      .first()
  ).object_key;
  assert.ok(await bucket.get(key));
  await cleanupAbandonedBundles(
    { DB: db, ARTIFACTS: bucket },
    "2001-01-01T00:00:00Z",
  );
  assert.equal(await bucket.get(key), null);
  assert.equal(
    await db
      .prepare("SELECT id FROM quant_bundle_stages WHERE id=?")
      .bind(s.stageId)
      .first(),
    null,
  );
  assert.ok(
    await db
      .prepare(
        "SELECT id FROM quant_bundle_stages WHERE id=? AND status='committed'",
      )
      .bind(stage.stageId)
      .first(),
  );
});

test("retention removes R2 writes whose D1 receipt transaction failed without touching other stages", async () => {
  const { cleanupAbandonedBundles } = await import(
    "../edge/bundles/retention.mjs"
  );
  const f = bundleFixture(),
    q = await queued(f),
    s = await begin(f, q.claim);
  const chunk = f.manifest.collections.find((c) => c.id === "forecasts")
    .chunks[0];
  const ownerRow = await db
    .prepare("SELECT owner FROM jobs WHERE id=?")
    .bind(q.claim.id)
    .first();
  const orphanKey = `bundle/${ownerRow.owner}/${s.stageId}/forecasts/0-${chunk.sha256}.json`;
  const protectedKey = `bundle/${ownerRow.owner}/different-stage/forecasts/0-${chunk.sha256}.json`;
  await bucket.put(protectedKey, "unrelated object");
  await db.exec(
    "CREATE TRIGGER reject_bundle_receipt BEFORE INSERT ON quant_bundle_chunks BEGIN SELECT RAISE(ABORT,'isolated receipt failure'); END",
  );
  try {
    const response = await call(
      `/runner/bundles/${f.bundleId}/chunks/forecasts/0`,
      {
        method: "PUT",
        runner: true,
        raw: f.chunks.get("forecasts:0"),
        headers: {
          "X-Quant-Job": q.claim.id,
          "X-Quant-Lease": q.claim.leaseToken,
          "X-Quant-Stage": s.stageId,
        },
      },
    );
    assert.equal(response.status, 500);
  } finally {
    await db.exec("DROP TRIGGER reject_bundle_receipt");
  }
  assert.ok(await bucket.get(orphanKey));
  assert.equal(
    (
      await db
        .prepare("SELECT count(*) n FROM quant_bundle_chunks WHERE stage_id=?")
        .bind(s.stageId)
        .first()
    ).n,
    0,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT count(*) n FROM quant_bundle_records WHERE stage_id=?")
        .bind(s.stageId)
        .first()
    ).n,
    0,
  );
  await call(`/runs/${q.claim.id}/cancel`, {
    method: "POST",
    cookie: q.cookie,
    data: {},
  });
  await db
    .prepare("UPDATE jobs SET updated_at='2000-01-01T00:00:00Z' WHERE id=?")
    .bind(q.claim.id)
    .run();
  await db
    .prepare(
      "UPDATE quant_bundle_stages SET updated_at='2000-01-01T00:00:00Z' WHERE id=?",
    )
    .bind(s.stageId)
    .run();
  await cleanupAbandonedBundles(
    { DB: db, ARTIFACTS: bucket },
    "2001-01-01T00:00:00Z",
  );
  assert.equal(await bucket.get(orphanKey), null);
  assert.ok(await bucket.get(protectedKey));
  assert.equal(
    await db
      .prepare("SELECT id FROM quant_bundle_stages WHERE id=?")
      .bind(s.stageId)
      .first(),
    null,
  );
});
