import { bundleFixture, canonical, hash } from './bundle-fixture.mjs';
import { validateStatisticalQuant } from '../../edge/statistical-quant/validation.mjs';

/** Mechanical protocol fixture; no assertion of real financial source closure. */
export function financialBundleFixture() {
  const sourceEvidence = {
    datasetRef: {
      datasetId: '00000000-0000-0000-0000-000000000081',
      datasetRoot: 'e'.repeat(64),
      format: 'atlas.quant.research_dataset',
      version: 2
    },
    admissionProfile: 'financial_snapshot_view_50_v1'
  };
  const f = bundleFixture({
    mutate({ forecast, report, snapshot }) {
      forecast.sourceStrategy = validateStatisticalQuant({
        ...forecast.sourceStrategy,
        factors: [
          {
            id: 'source_roe',
            expression: 'fd_roe',
            direction: 1,
            role: 'predictor'
          }
        ],
        model: { ...forecast.sourceStrategy.model, family: 'fundamental' }
      });
      report.strategy = forecast.sourceStrategy;
      report.research.executionOnly = false;
      report.provenance.dataSha256 = forecast.dataFingerprint;
      report.provenance.financialSourceCommitment = {
        financialCompositionVersion: 'financial_dataset_v1',
        marketRoot: 'd'.repeat(64),
        financialDatasetRoot: 'f'.repeat(64),
        financialInputs: []
      };
      Object.assign(snapshot, {
        schemaVersion: 2,
        fingerprintVersion: 'research_input_financial_v1',
        datasetRef: sourceEvidence.datasetRef,
        sourceEvidenceClosure: 'separate_research_dataset_v2',
        sourceDataFingerprint: 'a'.repeat(64),
        financialSourceCommitment: report.provenance.financialSourceCommitment
      });
      snapshot.provenance.dataFingerprint = 'a'.repeat(64);
      snapshot.provenance.numericWitness = {
        positiveFloat: 1,
        negativeZero: 0,
        missing: null
      };
      Object.assign(snapshot.rows[0], {
        positiveFloat: 1,
        negativeZero: 0,
        missing: null
      });
    }
  });
  const typed = (raw) =>
    raw
      .replaceAll('"positiveFloat":1', '"positiveFloat":1.0')
      .replaceAll('"negativeZero":0', '"negativeZero":-0.0');
  const manifest = f.manifest;
  manifest.format = 'atlas.quant.financial_bundle';
  manifest.sourceEvidence = sourceEvidence;
  for (const [name, doc] of Object.entries(manifest.documents))
    doc.codec = name === 'snapshot' ? 'financial_json_v1' : 'forecast_json_v1';
  for (const item of manifest.collections.find((x) => x.id === 'snapshotRows').chunks) {
    const key = 'snapshotRows:' + item.ordinal,
      raw = typed(f.chunks.get(key));
    f.chunks.set(key, raw);
    item.sha256 = hash(raw);
    item.byteLength = Buffer.byteLength(raw);
  }
  const snapshot = manifest.documents.snapshot;
  for (const part of snapshot.parts) if (part.literal) part.literal = typed(part.literal);
  f.snapshotText = snapshot.parts
    .map(
      (part) =>
        part.literal ??
        '[' +
          manifest.collections
            .find((x) => x.id === part.collection)
            .chunks.map((d) => f.chunks.get(part.collection + ':' + d.ordinal).slice(1, -1))
            .join(',') +
          ']'
    )
    .join('');
  snapshot.sha256 = hash(f.snapshotText);
  snapshot.byteLength = Buffer.byteLength(f.snapshotText);
  manifest.totals.chunkBytes = [...f.chunks.values()].reduce((n, x) => n + Buffer.byteLength(x), 0);
  f.manifestText = canonical(manifest);
  f.bundleId = hash(f.manifestText);
  f.sourceEvidence = sourceEvidence;
  const exactProvenance = typed(canonical(f.snapshot.provenance));
  const exactRows = manifest.collections
    .find((x) => x.id === 'snapshotRows')
    .chunks.map((d) => f.chunks.get('snapshotRows:' + d.ordinal).slice(1, -1))
    .join(',');
  const joined =
    '{"provenance":' + exactProvenance + ',"rows":[' + exactRows + '],"schemaVersion":1}';
  f.sourceDatasetManifest = {
    scope: strategyScope(f.strategy),
    roots: { marketRoot: 'd'.repeat(64), financialDatasetRoot: 'f'.repeat(64) },
    components: [
      {
        componentId: 'researchRows',
        type: 'research_rows',
        version: 1,
        encoding: 'raw_bytes',
        payloadSha256: hash(joined),
        byteLength: Buffer.byteLength(joined)
      }
    ]
  };
  return f;
}

function strategyScope(strategy) {
  return Object.fromEntries(['symbols', 'start', 'end'].map((k) => [k, strategy.universe[k]]));
}
