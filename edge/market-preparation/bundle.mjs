/** Server-only numerical admission; bundle/1 wire format and byte budgets stay fixed. */
import { parse } from "../runtime.mjs";
import { fail, canonical, hash } from "./common.mjs";
import {
  validateManifestLayout,
  LEGACY_PROTOCOL,
} from "../bundles/manifest.mjs";
import { validateChunk } from "../bundles/manifest.mjs";
import {
  assertRunMarket,
  validateCapacity,
  MARKET_RESEARCH_PROFILES,
} from "./research.mjs";

const MARKET_PROTOCOL = Object.freeze({
  ...LEGACY_PROTOCOL,
  kinds: ["forecast"],
  maxForecastRows: 80000,
  maxInputRows: 300000,
});
export const validateMarketBundle = (text, id) =>
  validateManifestLayout(text, id, MARKET_PROTOCOL);

export function storedMarketAdmission(stage) {
  const value = parse(stage.metadata)?._marketAdmission;
  if (value === undefined) return null;
  if (
    !value ||
    !MARKET_RESEARCH_PROFILES.includes(value.admissionProfile) ||
    Object.keys(value).sort().join(",") !==
      "admissionProfile,marketDatasetRef,rowValueRoot,universeScopeRef"
  )
    fail("MARKET_BUNDLE_ADMISSION", "服务端来源准入状态无效", 409);
  hash(value.rowValueRoot);
  hash(value.marketDatasetRef?.datasetRoot);
  hash(value.universeScopeRef?.scopeRoot);
  return value;
}

export async function assertMarketBundle(env, job, parsed, expected = null) {
  if (job.data_source !== "ready_market")
    fail("MARKET_RUN_BINDING", "无完整市场准入关联", 409);
  const a = await assertRunMarket(env, job);
  if (expected && canonical(a.sourceEvidence) !== canonical(expected))
    fail("MARKET_BUNDLE_ADMISSION", "市场来源准入不能在上传期间变更", 409);
  const { metadata, collections, manifest } = parsed;
  const strategy = validateCapacity(
    metadata.report.strategy,
    a.admissionProfile,
  );
  const source = validateCapacity(
    metadata.forecast.sourceStrategy,
    a.admissionProfile,
  );
  if (
    manifest.kind !== "forecast" ||
    canonical(strategy) !== canonical(a.strategy) ||
    canonical(source) !== canonical(a.strategy) ||
    canonical(metadata.report.provenance.marketSource) !==
      canonical(a.sourceEvidence) ||
    canonical(metadata.snapshot.provenance.marketSource) !==
      canonical(a.sourceEvidence) ||
    canonical(metadata.snapshot.provenance.tradingDates) !==
      canonical(a.manifest.calendar) ||
    metadata.snapshot.provenance.synthetic !==
      (a.manifest.sourceKind === "fixture") ||
    metadata.report.provenance.synthetic !==
      (a.manifest.sourceKind === "fixture") ||
    metadata.report.provenance.source !==
      (a.manifest.sourceKind === "fixture"
        ? "SYNTHETIC_MARKET_FIXTURE"
        : "TUSHARE_PRO") ||
    metadata.snapshot.provenance.source !==
      (a.manifest.sourceKind === "fixture"
        ? "SYNTHETIC_MARKET_FIXTURE"
        : "TUSHARE_PRO") ||
    collections.get("snapshotRows").rowCount !== a.manifest.rowCount
  )
    fail("MARKET_BUNDLE_SOURCE", "报告与完整市场冻结来源不一致", 409);
  return a.sourceEvidence;
}

export async function verifyMarketSnapshot(stage, parsed, read) {
  const admission = storedMarketAdmission(stage);
  if (!admission) return;
  const stream = new crypto.DigestStream("SHA-256"),
    writer = stream.getWriter();
  for (const descriptor of parsed.collections.get("snapshotRows").chunks) {
    const raw = await read("snapshotRows", descriptor);
    const text =
      typeof raw === "string"
        ? raw
        : new TextDecoder("utf-8", { fatal: true }).decode(raw);
    const rows = await validateChunk(text, descriptor);
    // One decoded chunk and one encoded slice; never cache the complete panel.
    await writer.write(
      new TextEncoder().encode(rows.map((r) => canonical(r) + "\n").join("")),
    );
  }
  await writer.close();
  const actual = Array.from(new Uint8Array(await stream.digest), (v) =>
    v.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== admission.rowValueRoot)
    fail("MARKET_SNAPSHOT_SOURCE", "研究冻结行情与已保存完整市场行不同", 409);
}
