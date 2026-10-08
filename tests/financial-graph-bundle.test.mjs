/** Real tiny synthetic core output. No provider or production calls. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { Miniflare } from 'miniflare';
import { buildWorkerSource } from '../scripts/worker-source.mjs';
import {
  validateFinancialGraphManifest,
  validateGraphRows
} from '../edge/financial-graph-bundles/manifest.mjs';
import { validateFinancialManifest } from '../edge/financial-bundles/manifest.mjs';
import { validateManifest } from '../edge/bundles/manifest.mjs';
import { canonical } from './fixtures/bundle-fixture.mjs';
const hash = (value) => createHash('sha256').update(value).digest('hex');
const generated = process.env.GRAPH_FIXTURE
  ? { status: 0, stdout: await fs.readFile(process.env.GRAPH_FIXTURE, 'utf8') }
  : spawnSync(
      process.env.PYTHON || '.venv/bin/python',
      ['tests/helpers/financial-graph-bundle.py'],
      {
        encoding: 'utf8',
        env: { ...process.env, PYTHONPATH: 'engine' },
        timeout: 120000,
        maxBuffer: 8 * 1024 ** 2
      }
    );
assert.equal(generated.status, 0, generated.stderr || String(generated.error));
const fixture = JSON.parse(generated.stdout);
assert.equal(fixture.sourceKind, 'SYNTHETIC');
assert.equal(fixture.providerCalls, 0);

function rows(f, id) {
  const m = JSON.parse(f.manifestText),
    c = m.collections.find((x) => x.id === id);
  return c.chunks.flatMap((d) => JSON.parse(f.chunks[id + ':' + d.ordinal]));
}
function rewrite(base, change) {
  const f = structuredClone(base),
    m = JSON.parse(f.manifestText),
    oldArtifact = m.forecastArtifactId;
  const replace = (name, values) => {
    const c = m.collections.find((x) => x.id === name);
    for (const d of c.chunks) delete f.chunks[name + ':' + d.ordinal];
    const raw = canonical(values);
    c.rowCount = values.length;
    c.chunks = values.length
      ? [
          {
            ordinal: 0,
            start: 0,
            count: values.length,
            sha256: hash(raw),
            byteLength: Buffer.byteLength(raw)
          }
        ]
      : [];
    if (values.length) f.chunks[name + ':0'] = raw;
  };
  change({ f, m, replace, rows: (id) => rows(f, id) });
  const render = (name) =>
    m.documents[name].parts
      .map((p) => {
        if (Object.hasOwn(p, 'literal')) return p.literal;
        if (p.collection) {
          const c = m.collections.find((x) => x.id === p.collection);
          return (
            '[' + c.chunks.map((d) => f.chunks[c.id + ':' + d.ordinal].slice(1, -1)).join(',') + ']'
          );
        }
        return '{"artifactId":"' + p.wrapArtifactId + '",' + render('forecast').slice(1);
      })
      .join('');
  const total = m.collections.find((x) => x.id === 'forecasts').rowCount;
  for (const p of m.documents.forecast.parts)
    if (p.literal) p.literal = p.literal.replace(/"totalRows":\d+/, '"totalRows":' + total);
  m.forecastArtifactId = hash(render('forecast'));
  for (const p of m.documents.report.parts) {
    if (p.document) p.wrapArtifactId = m.forecastArtifactId;
    if (p.literal) p.literal = p.literal.replaceAll(oldArtifact, m.forecastArtifactId);
  }
  for (const [name, d] of Object.entries(m.documents)) {
    const raw = render(name);
    d.sha256 = hash(raw);
    d.byteLength = Buffer.byteLength(raw);
  }
  m.totals = {
    chunkBytes: m.collections.flatMap((c) => c.chunks).reduce((n, d) => n + d.byteLength, 0),
    chunkCount: m.collections.reduce((n, c) => n + c.chunks.length, 0)
  };
  f.manifestText = canonical(m);
  return f;
}
const script = await buildWorkerSource({
  wrapper: `
import {validateFinancialGraphManifest,validateGraphRows} from './edge/financial-graph-bundles/manifest.mjs';
import {assertGraphDatasetCommitment,verifyGraphSnapshotDataset} from './edge/financial-graph-bundles/source.mjs';
import {verifyGraphCoverage} from './edge/financial-graph-bundles/coverage.mjs';
import {validateChunk} from './edge/bundles/manifest.mjs';
import {recordIndex,indexStatements} from './edge/bundles/records.mjs';
import {verifyDocuments} from './edge/bundles/streams.mjs';
import {parsedStage,assertTransport} from './edge/bundles/storage.mjs';
export default {async fetch(req,env){let documentsVerified=false,sourceVerified=false;try{
 const p=await req.json(),parsed=await validateFinancialGraphManifest(p.manifestText),m=JSON.parse(p.datasetManifestText);
 const id=crypto.randomUUID(),stage={id},now=new Date().toISOString(),lease=crypto.randomUUID();
 await env.DB.prepare("INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,'running','ready_dataset','{}',?,?,?,?)").bind(id,'fixture','SYNTHETIC',lease,new Date(Date.now()+120000).toISOString(),now,now).run();
 await env.DB.prepare("INSERT INTO quant_bundle_stages(id,owner,job_id,lease_token,bundle_id,manifest_text,manifest_key,metadata,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'{}','staging',?,?)").bind(id,'fixture',id,lease,parsed.bundleId,p.manifestText,'fixture',now,now).run();
 const sourceId=crypto.randomUUID();
 await env.DB.prepare('INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)').bind(sourceId,'fixture',sourceId,'a'.repeat(64),'b'.repeat(64),'SYNTHETIC','{}',now).run();
 await env.DB.prepare("INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?)").bind(sourceId,'fixture',sourceId,sourceId,'a'.repeat(64),now,now).run();
 await env.DB.prepare("INSERT INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,'committed','{}',?,?)").bind(sourceId,sourceId,'fixture',lease,sourceId,'b'.repeat(64),p.datasetManifestText,now,now).run();
 for(const c of m.components)for(const d of c.parts){
  const key=sourceId+'/'+c.componentId+'/'+d.ordinal;await env.ARTIFACTS.put(key,p.datasetParts[c.componentId+':'+d.ordinal]);
  await env.DB.prepare('INSERT INTO quant_dataset_parts(stage_id,component_id,ordinal,sha256,byte_length,object_key) VALUES(?,?,?,?,?,?)').bind(sourceId,c.componentId,d.ordinal,d.sha256,d.byteLength,key).run();
 }
 for(const c of parsed.collections.values())for(const d of c.chunks){
  const rr=await validateChunk(p.chunks[c.id+':'+d.ordinal],d,c.id==='snapshotColumns'?'financial_column_snapshot_v1':'forecast_json_v1');
  validateGraphRows(c.id,rr,parsed);
  await env.DB.batch(indexStatements(env,stage,c.id,await Promise.all(rr.map((r,i)=>recordIndex(c.id,r,d.start+i,d.ordinal,i)))));
 }
 const read=async(c,d)=>new TextEncoder().encode(p.chunks[c+':'+d.ordinal]);
 await verifyDocuments(parsed,read);documentsVerified=true;
 const commitment=await assertGraphDatasetCommitment(env,{manifest:m,stage:{id:sourceId}},parsed);
 await verifyGraphSnapshotDataset(commitment,parsed,read);sourceVerified=true;
 const domain=await verifyGraphCoverage(env,stage,parsed,commitment,read);
 const dispatch=await parsedStage({metadata:'{}',manifest_text:p.manifestText,bundle_id:parsed.bundleId});
 assertTransport(dispatch,'atlas.quant.financial_bundle',2);
 let rejectsLegacy=false;try{assertTransport(dispatch,'atlas.quant.financial_bundle',1);}catch{rejectsLegacy=true;}
 return Response.json({ok:true,documentsVerified,sourceVerified,rows:domain.expectedRows,rejectsLegacy});
 }catch(e){return Response.json({code:e.code||'ERROR',message:e.message,documentsVerified,sourceVerified},{status:e.status||400});}}};`
});
const schema = await fs.readFile(new URL('../edge/schema.sql', import.meta.url), 'utf8');
async function check(f) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS']
  });
  try {
    await (await mf.getD1Database('DB')).exec(schema.replaceAll('\n', ' '));
    const r = await mf.dispatchFetch('https://graph.test/', {
      method: 'POST',
      body: JSON.stringify(f)
    });
    return { status: r.status, ...(await r.json()) };
  } finally {
    await mf.dispose();
  }
}

test('actual synthetic graph columns preserve source bytes and full output including six tail rows', async () => {
  const r = await check(fixture);
  assert.equal(r.status, 200, JSON.stringify(r));
  assert.equal(r.rows, 41);
  assert.equal(r.sourceVerified, true);
  assert.equal(r.rejectsLegacy, true);
  assert.equal(rows(fixture, 'forecasts').filter((r) => r.status === 'invalid').length, 6);
});
test('financial bundle/1 and ordinary bundle/1 never accept graph format', async () => {
  await assert.rejects(validateFinancialManifest(fixture.manifestText));
  await assert.rejects(validateManifest(fixture.manifestText));
});
for (const id of ['forecasts', 'baselineRows', 'plannedOrigins'])
  test('fully rehashed missing ' + id + ' records rejected from source-derived clock', async () => {
    const f = rewrite(fixture, ({ replace, rows }) => replace(id, rows(id).slice(0, -1)));
    const r = await check(f);
    assert.notEqual(r.status, 200, JSON.stringify(r));
    assert.notEqual(r.code, 'ERROR', JSON.stringify(r));
  });
test('fully rehashed simultaneous half-window main/baseline/plan deletion cannot define its own domain', async () => {
  const f = rewrite(fixture, ({ replace, rows }) => {
    for (const id of ['forecasts', 'baselineRows', 'plannedOrigins'])
      replace(id, rows(id).slice(20));
  });
  const r = await check(f);
  assert.equal(r.sourceVerified, true, JSON.stringify(r));
  assert.equal(r.code, 'FINANCIAL_GRAPH_COVERAGE');
});
for (const change of [
  'value',
  'null',
  'signed-zero',
  'row-count',
  'secret-name',
  'duplicate-column',
  'dictionary-index',
  'unused-dictionary'
])
  test(
    'graph snapshot rejects ' + change + ' mutation even after chunk/document rehash',
    async () => {
      const f = rewrite(fixture, ({ replace, rows }) => {
        const c = rows('snapshotColumns'),
          n = c.find((x) => x.kind === 'number'),
          d = c.find((x) => x.kind === 'dictionary');
        if (change === 'value') n.values[0] = 12345;
        if (change === 'null') n.values[0] = null;
        if (change === 'signed-zero') n.values[0] = -0;
        if (change === 'row-count') n.values.pop();
        if (change === 'secret-name') n.name = 'serviceToken';
        if (change === 'duplicate-column') c.push(structuredClone(n));
        if (change === 'dictionary-index') d.indices[0] = d.dictionary.length;
        if (change === 'unused-dictionary') d.dictionary.push('unused');
        replace('snapshotColumns', c);
      });
      const r = await check(f);
      assert.notEqual(r.status, 200, JSON.stringify(r));
      if (change === 'duplicate-column') assert.match(r.message, /UNIQUE constraint/);
      else assert.notEqual(r.code, 'ERROR', JSON.stringify(r));
    }
  );
test('snapshot column lexical kinds stay strict and legacy collection names cannot substitute', async () => {
  const p = await validateFinancialGraphManifest(fixture.manifestText);
  assert.throws(() =>
    validateGraphRows(
      'snapshotColumns',
      [{ name: 'x', kind: 'number', values: Array(262).fill(true) }],
      p
    )
  );
  const m = JSON.parse(fixture.manifestText);
  m.collections.find((c) => c.id === 'snapshotColumns').id = 'snapshotRows';
  await assert.rejects(validateFinancialGraphManifest(canonical(m)));
});

test('actual Worker graph upload, source admission, owner report/archive and portable F resolve', async (t) => {
  const production = await buildWorkerSource();
  const secret = 'graph-fixture-runner';
  const mf = new Miniflare({
    modules: true,
    script: production,
    compatibilityDate: '2026-08-01',
    d1Databases: ['DB'],
    r2Buckets: ['ARTIFACTS'],
    bindings: {
      RUNNER_SECRET: secret,
      RESEARCH_DATASET_GRAPHS_ENABLED: 'true',
      FINANCIAL_GRAPH_RESEARCH_ENABLED: 'true'
    }
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB'),
    bucket = await mf.getR2Bucket('ARTIFACTS');
  await db.exec(schema.replaceAll('\n', ' '));
  const call = (path, { data, raw, cookie, headers = {} } = {}) =>
    mf.dispatchFetch('https://graph.test/quant/api' + path, {
      method: data !== undefined ? 'POST' : raw !== undefined ? 'PUT' : 'GET',
      headers: {
        'content-type': 'application/json',
        ...(cookie ? { cookie } : {}),
        ...(path.startsWith('/runner/') ? { authorization: 'Bearer ' + secret } : {}),
        ...headers
      },
      ...(data !== undefined
        ? { body: JSON.stringify(data) }
        : raw !== undefined
          ? { body: raw }
          : {})
    });
  const reply = async (r, status = 200) => {
    const v = await r.json();
    assert.equal(r.status, status, JSON.stringify(v));
    return v;
  };
  const session = await call('/session'),
    cookie = session.headers.get('set-cookie').split(';')[0],
    who = await session.json();
  // Seed an already committed pure-core source, never an invented provider.
  const owner = who.workspace.id;
  assert(owner);
  const f = fixture,
    m = JSON.parse(f.datasetManifestText),
    bundle = JSON.parse(f.manifestText),
    ref = bundle.sourceEvidence.datasetRef;
  const parsed = await validateFinancialGraphManifest(f.manifestText),
    strategy = parsed.metadata.forecast.sourceStrategy;
  const now = new Date().toISOString(),
    id = crypto.randomUUID(),
    lease = crypto.randomUUID(),
    sourceId = crypto.randomUUID();
  const registry = JSON.parse(f.datasetParts['registryEvidence:0']).entries.map((e) => ({
    ...e,
    kind: JSON.parse(e.rawText).kind
  }));
  for (const e of registry) {
    const key = 'fixture/registry/' + e.ref;
    await bucket.put(key, e.rawText);
    await db
      .prepare(
        'INSERT INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) VALUES(?,?,?,?,?,?,?,?)'
      )
      .bind(e.ref, e.kind, owner, key, e.sha256, e.byteLength, '{}', now)
      .run();
  }
  const spec = {
    profile: m.profile,
    request: { profile: m.profile },
    sources: {
      registry: registry.map(({ rawText, ...e }) => e),
      financial: [{ selection: { selectedStateIds: strategy.factors.map((x) => x.expression) } }]
    }
  };
  await db
    .prepare(
      'INSERT INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) VALUES(?,?,?,?,?,?,?,?)'
    )
    .bind(
      sourceId,
      owner,
      sourceId,
      'a'.repeat(64),
      'b'.repeat(64),
      'SYNTHETIC core graph',
      canonical(spec),
      now
    )
    .run();
  await db
    .prepare(
      "INSERT INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?)"
    )
    .bind(sourceId, owner, sourceId, sourceId, 'a'.repeat(64), now, now)
    .run();
  await db
    .prepare(
      "INSERT INTO quant_dataset_stages(id,job_id,owner,lease_token,dataset_id,dataset_root,manifest_text,total_bytes,status,summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'committed','{}',?,?)"
    )
    .bind(
      sourceId,
      sourceId,
      owner,
      lease,
      ref.datasetId,
      ref.datasetRoot,
      f.datasetManifestText,
      Object.values(f.datasetParts).reduce((n, r) => n + Buffer.byteLength(r), 0),
      now,
      now
    )
    .run();
  await db
    .prepare(
      "INSERT INTO quant_research_datasets(id,owner,name,dataset_root,stage_id,status,scope,summary,created_at) VALUES(?,?,?,?,?,'ready',?,'{}',?)"
    )
    .bind(
      ref.datasetId,
      owner,
      'SYNTHETIC core graph',
      ref.datasetRoot,
      sourceId,
      canonical(m.scope),
      now
    )
    .run();
  for (const c of m.components)
    for (const d of c.parts) {
      const key = sourceId + '/' + c.componentId + '/' + d.ordinal;
      await bucket.put(key, f.datasetParts[c.componentId + ':' + d.ordinal]);
      await db
        .prepare(
          'INSERT INTO quant_dataset_parts(stage_id,component_id,ordinal,sha256,byte_length,object_key) VALUES(?,?,?,?,?,?)'
        )
        .bind(sourceId, c.componentId, d.ordinal, d.sha256, d.byteLength, key)
        .run();
    }
  await db
    .prepare(
      "INSERT INTO jobs(id,owner,name,status,data_source,spec,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,'running','ready_dataset',?,?,?,?,?)"
    )
    .bind(
      id,
      owner,
      'SYNTHETIC F',
      canonical(strategy),
      lease,
      new Date(Date.now() + 120000).toISOString(),
      now,
      now
    )
    .run();
  await db
    .prepare(
      'INSERT INTO quant_run_datasets(job_id,owner,dataset_id,dataset_root,profile,admission,created_at) VALUES(?,?,?,?,?,?,?)'
    )
    .bind(
      id,
      owner,
      ref.datasetId,
      ref.datasetRoot,
      bundle.sourceEvidence.admissionProfile,
      canonical({ strategyHash: hash(canonical(strategy)) }),
      now
    )
    .run();
  await db
    .prepare(
      'INSERT INTO quant_experiments(id,owner,name,spec,created_at,updated_at) VALUES(?,?,?,?,?,?)'
    )
    .bind(id, owner, 'SYNTHETIC graph', canonical(strategy), now, now)
    .run();
  await db
    .prepare(
      'INSERT INTO quant_experiment_versions(experiment_id,version,spec,created_at) VALUES(?,1,?,?)'
    )
    .bind(id, canonical(strategy), now)
    .run();
  await db
    .prepare(
      "INSERT INTO quant_runs(job_id,owner,experiment_id,experiment_version,kind,created_at) VALUES(?,?,?,1,'forecast',?)"
    )
    .bind(id, owner, id, now)
    .run();
  const bundleId = hash(f.manifestText),
    packet = { id, leaseToken: lease, bundleId };
  // Old namespaces never silently promote this exact typed snapshot.
  await reply(
    await call('/runner/financial-bundles/begin', {
      data: { ...packet, manifestText: f.manifestText }
    }),
    400
  );
  const stage = await reply(
    await call('/runner/financial-graph-bundles/begin', {
      data: { ...packet, manifestText: f.manifestText }
    })
  );
  const headers = { 'X-Quant-Job': id, 'X-Quant-Lease': lease, 'X-Quant-Stage': stage.stageId };
  for (const [key, raw] of Object.entries(f.chunks)) {
    const [c, n] = key.split(':');
    await reply(
      await call(`/runner/financial-graph-bundles/${bundleId}/chunks/${c}/${n}`, { raw, headers })
    );
  }
  const completion = { ...packet, stageId: stage.stageId };
  await reply(await call('/runner/financial-bundles/finalize', { data: completion }), 409);
  await reply(await call('/runner/financial-graph-bundles/finalize', { data: completion }));
  await reply(await call('/runner/financial-graph-bundles/complete', { data: completion }));
  const summary = await reply(await call(`/runs/${id}/report`, { cookie }));
  assert.equal(summary.transport.format, 'atlas.quant.financial_bundle');
  assert.equal(summary.transport.version, 2);
  assert.equal(summary.transport.sourceEvidence.datasetRef.version, 3);
  assert.equal(summary.transport.executionEligible, false);
  assert.equal(summary.transport.collections.snapshotColumns, undefined);
  const page = await reply(
    await call(`/runs/${id}/report/pages?collection=forecasts&bundleId=${bundleId}`, { cookie })
  );
  assert.equal(page.total, 41);
  const archive = await call(`/runs/${id}/report/bundle?bundleId=${bundleId}`, { cookie });
  assert.equal(archive.status, 200);
  const tar = Buffer.from(await archive.arrayBuffer());
  let at = 0;
  const entries = new Map();
  while (tar[at]) {
    const h = tar.subarray(at, at + 512),
      name = h.subarray(0, 100).toString().split('\0')[0],
      n = parseInt(h.subarray(124, 136).toString().replaceAll('\0', ''), 8);
    entries.set(name, tar.subarray(at + 512, at + 512 + n));
    at += 512 + Math.ceil(n / 512) * 512;
  }
  assert.equal(entries.get('manifest.json').toString(), f.manifestText);
  assert.equal(entries.size, Object.keys(f.chunks).length + 1);
  for (const [key, raw] of Object.entries(f.chunks)) {
    const [c, n] = key.split(':');
    assert.equal(entries.get(`chunks/${c}/${n}.json`).toString(), raw);
  }
  const model = rows(f, 'modelFits').find((r) => r.functionArtifact);
  assert(model, 'public core exports a real fitted F');
  const resolved = await reply(
    await call('/model-functions/resolve', {
      cookie,
      data: { source: { runId: id, bundleId, modelFitId: model.id } }
    })
  );
  assert.deepEqual(resolved.artifact, model.functionArtifact);
  const other = (await call('/session')).headers.get('set-cookie').split(';')[0];
  assert.equal((await call(`/runs/${id}/report`, { cookie: other })).status, 404);
  assert.equal(
    (
      await call('/model-functions/resolve', {
        cookie: other,
        data: { source: { runId: id, bundleId, modelFitId: model.id } }
      })
    ).status,
    404
  );
  const ack = await reply(
    await call('/runner/financial-graph-bundles/complete', { data: completion })
  );
  assert.equal(ack.idempotent, true);
});
