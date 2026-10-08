/** Source-derived full-domain checks; explicit synthetic transport, no fitting. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";
import { bundleFixture } from "./fixtures/bundle-fixture.mjs";
import { marketOriginDomain } from "../edge/market-preparation/coverage.mjs";

const calendar = [];
for (
  let day = new Date("2024-01-01T00:00:00Z");
  calendar.length < 201;
  day.setUTCDate(day.getUTCDate() + 1)
) {
  if (![0, 6].includes(day.getUTCDay()))
    calendar.push(day.toISOString().slice(0, 10).replaceAll("-", ""));
}
const symbols = ["600000.SH", "000001.SZ"]; // Source order is not output asset order.
async function makeFixture({
  observationDays = 1,
  expression = "returns(close,20)",
  predictors = true,
  mutate = null,
} = {}) {
  const strategy = {
    schemaVersion: 2,
    name: "SYNTHETIC complete-asset transport, no fitted model",
    universe: { symbols, start: calendar[0], end: calendar.at(-1) },
    research: { mode: "statistical_quant", observationDays },
    target: { kind: "asset_price", horizonSessions: 5 },
    validation: { holdoutFraction: 0.2 },
    factors: predictors ? [{ id: "f", expression, role: "predictor" }] : [],
  };
  const domain = await marketOriginDomain(strategy, calendar, symbols);
  const fixture = bundleFixture({
    count: 1,
    rowsPerChunk: 1000,
    mutate(values) {
      const { forecast, report, coverage, snapshot } = values;
      forecast.sourceStrategy = report.strategy = strategy;
      forecast.targetDefinitions = structuredClone(domain.targets);
      forecast.modelFits = [];
      forecast.rows = domain.origins.flatMap((origin, i) =>
        domain.targets.map((target, j) => ({
          ...origin,
          forecastId: `f${i}_${j}`,
          targetId: target.id,
          modelFitId: null,
          informationCutoff: origin.date,
          horizonSessions: 5,
          status: "invalid",
          invalidReason: "SYNTHETIC_no_fit_performed",
          labelMaturedAt: null,
        })),
      );
      forecast.totalRows = forecast.rows.length;
      forecast.diagnostics = {
        holdoutStart: domain.holdoutStart,
        holdoutEnd: domain.holdoutEnd,
      };
      if (predictors)
        forecast.diagnostics.factorIncrement = {
          baselineRows: structuredClone(forecast.rows),
          baselineModelFits: [],
          baselineValidation: {
            holdoutStart: domain.holdoutStart,
            holdoutEnd: domain.holdoutEnd,
          },
        };
      coverage.holdoutStart = report.validation.holdoutStart =
        domain.holdoutStart;
      report.validation.holdoutEnd = domain.holdoutEnd;
      coverage.baselineRequired = domain.baselineRequired;
      report.capacity = {
        holdoutStart: domain.holdoutStart,
        baselineRequired: domain.baselineRequired,
        forecastRows: domain.expectedRows,
        sampleRows: domain.sampleRows,
        inputRows: calendar.length * symbols.length,
        completeGridRows: calendar.length * symbols.length,
        symbols: [...symbols].sort(),
      };
      coverage.origins = forecast.rows.map(
        ({ date, targetId, entryDate, targetDate }) => ({
          date,
          targetId,
          entryDate,
          targetDate,
          inputValid: false,
        }),
      );
      snapshot.provenance.tradingDates = calendar;
      if (mutate) mutate(values, domain);
    },
  });
  return {
    ...fixture,
    admission: {
      strategy,
      manifest: {
        calendar,
        scope: { symbols },
        rowCount: calendar.length * symbols.length,
      },
    },
    domain,
  };
}
const schema = await fs.readFile(
  new URL("../edge/schema.sql", import.meta.url),
  "utf8",
);
const script = await buildWorkerSource({
  wrapper: `
import {validateMarketBundle} from './edge/market-preparation/bundle.mjs';
import {validateChunk} from './edge/bundles/manifest.mjs';
import {recordIndex,indexStatements} from './edge/bundles/records.mjs';
import {verifyRecords} from './edge/bundles/verify.mjs';
import {verifyDocuments} from './edge/bundles/streams.mjs';
import {verifyMarketCoverage} from './edge/market-preparation/coverage.mjs';
export default {async fetch(req,env){
  let prechecks=false;
  try {
    const p=await req.json(),parsed=await validateMarketBundle(p.manifestText,p.bundleId);
    const id=crypto.randomUUID(),now=new Date().toISOString(),lease=crypto.randomUUID();
    await env.DB.prepare('INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)').bind(id,'fixture','fixture','running','ready_market','{}',lease,new Date(Date.now()+120000).toISOString(),now,now).run();
    const stage={id,job_id:id,lease_token:lease,metadata:'{}'};
    await env.DB.prepare('INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(id,'fixture',id,lease,p.bundleId,p.manifestText,'fixture','{}','staging',now,now).run();
    for(const c of parsed.collections.values()) for(const chunk of c.chunks){
      const rows=await validateChunk(p.chunks[c.id+':'+chunk.ordinal],chunk);
      const indexed=await Promise.all(rows.map((row,i)=>recordIndex(c.id,row,chunk.start+i,chunk.ordinal,i)));
      await env.DB.batch(indexStatements(env,stage,c.id,indexed));
    }
    const read=async(c,d)=>new TextEncoder().encode(p.chunks[c+':'+d.ordinal]);
    await verifyRecords(env,stage,parsed);
    await verifyDocuments(parsed,read);
    prechecks=true;
    const domain=await verifyMarketCoverage(env,stage,parsed,p.admission,read);
    return Response.json({ok:true,prechecks,rows:domain.expectedRows});
  }catch(error){return Response.json({code:error.code||'ERROR',message:error.message,prechecks},{status:409});}
}};`,
});
async function check(fixture) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
  });
  try {
    await (await mf.getD1Database("DB")).exec(schema.replaceAll("\n", " "));
    const response = await mf.dispatchFetch("https://coverage.test/", {
      method: "POST",
      body: JSON.stringify({
        manifestText: fixture.manifestText,
        bundleId: fixture.bundleId,
        chunks: Object.fromEntries(fixture.chunks),
        admission: fixture.admission,
      }),
    });
    return { status: response.status, ...(await response.json()) };
  } finally {
    await mf.dispose();
  }
}

test("full grid includes all invalid origins and null tail endpoints with/without baseline", async () => {
  for (const predictors of [true, false]) {
    const fixture = await makeFixture({ predictors });
    assert.equal(fixture.forecast.rows.at(-1).entryDate, null);
    assert.equal(fixture.forecast.rows.at(-1).targetDate, null);
    const result = await check(fixture);
    assert.equal(result.status, 200, JSON.stringify(result));
    assert.equal(result.rows, fixture.domain.expectedRows);
  }
});

test("lookback and observation stride are anchored before holdout, not at its boundary", async () => {
  const fixture = await makeFixture({
    observationDays: 4,
    expression: "returns(close,90)",
  });
  assert.equal(fixture.domain.holdoutStart, calendar[179]);
  assert.equal(fixture.domain.origins[0].date, calendar[179]);
  const shifted = await makeFixture({ observationDays: 3 });
  assert.equal(shifted.domain.holdoutStart, calendar[173]);
  assert.equal(shifted.domain.origins[0].date, calendar[175]);
  for (const f of [fixture, shifted])
    assert.equal((await check(f)).status, 200);
});

for (const variant of [
  "half-pool",
  "whole-date",
  "tail",
  "swap-assets",
  "forged-clock",
  "wrong-definition",
  "drop-baseline",
  "baseline-clock",
  "capacity-clock",
  "post-fit-coverage",
]) {
  test(
    "fully rehashed " + variant + " cannot pass source-derived coverage",
    async () => {
      const f = await makeFixture({
        mutate({ forecast, coverage, report }, domain) {
          const baseline = forecast.diagnostics.factorIncrement;
          if (["half-pool", "whole-date", "tail"].includes(variant)) {
            const keep = (r) =>
              variant === "half-pool"
                ? r.targetId === domain.targets[0].id
                : variant === "whole-date"
                  ? r.date !== domain.origins[0].date
                  : r.targetDate !== null;
            forecast.rows = forecast.rows.filter(keep);
            baseline.baselineRows = baseline.baselineRows.filter(keep);
            coverage.origins = coverage.origins.filter(keep);
            forecast.totalRows = forecast.rows.length;
          } else if (variant === "swap-assets") {
            for (const rows of [
              forecast.rows,
              baseline.baselineRows,
              coverage.origins,
            ]) {
              for (let i = 0; i < rows.length; i += 2)
                [rows[i], rows[i + 1]] = [rows[i + 1], rows[i]];
            }
          } else if (variant === "forged-clock") {
            coverage.holdoutStart =
              forecast.diagnostics.holdoutStart =
              report.validation.holdoutStart =
                calendar[172];
            baseline.baselineValidation.holdoutStart = calendar[172];
          } else if (variant === "wrong-definition") {
            forecast.targetDefinitions[0].quantities = [2];
          } else if (variant === "baseline-clock") {
            baseline.baselineValidation.holdoutStart = calendar[172];
          } else if (variant === "capacity-clock") {
            report.capacity.holdoutStart = calendar[172];
          } else if (variant === "post-fit-coverage") {
            coverage.source = "legacy_artifact_derived";
          } else {
            coverage.baselineRequired = false;
            baseline.baselineRows = [];
          }
        },
      });
      const result = await check(f);
      assert.equal(result.prechecks, true, JSON.stringify(result));
      assert.equal(
        result.code,
        "MARKET_FORECAST_COVERAGE",
        JSON.stringify(result),
      );
    },
  );
}
