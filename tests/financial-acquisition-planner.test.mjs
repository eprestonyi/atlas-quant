import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import {
  acquisitionEnabled,
  createPlanSpec,
  digest,
  profile,
} from "../edge/financial-acquisition/planner.mjs";

const base = () => ({
  requestId: randomUUID(),
  profile: profile.id,
  name: "年度来源",
  symbols: ["600690.SH"],
  period: "20241231",
  announcementStart: "20250101",
  start: "20250301",
  end: "20251231",
  selectedStateIds: ["model_fin_cash_asset_share"],
});
const plan = (value = base(), scope = "test-entitlement-v1") =>
  createPlanSpec(value, scope, "20261008");

test("financial acquisition remains default off and requires both authorization and stable scope", () => {
  assert.equal(acquisitionEnabled({}), false);
  const enabled = {
    FINANCIAL_ACQUISITION_ENABLED: "true",
    FINANCIAL_ACQUISITION_AUDIENCE: "public",
    TUSHARE_PUBLIC_AUTHORIZED: "true",
    FINANCIAL_ACQUISITION_AUTH_SCOPE: "licensed-domain-v1",
  };
  assert.equal(acquisitionEnabled(enabled), true);
  for (const field of Object.keys(enabled))
    assert.equal(acquisitionEnabled({ ...enabled, [field]: undefined }), false);
  assert.equal(
    acquisitionEnabled({
      ...enabled,
      FINANCIAL_ACQUISITION_AUTH_SCOPE: "secret value with spaces",
    }),
    false,
  );
});
test("deterministic annual request plan has exactly four or seven explicit nonretrying requests", async () => {
  const a = await plan(),
    b = await plan({ ...base(), symbols: ["600690.SH", "600519.SH"] });
  assert.equal(a.requests.length, 4);
  assert.equal(b.requests.length, 7);
  assert.equal(b.budget.maximumProviderCalls, 7);
  assert.deepEqual(a.requests[0].params, {
    exchange: "SSE",
    start_date: "20250101",
    end_date: "20251231",
  });
  assert.equal(a.requests[1].params.comp_type, "1");
  assert.equal(a.requests[1].params.report_type, "1");
  assert.equal(a.researchBinding, false);
  assert.equal((await plan()).planRoot, a.planRoot);
  assert.equal(
    (await plan({ ...base(), symbols: ["600519.SH", "600690.SH"] })).planRoot,
    b.planRoot,
  );
  assert.equal(
    (await plan({ ...base(), symbols: ["000651.SZ"] })).requests[0].params
      .exchange,
    "SZSE",
  );
});
test("cache identities bind authorization, period, exact fields and normalizer but not owner or draft name", async () => {
  const a = await plan(),
    renamed = await plan({ ...base(), name: "另一输入" });
  assert.deepEqual(a.requests, renamed.requests);
  assert.notEqual(a.planRoot, renamed.planRoot);
  const changedScope = await plan(base(), "another-entitlement");
  assert(
    a.requests.every(
      (request, n) =>
        request.requestKey !== changedScope.requests[n].requestKey,
    ),
  );
  const { requestKey, ...definition } = a.requests[1];
  assert.equal(await digest(definition), requestKey);
  for (const delta of [
    { fields: definition.fields.slice(1) },
    { normalizerVersion: "different" },
    { params: { ...definition.params, period: "20231231" } },
  ])
    assert.notEqual(await digest({ ...definition, ...delta }), requestKey);
});
test("invalid or expanded scopes fail rather than filtering stocks or fabricating a usable input", async () => {
  for (const delta of [
    { symbols: ["600690.SH", "000651.SZ"] },
    { symbols: ["600690.SH", "600690.SH"] },
    { symbols: [] },
    { symbols: ["600690.SH", "600519.SH", "600000.SH"] },
    { symbols: ["600690.BJ"] },
    { period: "20240630" },
    { period: "20261231" },
    { announcementStart: "20240101" },
    { end: "20270101" },
    { start: "20250100" },
    { selectedStateIds: [] },
    { selectedStateIds: ["invented_state"] },
    { trusted: true },
  ])
    await assert.rejects(plan({ ...base(), ...delta }));
});
