import { LEGACY_DATASET, assertContext } from './context.mjs';
/** Shared immutable identities and ceilings for the provider-free hosted path. */
import protocol from '../../contracts/hosted-datasets-v1.json' with { type: 'json' };
export { protocol };
export { NOW, parse, random, sha, json } from '../runtime.mjs';
export {
  object,
  id,
  hash,
  string,
  integer,
  date,
  fail,
  bytes,
  hashBytes,
  readBytes,
  utf8
} from '../financial/common.mjs';
import { fail, id, hash } from '../financial/common.mjs';
import { parse } from '../runtime.mjs';
export const CAPABILITY = protocol.protocol,
  PROFILE = protocol.profile,
  LIMITS = protocol.limits;
export const enabled = (env, context = LEGACY_DATASET) =>
  env[assertContext(context).flag] === 'true';
export function requireEnabled(env, context = LEGACY_DATASET) {
  if (!enabled(env, context)) fail('DATASET_CAPABILITY_UNAVAILABLE', '研究数据集尚未开放', 503);
}
export function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object')
    return (
      '{' +
      Object.keys(value)
        .sort()
        .map((k) => JSON.stringify(k) + ':' + canonical(value[k]))
        .join(',') +
      '}'
    );
  return JSON.stringify(value);
}
export async function owned(env, table, owner, value) {
  const tables = ['quant_dataset_plans', 'quant_dataset_jobs', 'quant_research_datasets'];
  if (!tables.includes(table)) throw Error('Unregistered table');
  const row = await env.DB.prepare(`SELECT * FROM ${table} WHERE id=? AND owner=?`)
    .bind(id(value), owner)
    .first();
  if (!row) fail('NOT_FOUND', '研究数据集记录不存在', 404);
  return row;
}
export function datasetRef(row, context = LEGACY_DATASET) {
  assertContext(context);
  return {
    datasetId: row.id,
    datasetRoot: row.dataset_root,
    format: protocol.datasetFormat,
    version: context.version
  };
}
export function jobDTO(row) {
  return {
    id: row.id,
    planId: row.plan_id,
    status: row.status,
    phase: row.phase,
    error: parse(row.error),
    datasetId: row.dataset_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
export async function runnerInfo(env, context = LEGACY_DATASET) {
  assertContext(context);
  const row = await env.DB.prepare('SELECT value,updated_at FROM meta WHERE key=?')
      .bind(context.metaKey)
      .first(),
    v = parse(row?.value, {});
  return {
    online:
      !!row &&
      v.capability === context.capability &&
      Date.now() - Date.parse(row.updated_at) < 120000,
    capability: v.capability || null,
    lastSeen: row?.updated_at || null
  };
}
export async function readObject(env, key, expectedHash, expectedBytes, max) {
  hash(expectedHash);
  if (!Number.isInteger(expectedBytes) || expectedBytes < 1 || expectedBytes > max)
    fail('DATASET_BUDGET', '来源描述超过大小限制', 413);
  const obj = await env.ARTIFACTS.get(key);
  if (!obj || obj.size !== expectedBytes) fail('DATASET_SOURCE_INTEGRITY', '来源内容不可读取', 409);
  const raw = new Uint8Array(await obj.arrayBuffer());
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))]
    .map((x) => x.toString(16).padStart(2, '0'))
    .join('');
  if (digest !== expectedHash) fail('DATASET_SOURCE_INTEGRITY', '来源内容哈希不匹配', 409);
  return raw;
}
