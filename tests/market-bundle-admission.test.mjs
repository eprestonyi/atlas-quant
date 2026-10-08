import test from "node:test";
import assert from "node:assert/strict";
import { bundleFixture, canonical, hash } from "./fixtures/bundle-fixture.mjs";
import { validateManifest } from "../edge/bundles/manifest.mjs";
import {
  validateMarketBundle,
  storedMarketAdmission,
} from "../edge/market-preparation/bundle.mjs";

test("numerical count expansion is a server-side parser choice, never runner metadata", async () => {
  // Layout-only descriptors: no chunks are uploaded and this is not a valid
  // numerical report. Finalization must independently read/count all records.
  const fixture = bundleFixture({ count: 1 });
  const m = structuredClone(fixture.manifest);
  for (const c of m.collections) {
    if (!["forecasts", "plannedOrigins"].includes(c.id)) continue;
    const d = c.chunks[0];
    c.rowCount = 25001;
    c.chunks = [10000, 10000, 5001].map((count, ordinal) => ({
      ...d,
      count,
      ordinal,
      start: ordinal * 10000,
    }));
  }
  for (const p of m.documents.forecast.parts) {
    if (p.literal)
      p.literal = p.literal.replace('"totalRows":1', '"totalRows":25001');
  }
  const allChunks = m.collections.flatMap((c) => c.chunks);
  m.totals = {
    chunkCount: allChunks.length,
    chunkBytes: allChunks.reduce((n, c) => n + c.byteLength, 0),
  };
  let raw = canonical(m);
  await assert.rejects(
    validateManifest(raw, hash(raw)),
    (e) => e.code === "BUNDLE_BUDGET",
  );
  assert.equal(
    (await validateMarketBundle(raw, hash(raw))).metadata.forecast.totalRows,
    25001,
  );
  m.admissionProfile = "pooled_asset_1000_v1";
  raw = canonical(m);
  await assert.rejects(validateManifest(raw, hash(raw)));
  assert.equal(
    storedMarketAdmission({
      metadata: JSON.stringify({
        report: { admissionProfile: "pooled_asset_1000_v1" },
      }),
    }),
    null,
  );
});
