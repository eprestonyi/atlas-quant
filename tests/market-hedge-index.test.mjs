/** Real D1/R2 transport tests with SYNTHETIC rows; no provider or fitted model. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { marketOriginDomain } from "../edge/market-preparation/coverage.mjs";
import { recordIndex } from "../edge/bundles/records.mjs";
import { canonical, hash } from "./fixtures/bundle-fixture.mjs";

const symbols = Array.from(
  { length: 1000 },
  (_, i) => String(600000 + i) + ".SH",
);
const calendar = [];
for (
  let d = new Date("2024-01-01T00:00:00Z");
  calendar.length < 262;
  d.setUTCDate(d.getUTCDate() + 1)
) {
  if (![0, 6].includes(d.getUTCDay()))
    calendar.push(d.toISOString().slice(0, 10).replaceAll("-", ""));
}
const strategy = {
  factors: [{ expression: "returns(close,20)", role: "predictor" }],
  research: { observationDays: 1 },
  model: { refitDays: 20 },
  validation: { holdoutFraction: 0.2 },
  target: { horizonSessions: 5 },
};
const domain = await marketOriginDomain(strategy, calendar, symbols);
const rows = domain.hedgeFits.map((clock) => ({
  ...clock,
  targetIds: domain.targets.map((t) => t.id),
  status: "valid",
}));
const schema = await fs.readFile(
  new URL("../edge/schema.sql", import.meta.url),
  "utf8",
);
const script = await buildWorkerSource({
  wrapper: `
import {recordIndex,indexStatements} from './edge/bundles/records.mjs';
import {verifyMarketHedgeFits} from './edge/market-preparation/coverage.mjs';
import {verifyRecords} from './edge/bundles/verify.mjs';
import {sha} from './edge/runtime.mjs';
import {canonical} from './edge/market-preparation/common.mjs';
export default {async fetch(req,env) {
 try {
  const p=await req.json(), id=crypto.randomUUID(), lease=crypto.randomUUID(), now=new Date().toISOString();
  const stage={id,metadata:'{}'};
  await env.DB.prepare('INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)').bind(id,'synthetic','fixture','running','ready_market','{}',lease,new Date(Date.now()+120000).toISOString(),now,now).run();
  await env.DB.prepare('INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(id,'synthetic',id,lease,'a'.repeat(64),'{}','fixture','{}','staging',now,now).run();
  const indexes=[];
  for(let i=0;i<p.rows.length;i++) indexes.push(await recordIndex('hedgeFits',p.rows[i],i,0,i,{marketHedgeTargets:p.domain.targets.map(t=>t.id)}));
  await env.DB.batch(indexStatements(env,stage,'hedgeFits',indexes));
  const raw=canonical(p.rawRows??p.rows), descriptor={ordinal:0,start:0,count:(p.rawRows??p.rows).length,byteLength:new TextEncoder().encode(raw).length,sha256:await sha(raw)};
  await env.ARTIFACTS.put('raw',raw);
  if(p.tamperIndex) await env.DB.prepare("UPDATE quant_bundle_records SET metadata=? WHERE stage_id=? AND ordinal=0").bind(JSON.stringify(p.tamperIndex),id).run();
  const parsed={collections:new Map([['hedgeFits',{id:'hedgeFits',rowCount:descriptor.count,chunks:[descriptor]}]]),metadata:{coverage:{baselineRequired:false}}};
  if(p.legacy) await verifyRecords(env,stage,parsed);
  await verifyMarketHedgeFits(env,stage,parsed,p.domain,async()=>new Uint8Array(await (await env.ARTIFACTS.get('raw')).arrayBuffer()));
  return Response.json({ok:true,rawBytes:descriptor.byteLength,indexBytes:Math.max(...indexes.map(x=>new TextEncoder().encode(x[12]).length)),index:JSON.parse(indexes[0][12]),rawSha256:await sha(await (await env.ARTIFACTS.get('raw')).text())});
 }catch(e){return Response.json({code:e.code,message:e.message},{status:409});}
}};`,
});
async function run(extra = {}) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
    r2Buckets: ["ARTIFACTS"],
  });
  try {
    await (await mf.getD1Database("DB")).exec(schema.replaceAll("\n", " "));
    const response = await mf.dispatchFetch("https://fixture.test/", {
      method: "POST",
      body: JSON.stringify({ domain, rows, ...extra }),
    });
    return { status: response.status, ...(await response.json()) };
  } finally {
    await mf.dispose();
  }
}

test("all 1000 source targets × 11 construction dates preserve complete raw rows with bounded indexes", async () => {
  const result = await run();
  assert.equal(result.status, 200, JSON.stringify(result));
  assert.equal(rows.length, 11);
  assert.equal(result.index.targetCount, 1000);
  assert.equal(result.index.targetIndexPolicy, "source_asset_targets_v1");
  assert.equal(result.index.targetIdsRoot, hash(canonical(rows[0].targetIds)));
  assert.ok(result.rawBytes > 350000);
  assert.ok(result.indexBytes < 256);
  assert.equal(result.rawSha256, hash(canonical(rows)));
});

test("legacy indexing retains 50-reference maximum and cannot opt in using row metadata", async () => {
  await recordIndex(
    "hedgeFits",
    { date: calendar[61], targetIds: rows[0].targetIds.slice(0, 50) },
    0,
    0,
    0,
  );
  for (const n of [51, 1000])
    await assert.rejects(
      recordIndex(
        "hedgeFits",
        {
          date: calendar[61],
          targetIds: rows[0].targetIds.slice(0, n),
          targetIndexPolicy: "source_asset_targets_v1",
          _marketAdmission: true,
        },
        0,
        0,
        0,
      ),
      (e) => e.code === "BUNDLE_RECORD",
    );
  const r = await run({ legacy: true });
  assert.equal(r.code, "BUNDLE_INCOMPLETE");
});

for (const variant of [
  "missing",
  "duplicate",
  "foreign",
  "reordered",
  "malicious",
  "extra-key",
]) {
  test(
    "compact index rejects " +
      variant +
      " target references before persistence",
    async () => {
      const changed = structuredClone(rows);
      if (variant === "missing") changed[0].targetIds.pop();
      if (variant === "duplicate")
        changed[0].targetIds[999] = changed[0].targetIds[0];
      if (variant === "foreign")
        changed[0].targetIds[999] = "target_" + "a".repeat(24);
      if (variant === "reordered") changed[0].targetIds.reverse();
      if (variant === "extra-key") changed[0].unexpected = true;
      if (variant === "malicious")
        changed[0].targetIds[0] = { id: changed[0].targetIds[0] };
      assert.equal(
        (await run({ rows: changed })).code,
        "MARKET_HEDGE_REFERENCES",
      );
    },
  );
}
for (const variant of [
  "raw-half-pool",
  "raw-extra",
  "missing-fit",
  "duplicate-fit",
  "future-cutoff",
  "bad-status",
  "forged-index",
]) {
  test(
    "finalize independently rejects " +
      variant +
      " despite rehashed raw chunks",
    async () => {
      const rawRows = structuredClone(rows),
        extra = {};
      if (variant === "raw-half-pool")
        rawRows[0].targetIds = rawRows[0].targetIds.slice(0, 500);
      if (variant === "raw-extra")
        rawRows[0].targetIds.push("target_" + "a".repeat(24));
      if (variant === "missing-fit") rawRows.pop();
      if (variant === "duplicate-fit") rawRows[1] = rawRows[0];
      if (variant === "future-cutoff")
        rawRows[0].informationCutoff = rawRows[0].date;
      if (variant === "bad-status") rawRows[0].status = "invalid";
      if (variant === "forged-index")
        extra.tamperIndex = {
          targetIndexPolicy: "source_asset_targets_v1",
          targetCount: 1000,
          targetIdsRoot: "0".repeat(64),
        };
      const result = await run({ rawRows, ...extra });
      assert.equal(result.status, 409, JSON.stringify(result));
      assert.ok(
        ["MARKET_HEDGE_REFERENCES", "MARKET_FORECAST_COVERAGE"].includes(
          result.code,
        ),
        JSON.stringify(result),
      );
    },
  );
}
