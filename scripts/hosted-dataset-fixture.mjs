/** Local fixture bootstrap only. It seeds existing source receipts, not a new
 * forecast or financial computation. Every retained value is explicitly synthetic. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import { canonical } from '../edge/datasets/common.mjs';
const defaultDirectory = new URL(
  '../tests/fixtures/dataset-v2-core/',
  import.meta.url,
);
const sha = (x) => createHash('sha256').update(x).digest('hex');
export async function seedDatasetSources(
  db,
  bucket,
  owner,
  fixtureDirectory = null,
) {
  if (!/^[0-9a-f-]{36}$/.test(owner)) throw Error('Exact owner UUID required');
  const directory = fixtureDirectory
    ? pathToFileURL(path.resolve(fixtureDirectory) + '/')
    : defaultDirectory;
  const read = (p) => fs.readFile(new URL(p, directory));
  const origin = JSON.parse(await read('dataset/parts/marketOrigin/0.bin')),
    summary = JSON.parse(await read('summary.json')),
    rawManifest = origin.source.manifestRawText,
    parsed = await validateManifest(rawManifest),
    runId = randomUUID(),
    stageId = randomUUID(),
    experimentId = randomUUID(),
    now = new Date().toISOString(),
    calendarRef = randomUUID(),
    inputId = randomUUID(),
    preparationId = randomUUID(),
    publicationId = randomUUID(),
    financialJob = randomUUID();
  const registryRaw = await read(
      'registry/' + summary.registryRefs[0] + '.json',
    ),
    registry = JSON.parse(registryRaw);
  if (
    registry.payload.kind !== 'fixture' ||
    !registry.evidenceLevel.includes('SYNTHETIC')
  )
    throw Error('Only explicit synthetic evidence allowed');
  const prefix = 'synthetic-dataset/' + owner + '/' + runId;
  await bucket.put(prefix + '/calendar', registryRaw);
  await db
    .prepare(
      "INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,status,created_at) VALUES(?,'calendar',?,?,?,?,?,'active',?)",
    )
    .bind(
      calendarRef,
      owner,
      prefix + '/calendar',
      sha(registryRaw),
      registryRaw.length,
      JSON.stringify({
        label: '合成weekday日历 · 非市场证据',
        calendarRoot: registry.scope.calendarRoot,
      }),
      now,
    )
    .run();
  await db
    .prepare(
      'INSERT INTO quant_experiments(id,owner,name,spec,created_at,updated_at) VALUES(?,?,?,?,?,?)',
    )
    .bind(
      experimentId,
      owner,
      summary.sourceStrategy.name,
      JSON.stringify(summary.sourceStrategy),
      now,
      now,
    )
    .run();
  await db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,created_at,updated_at) VALUES(?,?,?,'completed','demo',?,?,?)",
    )
    .bind(
      runId,
      owner,
      'SYNTHETIC source receipt · no fit',
      JSON.stringify(summary.sourceStrategy),
      now,
      now,
    )
    .run();
  await db
    .prepare(
      "INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,created_at) VALUES(?,?,?,1,'forecast',?)",
    )
    .bind(runId, owner, experimentId, now)
    .run();
  await bucket.put(prefix + '/manifest', rawManifest);
  await db
    .prepare(
      "INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed',?,?)",
    )
    .bind(
      stageId,
      owner,
      runId,
      randomUUID(),
      summary.sourceBundleId,
      rawManifest,
      prefix + '/manifest',
      JSON.stringify(parsed.metadata),
      now,
      now,
    )
    .run();
  await db
    .prepare(
      'INSERT INTO quant_bundle_runs(job_id,owner,stage_id) VALUES(?,?,?)',
    )
    .bind(runId, owner, stageId)
    .run();
  const rows = JSON.parse(origin.source.snapshotRawText).rows,
    collection = parsed.collections.get('snapshotRows');
  for (const d of collection.chunks) {
    const raw = canonical(rows.slice(d.start, d.start + d.count)),
      key = prefix + '/snapshot/' + d.ordinal;
    if (sha(raw) !== d.sha256 || Buffer.byteLength(raw) !== d.byteLength)
      throw Error('Legacy source rows lost original identity');
    await bucket.put(key, raw);
    await db
      .prepare(
        "INSERT INTO quant_bundle_chunks(stage_id,collection,ordinal,start_row,row_count,sha256,byte_length,object_key,created_at) VALUES(?,'snapshotRows',?,?,?,?,?,?,?)",
      )
      .bind(
        stageId,
        d.ordinal,
        d.start,
        d.count,
        d.sha256,
        d.byteLength,
        key,
        now,
      )
      .run();
  }
  const packageRaw = await read('dataset/parts/financialInput0/0.bin'),
    packageValue = JSON.parse(packageRaw),
    coverage = JSON.parse(await read('dataset/parts/coverage/0.bin')),
    f = coverage.financial[0],
    roots = Object.fromEntries(
      ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot'].map((k) => [
        k,
        f[k],
      ]),
    ),
    selection = {
      ...packageValue.selection.universe,
      selectedStateIds: packageValue.selection.selectedStates,
      announcementStart: packageValue.selection.announcementStart,
      scope: packageValue.selection.scope,
      flowBasis: packageValue.selection.flowBasis,
    },
    packageDescriptor = {
      encoding: 'bytes',
      rowCount: null,
      sha256: sha(packageRaw),
      byteLength: packageRaw.length,
      chunks: [
        {
          ordinal: 0,
          startRow: null,
          rowCount: null,
          sha256: sha(packageRaw),
          byteLength: packageRaw.length,
        },
      ],
    },
    financialManifest = {
      summary: { input: { selection, unitPolicy: packageValue.unitPolicy } },
      collections: { package: packageDescriptor },
    },
    financialText = JSON.stringify(financialManifest);
  await db
    .prepare(
      "INSERT INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'prepared',?,'[]',?,?,?,?,?)",
    )
    .bind(
      inputId,
      owner,
      'SYNTHETIC prepared statements · declared units',
      calendarRef,
      packageRaw.length,
      randomUUID(),
      sha(packageRaw),
      now,
      now,
    )
    .run();
  await db
    .prepare(
      "INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'financial_prepare','completed','{}',?,?,?,?)",
    )
    .bind(financialJob, owner, inputId, randomUUID(), sha(packageRaw), now, now)
    .run();
  await db
    .prepare(
      "INSERT INTO financial_publications(id,owner,job_id,manifest_text,manifest_hash,status,total_bytes,created_at,updated_at) VALUES(?,?,?,?,?,'committed',?,?,?)",
    )
    .bind(
      publicationId,
      owner,
      financialJob,
      financialText,
      sha(financialText),
      packageRaw.length,
      now,
      now,
    )
    .run();
  await bucket.put(prefix + '/package', packageRaw);
  await db
    .prepare(
      "INSERT INTO financial_chunks(publication_id,collection,ordinal,sha256,byte_length,start_row,row_count,object_key) VALUES(?,'package',0,?,?,NULL,NULL,?)",
    )
    .bind(
      publicationId,
      sha(packageRaw),
      packageRaw.length,
      prefix + '/package',
    )
    .run();
  await db
    .prepare(
      'INSERT INTO financial_preparations(id,owner,input_id,publication_id,roots,metadata,created_at) VALUES(?,?,?,?,?,?,?)',
    )
    .bind(
      preparationId,
      owner,
      inputId,
      publicationId,
      JSON.stringify(roots),
      JSON.stringify({
        hasUsableStates: f.securities.some((x) =>
          x.states.some((y) => y.okRows > 0),
        ),
      }),
      now,
    )
    .run();
  return {
    synthetic: true,
    providerCalls: 0,
    modelFits: 0,
    sourceBootstrap: 'existing_receipts_only',
    owner,
    marketSource: {
      kind: 'forecast_snapshot_view',
      runId,
      expectedBundleId: summary.sourceBundleId,
      expectedSnapshotSha256: summary.sourceSnapshotSha256,
      transform: summary.transform,
    },
    financialInputs: [{ inputId, preparationId, ...roots }],
    calendarRef,
    scope: summary.scope,
  };
}
