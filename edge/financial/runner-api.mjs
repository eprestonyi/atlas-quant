import { body } from '../runtime.mjs';
import {
  LIMITS,
  CAPABILITY,
  json,
  fail,
  object,
  string,
  id,
  hash,
  parse,
  bytes,
  readBytes,
  ownedInput,
  fixedStream,
} from './common.mjs';
import { claim, heartbeat, leasedJob, finishFailure } from './jobs.mjs';
import { inputRegistry, registryEntry, registryBytes } from './registry.mjs';
import {
  publication,
  publicationStatus,
  beginPublication,
  putChunk,
  completePublication,
  collectionStream,
} from './publications.mjs';

async function taskInput(env, job) {
  const input = await env.DB.prepare('SELECT * FROM financial_inputs WHERE id=? AND owner=?')
    .bind(job.input_id, job.owner)
    .first();
  if (!input) fail('NOT_FOUND', '输入不存在', 404);
  if (job.kind === 'financial_revise') {
    const parent = await ownedInput(env, job.owner, input.parent_id);
    return { input, parent };
  }
  return { input, parent: input };
}
function protectedResponse(body, sha256, byteLength) {
  return new Response(body instanceof ReadableStream ? fixedStream(body, byteLength) : body, {
    encodeBody: 'manual',
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store, no-transform',
      'content-encoding': 'identity',
      'x-content-sha256': sha256,
      'content-length': String(byteLength),
    },
  });
}
async function sourceDescriptor(env, job, parent) {
  if (job.kind === 'financial_validate')
    return {
      sha256: parent.source_hash,
      byteLength: parent.source_bytes,
      representation: 'uploaded_package_bytes',
    };
  const pub = await publication(env, parent.canonical_publication_id);
  if (pub.owner !== job.owner || pub.status !== 'committed')
    fail('NOT_FOUND', '已校验来源不可读取', 404);
  const c = parse(pub.manifest_text).collections.package;
  return {
    sha256: c.sha256,
    byteLength: c.byteLength,
    representation: 'canonical_package_bytes',
    publication: pub,
  };
}

