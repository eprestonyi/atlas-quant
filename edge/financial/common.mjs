import { ApiError } from '../errors.mjs';
import { NOW, json, parse, random, sha } from '../runtime.mjs';
export { ApiError, NOW, json, parse, random, sha };
export const CAPABILITY = 'financial-input/v1';
export const LIMITS = Object.freeze({
  packageBytes: 24 * 1024 * 1024,
  snapshots: 128,
  sourceRows: 20000,
  bindings: 20000,
  calendarSessions: 10000,
  selectedStates: 16,
  panelRows: 110000,
  preparedBytes: 64 * 1024 * 1024,
  pageSize: 25,
  maxPageSize: 100,
  pageBytes: 256 * 1024,
  chunkBytes: 512 * 1024,
  manifestBytes: 256 * 1024,
  registryEntryBytes: 256 * 1024,
  registryTotalBytes: 32 * 1024 * 1024,
  maxActiveTasksPerWorkspace: 1,
  maxPendingUploadsPerWorkspace: 3,
  workspaceRetainedFinancialBytes: 256 * 1024 * 1024,
});
export const ACTIVE = ['queued', 'running', 'cancel_requested'];
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const HASH = /^[0-9a-f]{64}$/;
export const enabled = (env) => env.FINANCIAL_WORKSPACE_ENABLED === 'true';
export const fail = (code, message, status = 400) => {
  throw new ApiError(code, message, status);
};
export function object(value, keys, required = keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    fail('INVALID_INPUT', '请求须为对象');
  if (Object.keys(value).some((k) => !keys.includes(k)))
    fail('UNKNOWN_PROPERTY', '请求含未支持的字段');
  if (required.some((k) => !Object.hasOwn(value, k))) fail('INVALID_INPUT', '缺少必需字段');
  return value;
}
export function string(value, max = 80, min = 1) {
  if (typeof value !== 'string' || value.length < min || value.length > max)
    fail('INVALID_INPUT', '文本长度超出范围');
  return value;
}
export function integer(value, min, max) {
  if (!Number.isInteger(value) || value < min || value > max)
    fail('INVALID_INPUT', '数值超出允许范围');
  return value;
}
export function id(value) {
  if (typeof value !== 'string' || !UUID.test(value)) fail('INVALID_INPUT', '标识格式无效');
  return value;
}
export function hash(value) {
  if (typeof value !== 'string' || !HASH.test(value)) fail('INVALID_INPUT', '内容哈希无效');
  return value;
}
export function date(value) {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) fail('INVALID_INPUT', '日期需 YYYYMMDD');
  const y = Number(value.slice(0, 4)),
    m = Number(value.slice(4, 6)),
    d = Number(value.slice(6));
  const check = new Date(Date.UTC(y, m - 1, d));
  if (
    y < 1900 ||
    y > 2200 ||
    check.getUTCFullYear() !== y ||
    check.getUTCMonth() !== m - 1 ||
    check.getUTCDate() !== d
  )
    fail('INVALID_INPUT', '日期不是有效日历日期');
  return value;
}
export const bytes = (value) =>
  new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value));
export async function hashBytes(raw) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))]
    .map((x) => x.toString(16).padStart(2, '0'))
    .join('');
}
export async function readBytes(request, max) {
  const declared = Number(request.headers.get('content-length') || 0);
  if (declared > max) fail('PACKAGE_BYTE_BUDGET', '内容超过大小限制', 413);
  const reader = request.body?.getReader(),
    parts = [];
  let length = 0;
  try {
    if (reader)
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > max) {
          await reader.cancel();
          fail('PACKAGE_BYTE_BUDGET', '内容超过大小限制', 413);
        }
        parts.push(value);
      }
  } finally {
    reader?.releaseLock();
  }
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}
export function utf8(raw) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(raw);
  } catch {
    fail('INVALID_JSON', '内容不是有效UTF-8');
  }
}
export function requireEnabled(env) {
  if (!enabled(env)) fail('FINANCIAL_CAPABILITY_UNAVAILABLE', '财务工作区尚未启用', 503);
}
export async function ownedInput(env, owner, inputId) {
  const r = await env.DB.prepare('SELECT * FROM financial_inputs WHERE id=? AND owner=?')
    .bind(id(inputId), owner)
    .first();
  if (!r) fail('NOT_FOUND', '输入不存在', 404);
  return r;
}
export async function ownedJob(env, owner, jobId) {
  const r = await env.DB.prepare('SELECT * FROM financial_jobs WHERE id=? AND owner=?')
    .bind(id(jobId), owner)
    .first();
  if (!r) fail('NOT_FOUND', '任务不存在', 404);
  return r;
}
export function jobDTO(row) {
  return {
    id: row.id,
    kind: row.kind,
    status: row.status,
    inputId: row.input_id,
    phase: row.phase,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    error: parse(row.error),
    resultRef: row.publication_id ? { publicationId: row.publication_id } : null,
  };
}
export function inputDTO(row) {
  return {
    id: row.id,
    name: row.name,
    status: row.status,
    revision: row.revision,
    parentId: row.parent_id,
    uploadSha256: row.source_hash,
    byteLength: row.source_bytes,
    calendarRef: row.calendar_ref,
    ...parse(row.roots, {}),
    ...parse(row.metadata, {}),
    error: parse(row.error),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}
export async function runnerInfo(env) {
  const r = await env.DB.prepare(
      "SELECT value,updated_at FROM meta WHERE key='financial_runner'"
    ).first(),
    v = parse(r?.value, {});
  return {
    online: !!r && Date.now() - Date.parse(r.updated_at) < 120000 && v.capability === CAPABILITY,
    capability: v.capability || null,
    lastSeen: r?.updated_at || null,
  };
}
export async function requireRunner(env) {
  requireEnabled(env);
  if (!(await runnerInfo(env)).online)
    fail('FINANCIAL_RUNNER_OFFLINE', '财务准备服务暂未在线；输入已保留', 503);
}
export async function storageUsage(env, owner) {
  const r = await env.DB.prepare(
    'SELECT COALESCE((SELECT SUM(declared_bytes) FROM financial_inputs WHERE owner=? AND parent_id IS NULL),0)+COALESCE((SELECT SUM(total_bytes) FROM financial_publications WHERE owner=?),0) AS n'
  )
    .bind(owner, owner)
    .first();
  return Number(r.n);
}
export function pageQuery(url) {
  const page = Number(url.searchParams.get('page') || 1),
    size = Number(url.searchParams.get('pageSize') || 25);
  integer(page, 1, 10000);
  integer(size, 1, 100);
  return { page, pageSize: size, offset: (page - 1) * size };
}

/** Use Workers' fixed stream so a truncated private download cannot look complete. */
export function fixedStream(source, length) {
  const fixed = new FixedLengthStream(length);
  source.pipeTo(fixed.writable).catch(() => {});
  return fixed.readable;
}
