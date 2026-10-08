import definitions from './definitions.json' with { type: 'json' };
import { body } from '../runtime.mjs';
import {
  ACTIVE,
  LIMITS,
  NOW,
  fail,
  object,
  string,
  integer,
  id,
  hash,
  date,
  parse,
  random,
  sha,
  readBytes,
  hashBytes,
  utf8,
  ownedInput,
  requireEnabled,
  requireRunner,
  storageUsage,
  inputDTO,
  jobDTO,
} from './common.mjs';
import { registryEntry, inputRegistry } from './registry.mjs';

export async function createInput(req, env, owner) {
  requireEnabled(env);
  const value = object(await body(req, 16384), [
    'requestId',
    'name',
    'byteLength',
    'calendarRef',
    'proofRefs',
  ]);
  id(value.requestId);
  string(value.name);
  integer(value.byteLength, 1, LIMITS.packageBytes);
  id(value.calendarRef);
  if (
    !Array.isArray(value.proofRefs) ||
    value.proofRefs.length > 256 ||
    new Set(value.proofRefs).size !== value.proofRefs.length
  )
    fail('INVALID_INPUT', '证明引用需要唯一且最多256项');
  value.proofRefs.forEach(id);
  const requestHash = await sha(JSON.stringify(value));
  const previous = await env.DB.prepare(
    'SELECT * FROM financial_inputs WHERE owner=? AND request_id=?'
  )
    .bind(owner, value.requestId)
    .first();
  if (previous) {
    if (previous.request_hash !== requestHash)
      fail('REQUEST_ID_CONFLICT', '请求标识已有其他内容', 409);
    return uploadReceipt(previous);
  }
  await inputRegistry(env, {
    owner,
    calendar_ref: value.calendarRef,
    proof_refs: JSON.stringify(value.proofRefs),
  });
  const inputId = random(),
    now = NOW();
  // Admission and insertion are one statement, so concurrent uploads share quota.
  const row = await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at)
 SELECT ?,?,?,'uploading',?,?,?,?,?,?,? WHERE
 (SELECT COUNT(*) FROM financial_inputs WHERE owner=? AND status='uploading')<3 AND
 COALESCE((SELECT SUM(declared_bytes) FROM financial_inputs WHERE owner=? AND parent_id IS NULL),0)+COALESCE((SELECT SUM(total_bytes) FROM financial_publications WHERE owner=?),0)+?<=? RETURNING *`
  )
    .bind(
      inputId,
      owner,
      value.name,
      value.calendarRef,
      JSON.stringify(value.proofRefs),
      value.byteLength,
      value.requestId,
      requestHash,
      now,
      now,
      owner,
      owner,
      owner,
      value.byteLength,
      LIMITS.workspaceRetainedFinancialBytes
    )
    .first();
  if (!row) {
    const raced = await env.DB.prepare(
      'SELECT * FROM financial_inputs WHERE owner=? AND request_id=?'
    )
      .bind(owner, value.requestId)
      .first();
    if (raced) {
      if (raced.request_hash !== requestHash)
        fail('REQUEST_ID_CONFLICT', '请求标识已有其他内容', 409);
      return uploadReceipt(raced);
    }
    fail('WORKSPACE_STORAGE_BUDGET', '工作区上传数量或存储空间已达限制', 413);
  }
  return uploadReceipt(row);
}
function uploadReceipt(row) {
  return {
    input: inputDTO(row),
    upload: {
      method: 'PUT',
      url: `/quant/api/financial/inputs/${row.id}/content`,
      maxBytes: LIMITS.packageBytes,
    },
  };
}
export async function uploadInput(req, env, owner, inputId) {
  requireEnabled(env);
  const row = await ownedInput(env, owner, inputId);
  const raw = await readBytes(req, LIMITS.packageBytes);
  if (raw.byteLength !== row.declared_bytes)
    fail('UPLOAD_LENGTH_MISMATCH', '实际文件大小与上传声明不同', 400);
  utf8(raw);
  const digest = await hashBytes(raw);
  if (row.source_hash) {
    if (row.source_hash !== digest) fail('INPUT_IMMUTABLE', '已上传内容不可覆盖，请新建输入', 409);
    return { input: inputDTO(row), idempotent: true };
  }
  if (row.status !== 'uploading') fail('INPUT_IMMUTABLE', '当前输入不可写入', 409);
  const key = `financial/${owner}/sources/${row.id}/${digest}.json`;
  await env.ARTIFACTS.put(key, raw, {
    httpMetadata: { contentType: 'application/json' },
  });
  const updated = await env.DB.prepare(
    "UPDATE financial_inputs SET source_key=?,source_hash=?,source_bytes=?,status='uploaded',updated_at=? WHERE id=? AND owner=? AND status='uploading' AND source_hash IS NULL RETURNING *"
  )
    .bind(key, digest, raw.length, NOW(), row.id, owner)
    .first();
  if (!updated) {
    const current = await ownedInput(env, owner, inputId);
    if (current.source_hash !== digest) {
      await env.ARTIFACTS.delete(key);
      fail('INPUT_IMMUTABLE', '另一请求已上传不同内容', 409);
    }
    return { input: inputDTO(current), idempotent: true };
  }
  return { input: inputDTO(updated) };
}
export function validateSelection(s) {
  object(s, [
    'symbols',
    'start',
    'end',
    'announcementStart',
    'selectedStateIds',
    'scope',
    'flowBasis',
  ]);
  if (
    !Array.isArray(s.symbols) ||
    !s.symbols.length ||
    s.symbols.length > 50 ||
    new Set(s.symbols).size !== s.symbols.length ||
    s.symbols.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x))
  )
    fail('INVALID_INPUT', '财务输入需1–50个唯一SH/SZ标的');
  for (const k of ['start', 'end', 'announcementStart']) date(s[k]);
  if (s.start > s.end || s.announcementStart > s.start) fail('INVALID_INPUT', '输入日期区间无效');
  if (
    !Array.isArray(s.selectedStateIds) ||
    !s.selectedStateIds.length ||
    s.selectedStateIds.length > 16 ||
    new Set(s.selectedStateIds).size !== s.selectedStateIds.length ||
    s.selectedStateIds.some((x) => !definitions.items.some((item) => item.id === x))
  )
    fail('INVALID_INPUT', '财务状态选择无效');
  if (
    !definitions.supportedSelection.scope.includes(s.scope) ||
    !definitions.supportedSelection.flowBasis.includes(s.flowBasis)
  )
    fail('INVALID_INPUT', '报表口径或期间基础未被核心支持');
  return s;
}
export async function queueOperation(req, env, owner, inputId, action) {
  const value = await body(req, action === 'revisions' ? 128 * 1024 : 4096);
  const allowed =
    action === 'validate'
      ? ['requestId', 'expectedUploadSha256']
      : action === 'prepare'
        ? ['requestId', 'expectedPackRoot']
        : ['requestId', 'expectedPackRoot', 'selection', 'unitPolicy', 'declarations'];
  object(value, allowed);
  id(value.requestId);
  hash(value[action === 'validate' ? 'expectedUploadSha256' : 'expectedPackRoot']);
  const requestHash = await sha(JSON.stringify({ inputId, action, value }));
  const old = await env.DB.prepare('SELECT * FROM financial_jobs WHERE owner=? AND request_id=?')
    .bind(owner, value.requestId)
    .first();
  if (old) {
    if (old.request_hash !== requestHash) fail('REQUEST_ID_CONFLICT', '请求标识已有其他任务', 409);
    return {
      input: inputDTO(await ownedInput(env, owner, old.input_id)),
      job: jobDTO(old),
      idempotent: true,
    };
  }
  await requireRunner(env);
  const input = await ownedInput(env, owner, inputId),
    roots = parse(input.roots, {});
  if (action === 'validate') {
    if (value.expectedUploadSha256 !== input.source_hash)
      fail('ROOT_MISMATCH', '源文件已改变', 409);
    if (input.status !== 'uploaded') fail('INPUT_NOT_VALIDATED', '当前源文件不能开始校验', 409);
  } else {
    if (!input.canonical_publication_id || !['ready_to_prepare', 'prepared'].includes(input.status))
      fail('INPUT_NOT_VALIDATED', '请先完成输入校验', 409);
    if (value.expectedPackRoot !== roots.packRoot) fail('ROOT_MISMATCH', '输入包版本不匹配', 409);
  }
  await inputRegistry(env, input);
  const jobId = random(),
    now = NOW();
  let target = input.id,
    spec = { ...value };
  delete spec.requestId;
  if (action === 'revisions') {
    validateSelection(value.selection);
    if (
      !['verified_only', 'allow_declared'].includes(value.unitPolicy) ||
      !Array.isArray(value.declarations) ||
      value.declarations.length > 64
    )
      fail('INVALID_INPUT', '单位声明或策略无效');
    for (const d of value.declarations) {
      object(d, ['fieldId', 'inputRoot', 'nativeUnit', 'currency', 'positiveOutflow', 'statement']);
      string(d.fieldId, 100);
      hash(d.inputRoot);
      if (d.inputRoot !== roots.inputRoot) fail('ROOT_MISMATCH', '单位声明不属于该源输入', 409);
      string(d.nativeUnit, 40);
      string(d.currency, 10);
      string(d.statement, 2000, 10);
      if (d.positiveOutflow !== null && typeof d.positiveOutflow !== 'boolean')
        fail('INVALID_INPUT', '现金流符号声明无效');
    }
    target = random();
    spec = { ...spec, parentId: input.id, declaredBy: owner, declaredAt: now };
  }
  const newInput = target !== input.id;
  const statements = [];
  if (newInput)
    statements.push(
      env.DB.prepare(
        "INSERT INTO financial_inputs(id,owner,name,status,revision,parent_id,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,'validating',?,?,?,?,0,?,?,?,?)"
      ).bind(
        target,
        owner,
        input.name,
        input.revision + 1,
        input.id,
        input.calendar_ref,
        input.proof_refs,
        value.requestId,
        requestHash,
        now,
        now
      )
    );
  statements.push(
    env.DB.prepare(
      "INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?,?,?,?)"
    ).bind(
      jobId,
      owner,
      target,
      action === 'validate'
        ? 'financial_validate'
        : action === 'prepare'
          ? 'financial_prepare'
          : 'financial_revise',
      JSON.stringify(spec),
      value.requestId,
      requestHash,
      now,
      now
    )
  );
  if (!newInput)
    statements.push(
      env.DB.prepare(
        'UPDATE financial_inputs SET status=?,updated_at=? WHERE id=? AND owner=?'
      ).bind(action === 'prepare' ? 'preparing' : 'validating', now, input.id, owner)
    );
  try {
    await env.DB.batch(statements);
  } catch (error) {
    const collision = await env.DB.prepare(
      'SELECT * FROM financial_jobs WHERE owner=? AND request_id=?'
    )
      .bind(owner, value.requestId)
      .first();
    if (collision && collision.request_hash === requestHash)
      return {
        input: inputDTO(await ownedInput(env, owner, collision.input_id)),
        job: jobDTO(collision),
        idempotent: true,
      };
    const active = await env.DB.prepare(
      "SELECT id FROM financial_jobs WHERE owner=? AND status IN ('queued','running','cancel_requested')"
    )
      .bind(owner)
      .first();
    if (active) fail('WORKSPACE_ACTIVE_TASK_LIMIT', '已有财务任务在运行', 429);
    throw error;
  }
  return {
    input: inputDTO(await ownedInput(env, owner, target)),
    job: jobDTO(
      await env.DB.prepare('SELECT * FROM financial_jobs WHERE id=?').bind(jobId).first()
    ),
  };
}
export async function inputDetail(env, owner, inputId) {
  id(inputId);
  // Completion updates these records together. Read one D1 snapshot so a
  // terminal job cannot be paired with a stale input that stops UI polling.
  const rows = await env.DB.batch([
    env.DB.prepare('SELECT * FROM financial_inputs WHERE id=? AND owner=?')
      .bind(inputId, owner),
    env.DB.prepare(
      'SELECT * FROM financial_jobs WHERE owner=? AND input_id=? ORDER BY created_at DESC,id DESC LIMIT 1'
    )
      .bind(owner, inputId),
    env.DB.prepare(
      'SELECT id,roots,metadata,created_at FROM financial_preparations WHERE owner=? AND input_id=? ORDER BY created_at DESC,id DESC LIMIT 1'
    )
      .bind(owner, inputId),
  ]);
  const [input, job, prep] = rows.map((row) => row.results[0]);
  if (!input) fail('NOT_FOUND', '财务输入不存在', 404);
  return {
    input: inputDTO(input),
    validation: parse(input.metadata, {})?.validation || null,
    activeJob: job && ACTIVE.includes(job.status) ? jobDTO(job) : null,
    latestJob: job ? jobDTO(job) : null,
    latestPreparation: prep
      ? {
          id: prep.id,
          ...parse(prep.roots, {}),
          ...parse(prep.metadata, {}),
          createdAt: prep.created_at,
        }
      : null,
  };
}