/** Called only after the existing shared runner bearer check. */
export async function financialRunnerApi(req, env, path) {
  if (!path.startsWith('/runner/financial/')) return null;
  const route = path.slice('/runner/financial'.length);
  if (req.method === 'POST' && route === '/heartbeat')
    return json(await heartbeat(env, await body(req, 4096)));
  if (req.method === 'POST' && route === '/claim')
    return json(await claim(env, await body(req, 4096)));
  if (req.method === 'POST' && route === '/publications/begin')
    return json(await beginPublication(env, await body(req, LIMITS.manifestBytes + 4096)));
  if (req.method === 'POST' && route === '/complete')
    return json(await completePublication(env, await body(req, 4096)));
  if (req.method === 'POST' && route === '/fail') {
    const value = object(await body(req, 16384), ['jobId', 'leaseToken', 'error']);
    object(value.error, ['code', 'message', 'issues'], ['code', 'message']);
    string(value.error.code, 80);
    string(value.error.message, 700);
    if (
      value.error.issues !== undefined &&
      (!Array.isArray(value.error.issues) || value.error.issues.length > 20)
    )
      fail('INVALID_INPUT', '错误明细过多');
    const job = await leasedJob(env, value.jobId, value.leaseToken, {
      terminal: true,
      cancel: true,
    });
    return json(await finishFailure(env, job, value.error));
  }
  const match = /^\/jobs\/([a-f0-9-]+)\/(input|source|status|registry)(?:\/([a-f0-9-]+))?$/.exec(
    route
  );
  if (req.method === 'GET' && match) {
    const [, jobId, action, ref] = match,
      job = await leasedJob(env, jobId, req.headers.get('X-Financial-Lease'), {
        terminal: action === 'status',
        cancel: action === 'status',
      });
    if (action === 'status') {
      const prep = job.publication_id
        ? await env.DB.prepare('SELECT id FROM financial_preparations WHERE publication_id=?')
            .bind(job.publication_id)
            .first()
        : null;
      return json({
        job: {
          id: job.id,
          status: job.status,
          inputId: job.input_id,
          error: parse(job.error),
        },
        result:
          job.status === 'completed'
            ? { inputId: job.input_id, preparationId: prep?.id || null }
            : null,
      });
    }
    const { input, parent } = await taskInput(env, job);
    if (action === 'source') {
      const descriptor = await sourceDescriptor(env, job, parent);
      if (descriptor.publication)
        return protectedResponse(
          collectionStream(env, descriptor.publication, 'package'),
          descriptor.sha256,
          descriptor.byteLength
        );
      const obj = await env.ARTIFACTS.get(parent.source_key);
      if (!obj || obj.size !== descriptor.byteLength) fail('SOURCE_INTEGRITY', '源包不可读取', 409);
      return protectedResponse(obj.body, descriptor.sha256, descriptor.byteLength);
    }
    if (action === 'registry') {
      // A download authorizes exactly one reference. Reading every proof again
      // for each body creates quadratic D1 work on the real cloud transport.
      const kind =
        ref === input.calendar_ref
          ? 'calendar'
          : JSON.parse(input.proof_refs).includes(ref)
            ? 'unit_proof'
            : null;
      if (!kind) fail('NOT_FOUND', '此任务未授权该证据', 404);
      const row = await registryEntry(env, input.owner, ref, kind);
      return protectedResponse(await registryBytes(env, row), row.sha256, row.byte_length);
    }
    const registry = await inputRegistry(env, input);
    const descriptor = (row) => ({
      ref: row.id,
      sha256: row.sha256,
      byteLength: row.byte_length,
      url: `/quant/api/runner/financial/jobs/${job.id}/registry/${row.id}`,
    });
    const source = await sourceDescriptor(env, job, parent);
    delete source.publication;
    const payload = {
      job: { id: job.id, kind: job.kind, inputId: job.input_id },
      source: {
        url: `/quant/api/runner/financial/jobs/${job.id}/source`,
        ...source,
      },
      calendar: descriptor(registry.calendar),
      proofs: registry.proofs.map(descriptor),
      operation: parse(job.spec),
      limits: {
        packageBytes: LIMITS.packageBytes,
        resultBytes: LIMITS.preparedBytes,
        chunkBytes: LIMITS.chunkBytes,
        manifestBytes: LIMITS.manifestBytes,
        registryEntryBytes: LIMITS.registryEntryBytes,
        registryTotalBytes: LIMITS.registryTotalBytes,
      },
    };
    if (bytes(payload).length > LIMITS.manifestBytes)
      fail('INPUT_METADATA_BUDGET', '任务元数据超过限制', 413);
    return json(payload);
  }
  const pubMatch =
    /^\/publications\/([a-f0-9-]+)(?:\/chunks\/([a-z]+)\/(0|[1-9][0-9]{0,3}))?$/.exec(route);
  if (pubMatch && ['GET', 'PUT'].includes(req.method)) {
    const [, pubId, name, ordinal] = pubMatch,
      pub = await publication(env, pubId),
      token = req.headers.get('X-Financial-Lease'),
      digest = new URL(req.url).searchParams.get('manifestSha256');
    await leasedJob(env, pub.job_id, token);
    if (hash(digest) !== pub.manifest_hash) fail('ROOT_MISMATCH', '发布版本不匹配', 409);
    if (req.method === 'GET' && !name) return json(await publicationStatus(env, pub));
    if (req.method === 'PUT' && name)
      return json(
        await putChunk(
          env,
          pub,
          token,
          digest,
          name,
          Number(ordinal),
          await readBytes(req, LIMITS.chunkBytes)
        )
      );
  }
  fail('NOT_FOUND', '财务计算接口不存在', 404);
}
