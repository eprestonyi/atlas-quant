import crypto from "node:crypto";
import { validateStatisticalQuant } from "../../edge/statistical-quant/validation.mjs";
import { COLLECTION_PATHS } from "../../edge/bundles/profile.mjs";
export const hash = (value) =>
  crypto.createHash("sha256").update(value).digest("hex");
export const canonical = (value) =>
  JSON.stringify(
    value && typeof value === "object"
      ? Array.isArray(value)
        ? value.map((x) => JSON.parse(canonical(x)))
        : Object.fromEntries(
            Object.keys(value)
              .sort()
              .map((k) => [k, JSON.parse(canonical(value[k]))]),
          )
      : value,
  );

/** Tiny explicit SYNTHETIC transport fixture; it is not model acceptance evidence. */
export function bundleFixture({
  count = 4,
  rowsPerChunk = 2,
  mutate = null,
  execution = false,
  sourceForecast = null,
} = {}) {
  const strategy = validateStatisticalQuant({
    schemaVersion: 2,
    name: "SYNTHETIC transport integrity fixture",
    universe: { symbols: ["000001.SZ"], start: "20230101", end: "20250930" },
    research: { mode: "statistical_quant" },
    target: { kind: "asset_price", horizonSessions: 5 },
    model: { family: "trend", estimator: "ridge" },
    execution: { enabled: false },
  });
  const rows = Array.from({ length: count }, (_, i) => ({
    forecastId: "f" + i,
    date: "202501" + String(2 + i).padStart(2, "0"),
    targetId: "t",
    modelFitId: "m",
    informationCutoff: "202501" + String(2 + i).padStart(2, "0"),
    entryDate: "202501" + String(3 + i).padStart(2, "0"),
    targetDate: "20250203",
    horizonSessions: 5,
    currentState: 10,
    scale: 10,
    expectedEntry: 10,
    expectedFuture: 11,
    edgeGap: -1,
    expectedChange: 1,
    expectedGrossPnl: 1,
    expectedGrossBps: 1000,
    realizedEntry: 10,
    realizedFuture: 11,
    forecastError: 0,
    labelMaturedAt: "20250203",
    status: "valid",
    invalidReason: null,
    uncertainty: null,
  }));
  const forecast = sourceForecast ?? {
    schemaVersion: 1,
    predictionConfigHash: "b".repeat(64),
    dataFingerprint: "c".repeat(64),
    sourceStrategy: strategy,
    totalRows: count,
    truncated: false,
    rows,
    targetDefinitions: [
      {
        id: "t",
        kind: "asset_price",
        symbols: ["000001.SZ"],
        quantities: [1],
        unit: "CNY",
        construction: "single_asset",
      },
    ],
    modelFits: [
      {
        id: "m",
        status: "valid",
        estimator: "ridge",
        trainStart: "20230101",
        trainEnd: "20241231",
        fitDate: "20250102",
        labelEndMax: "20241231",
      },
    ],
    diagnostics: { metrics: { mse: 0 } },
    hedgeFits: [],
  };
  const coverage = {
    schemaVersion: 1,
    source: "samples_before_model_fitting",
    baselineRequired: false,
    holdoutStart: "20250102",
    origins: forecast.rows.map((r) => ({
      date: r.date,
      targetId: r.targetId,
      entryDate: r.entryDate,
      targetDate: r.targetDate,
      inputValid: true,
    })),
  };
  const report = {
    schemaVersion: 2,
    status: "completed",
    engineVersion: "0.4.0",
    strategy: forecast.sourceStrategy,
    research: {
      mode: "statistical_quant",
      ...(execution
        ? { executionOnly: true, predictionRefitPerformed: false }
        : {}),
    },
    provenance: { synthetic: true, source: "SYNTHETIC_TRANSPORT_FIXTURE" },
    selection: { winner: "ridge", evidenceStatus: "UNVALIDATED_RESEARCH" },
    metrics: null,
    equity: [],
    trades: [],
    execution: { ledger: [], decisions: [] },
    validation: {},
  };
  const snapshot = {
    schemaVersion: 1,
    dataFingerprint: forecast.dataFingerprint,
    provenance: { synthetic: true, tradingDates: ["20250102"] },
    rows: [
      {
        ts_code: "000001.SZ",
        trade_date: "20250102",
        open: 10,
        close: 10,
        vol: 100,
      },
    ],
  };
  const inputs = { forecast, report, snapshot, coverage };
  if (mutate) mutate(inputs);
  const artifactId = hash(canonical(forecast));
  report.forecasts = { artifactId, ...forecast };
  report.execution.forecastArtifactId = artifactId;
  const documents = {},
    collections = [],
    chunks = new Map();
  function document(name, value) {
    const parts = [];
    const literal = (text) => {
      if (parts.at(-1)?.literal !== undefined) parts.at(-1).literal += text;
      else parts.push({ literal: text });
    };
    function visit(node, path = "") {
      const entry = Object.entries(COLLECTION_PATHS).find(
        ([, x]) => x[0] === name && x[1] === path,
      );
      if (entry && Array.isArray(node)) {
        const id = entry[0],
          descriptors = [];
        for (let start = 0; start < node.length; start += rowsPerChunk) {
          const raw = canonical(node.slice(start, start + rowsPerChunk));
          const ordinal = descriptors.length;
          descriptors.push({
            ordinal,
            start,
            count: Math.min(rowsPerChunk, node.length - start),
            sha256: hash(raw),
            byteLength: Buffer.byteLength(raw),
          });
          chunks.set(id + ":" + ordinal, raw);
        }
        collections.push({
          id,
          document: name,
          path,
          rowCount: node.length,
          chunks: descriptors,
        });
        parts.push({ collection: id });
        return;
      }
      if (name === "report" && path === "/forecasts") {
        parts.push({ document: "forecast", wrapArtifactId: artifactId });
        return;
      }
      if (node && typeof node === "object" && !Array.isArray(node)) {
        literal("{");
        Object.keys(node)
          .sort()
          .forEach((key, i) => {
            if (i) literal(",");
            literal(JSON.stringify(key) + ":");
            visit(node[key], path + "/" + key);
          });
        literal("}");
      } else literal(canonical(node));
    }
    visit(value);
    const raw = canonical(value);
    documents[name] = {
      parts,
      sha256: hash(raw),
      byteLength: Buffer.byteLength(raw),
    };
  }
  document("forecast", forecast);
  document("report", report);
  if (!execution) document("snapshot", snapshot);
  document("coverage", coverage);
  const manifest = {
    format: "atlas.quant.bundle",
    version: 1,
    kind: execution ? "execution" : "forecast",
    forecastArtifactId: artifactId,
    predictionConfigHash: forecast.predictionConfigHash,
    dataFingerprint: forecast.dataFingerprint,
    documents,
    collections,
    totals: {
      chunkCount: chunks.size,
      chunkBytes: [...chunks.values()].reduce(
        (n, x) => n + Buffer.byteLength(x),
        0,
      ),
    },
  };
  const manifestText = canonical(manifest);
  return {
    manifest,
    manifestText,
    bundleId: hash(manifestText),
    chunks,
    strategy: forecast.sourceStrategy,
    forecast,
    report,
    snapshot,
    coverage,
  };
}
