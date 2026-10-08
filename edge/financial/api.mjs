import definitions from './definitions.json' with { type: 'json' };
import { body } from '../runtime.mjs';
import {
  LIMITS,
  CAPABILITY,
  json,
  fail,
  object,
  parse,
  id,
  hash,
  date,
  pageQuery,
  enabled,
  runnerInfo,
  ownedInput,
  ownedJob,
  inputDTO,
  jobDTO,
  fixedStream,
} from './common.mjs';
import { createInput, uploadInput, queueOperation, inputDetail } from './inputs.mjs';
import { cancelJob, expireJobs } from './jobs.mjs';
import { registryDTO, registryEntry } from './registry.mjs';
import { publication, collectionStream } from './publications.mjs';
import {
  ownedPreparation,
  preparationDTO,
  recordPage,
  eventDetail,
  eventRecord,
  eventDownload,
  attachment,
} from './read-model.mjs';

export async function financialApi(req, env, path, owner) {
  if (!path.startsWith('/financial/')) return null;
  const url = new URL(req.url),
    route = path.slice('/financial'.length);
  if (route === '/capabilities' && req.method === 'GET')
    return json({
      apiVersion: 'financial-workspace/v1',
      enabled: enabled(env),
      operations: {
        upload: enabled(env),
        validate: enabled(env),
        prepare: enabled(env),
        researchBinding: false,
      },
      runner: await runnerInfo(env),
      limits: LIMITS,
    });
  if (route === '/definitions' && req.method === 'GET') return json(definitions);
  if (route === '/calendars' && req.method === 'GET') {
    for (const k of url.searchParams.keys())
      if (!['page', 'pageSize', 'dateFrom', 'dateTo'].includes(k))
        fail('INVALID_INPUT', '不支持的日历筛选');
    const dateFrom = url.searchParams.get('dateFrom'),
      dateTo = url.searchParams.get('dateTo');
    if (dateFrom) date(dateFrom);
    if (dateTo) date(dateTo);
    if (dateFrom && dateTo && dateFrom > dateTo) fail('INVALID_INPUT', '日历筛选区间无效');
    const condition =
      "kind='calendar' AND status='active' AND (owner=? OR owner='*')" +
      (dateFrom ? " AND json_extract(metadata,'$.coverageStart')<=?" : '') +
      (dateTo ? " AND json_extract(metadata,'$.coverageEnd')>=?" : '');
    const params = [owner, ...(dateFrom ? [dateFrom] : []), ...(dateTo ? [dateTo] : [])];
    const p = pageQuery(url),
      rows = await env.DB.prepare(
        `SELECT * FROM financial_registry_entries WHERE ${condition} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?`
      )
        .bind(...params, p.pageSize, p.offset)
        .all(),
      total = (
        await env.DB.prepare(`SELECT COUNT(*) n FROM financial_registry_entries WHERE ${condition}`)
          .bind(...params)
          .first()
      ).n;
    return json({
      items: rows.results.map((r) => ({
        ...registryDTO(r),
        calendarRef: r.id,
      })),
      total,
      page: p.page,
      pageSize: p.pageSize,
    });
  }
  if (route === '/unit-proofs' && req.method === 'GET') {
    const input = await ownedInput(env, owner, url.searchParams.get('inputId')),
      refs = JSON.parse(input.proof_refs),
      p = pageQuery(url),
      items = [];
    for (const ref of refs.slice(p.offset, p.offset + p.pageSize))
      items.push(registryDTO(await registryEntry(env, owner, ref, 'unit_proof')));
    return json({
      items,
      total: refs.length,
      page: p.page,
      pageSize: p.pageSize,
    });
  }
  if (route === '/inputs') {
    if (req.method === 'POST') return json(await createInput(req, env, owner), 201);
    if (req.method === 'GET') {
      const p = pageQuery(url),
        rows = await env.DB.prepare(
          `SELECT i.*,(SELECT json_object('id',j.id,'kind',j.kind,'status',j.status,'createdAt',j.created_at) FROM financial_jobs j WHERE j.owner=i.owner AND j.input_id=i.id ORDER BY j.created_at DESC,j.id DESC LIMIT 1) latest_job FROM financial_inputs i WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?`
        )
          .bind(owner, p.pageSize, p.offset)
          .all(),
        total = (
          await env.DB.prepare('SELECT COUNT(*) n FROM financial_inputs WHERE owner=?')
            .bind(owner)
            .first()
        ).n;
      return json({
        items: rows.results.map((r) => ({
          ...inputDTO(r),
          latestJob: parse(r.latest_job),
        })),
        total,
        page: p.page,
        pageSize: p.pageSize,
      });
    }
  }
  const inputMatch =
    /^\/inputs\/([a-f0-9-]+)(?:\/(content|validate|prepare|revisions|download|source-download))?$/.exec(
      route
    );
  if (inputMatch) {
    const [, inputId, action] = inputMatch;
    if (action === 'content' && req.method === 'PUT')
      return json(await uploadInput(req, env, owner, inputId));
    if (['validate', 'prepare', 'revisions'].includes(action) && req.method === 'POST')
      return json(await queueOperation(req, env, owner, inputId, action), 202);
    if (req.method === 'GET') {
      if (!action) return json(await inputDetail(env, owner, inputId));
      const input = await ownedInput(env, owner, inputId);
      if (action === 'source-download') {
        if (hash(url.searchParams.get('uploadSha256')) !== input.source_hash)
          fail('ROOT_MISMATCH', '源文件版本不匹配', 409);
        const obj = await env.ARTIFACTS.get(input.source_key);
        if (!obj) fail('NOT_FOUND', '源文件不存在', 404);
        return attachment(
          fixedStream(obj.body, input.source_bytes),
          `financial-source-${input.id}.json`,
          { 'x-content-sha256': input.source_hash }
        );
      }
      if (action === 'download') {
        if (hash(url.searchParams.get('packRoot')) !== parse(input.roots, {}).packRoot)
          fail('ROOT_MISMATCH', '输入包版本不匹配', 409);
        if (!input.canonical_publication_id) fail('INPUT_NOT_VALIDATED', '尚无已校验输入包', 409);
        const pub = await publication(env, input.canonical_publication_id);
        if (pub.owner !== owner || pub.status !== 'committed')
          fail('NOT_FOUND', '输入包不存在', 404);
        return attachment(
          fixedStream(
            collectionStream(env, pub, 'package'),
            parse(pub.manifest_text).collections.package.byteLength
          ),
          `financial-package-${input.id}.json`,
          {
            'x-content-sha256': parse(pub.manifest_text).collections.package.sha256,
          }
        );
      }
    }
  }
  const jobMatch = /^\/jobs\/([a-f0-9-]+)(?:\/(cancel))?$/.exec(route);
  if (jobMatch) {
    if (req.method === 'GET' && !jobMatch[2]) {
      await expireJobs(env);
      return json({ job: jobDTO(await ownedJob(env, owner, jobMatch[1])) });
    }
    if (req.method === 'POST' && jobMatch[2]) {
      object(await body(req, 1024), [], []);
      return json(await cancelJob(env, owner, jobMatch[1]));
    }
  }
  const prepMatch =
    /^\/preparations\/([a-f0-9-]+)(?:\/(coverage|events)(?:\/([a-f0-9]{64})(?:\/(dependencies|download))?)?)?$/.exec(
      route
    );
  if (prepMatch && req.method === 'GET') {
    const [, prepId, collection, eventId, action] = prepMatch,
      prep = await ownedPreparation(env, owner, prepId);
    if (!collection) return json(preparationDTO(prep));
    if (eventId) {
      if (hash(url.searchParams.get('preparedRoot')) !== parse(prep.roots).preparedRoot)
        fail('ROOT_MISMATCH', '准备结果版本不匹配', 409);
      if (action === 'download') return eventDownload(env, prep, eventId);
      if (action === 'dependencies') {
        await eventRecord(env, prep, eventId);
        return json(await recordPage(env, owner, prep, url, 'dependencies', eventId));
      }
      return json(await eventDetail(env, prep, eventId));
    }
    return json(await recordPage(env, owner, prep, url, collection));
  }
  fail('NOT_FOUND', '财务工作区接口不存在', 404);
}
