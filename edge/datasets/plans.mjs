import { LEGACY_DATASET, assertPlanContext } from './context.mjs';
import {
  protocol,
  PROFILE,
  LIMITS,
  requireEnabled,
  object,
  id,
  hash,
  string,
  fail,
  canonical,
  sha,
  NOW,
  random,
  parse,
  owned,
  runnerInfo,
  jobDTO
} from './common.mjs';
import { resolveSources, sourceDependencies } from './sources.mjs';
export function planDTO(row, context = LEGACY_DATASET) {
  const s = parse(row.spec);
  assertPlanContext(s, context);
  return {
    id: row.id,
    name: row.name,
    planRoot: row.plan_root,
    profile: context.profile,
    marketSource: s.request.marketSource,
    financialInputs: s.request.financialInputs,
    originalScope: s.sources.market.originalScope,
    scope: s.sources.market.scope,
    marketCalendarRef: s.sources.marketCalendarRef,
    selectedStates: [...new Set(s.sources.financial.flatMap((x) => x.selection.selectedStateIds))],
    knownSourceBytes: s.sources.knownSourceBytes,
    limits: LIMITS,
    checks: { metadata: 'passed', semantic: 'pending' },
    createdAt: row.created_at
  };
}
export async function createPlan(env, owner, value, context = LEGACY_DATASET) {
  requireEnabled(env, context);
  object(value, protocol.publicPlanKeys);
  id(value.requestId);
  string(value.name, 100);
  if (value.profile !== context.profile) fail('DATASET_PROFILE', '不支持的数据集组成口径');
  if (
    !Array.isArray(value.financialInputs) ||
    value.financialInputs.length < 1 ||
    value.financialInputs.length > LIMITS.financialInputs
  )
    fail('DATASET_SOURCE', '请选择1–8个已完成的财务准备');
  const requestHash = await sha(canonical(value));
  let previous = await env.DB.prepare(
    'SELECT * FROM quant_dataset_plans WHERE owner=? AND request_id=?'
  )
    .bind(owner, value.requestId)
    .first();
  if (previous) {
    if (previous.request_hash !== requestHash)
      fail('REQUEST_ID_CONFLICT', '请求标识已有其他计划', 409);
    return { plan: planDTO(previous, context), idempotent: true };
  }
  const sources = await resolveSources(env, owner, value, context);
  if (sources.knownSourceBytes + LIMITS.manifestBytes >= LIMITS.closureBytes)
    fail('DATASET_BUDGET', '已知原始来源已超过完整闭包预算', 413);
  const spec = { profile: context.profile, request: value, sources },
    planRoot = await sha(canonical(spec)),
    planId = random(),
    now = NOW();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_dataset_plans(id,owner,request_id,request_hash,plan_root,name,spec,created_at) SELECT ?,?,?,?,?,?,?,? WHERE (SELECT COUNT(*) FROM quant_dataset_plans WHERE owner=?)<200`
  )
    .bind(
      planId,
      owner,
      value.requestId,
      requestHash,
      planRoot,
      value.name,
      JSON.stringify(spec),
      now,
      owner
    )
    .run();
  previous = await env.DB.prepare(
    'SELECT * FROM quant_dataset_plans WHERE owner=? AND request_id=?'
  )
    .bind(owner, value.requestId)
    .first();
  if (!previous) fail('DATASET_PLAN_BUDGET', '工作区最多保留200份组成计划', 413);
  if (previous.request_hash !== requestHash)
    fail('REQUEST_ID_CONFLICT', '请求标识已有其他计划', 409);
  return { plan: planDTO(previous, context) };
}
export async function startPlan(env, owner, planId, value, context = LEGACY_DATASET) {
  requireEnabled(env, context);
  object(value, ['requestId', 'expectedPlanRoot']);
  id(value.requestId);
  hash(value.expectedPlanRoot);
  const plan = await owned(env, 'quant_dataset_plans', owner, planId);
  assertPlanContext(parse(plan.spec), context);
  if (value.expectedPlanRoot !== plan.plan_root) fail('ROOT_MISMATCH', '计划版本不匹配', 409);
  const requestHash = await sha(canonical({ planId, ...value }));
  let old = await env.DB.prepare('SELECT * FROM quant_dataset_jobs WHERE owner=? AND request_id=?')
    .bind(owner, value.requestId)
    .first();
  if (old) {
    if (old.request_hash !== requestHash) fail('REQUEST_ID_CONFLICT', '请求标识已有其他任务', 409);
    return { preparation: jobDTO(old), idempotent: true };
  }
  if (!(await runnerInfo(env, context)).online)
    fail('DATASET_RUNNER_UNAVAILABLE', '数据集组成服务暂未在线；计划已保留', 503);
  const spec = parse(plan.spec),
    fresh = await resolveSources(env, owner, spec.request, context);
  if (canonical(fresh) !== canonical(spec.sources))
    fail('DATASET_SOURCE_CHANGED', '来源或授权证据已变化，请重新核对计划', 409);
  const jobId = random(),
    now = NOW(),
    deps = sourceDependencies(fresh);
  await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_dataset_jobs(id,owner,plan_id,request_id,request_hash,status,created_at,updated_at) SELECT ?,?,?,?,?,'queued',?,? WHERE NOT EXISTS(SELECT 1 FROM quant_dataset_jobs WHERE owner=? AND status IN ('queued','running','cancel_requested')) AND (SELECT COUNT(*) FROM quant_dataset_jobs WHERE status IN ('queued','running','cancel_requested'))<8 AND NOT EXISTS(SELECT 1 FROM meta WHERE key IN ('dataset_maintenance','${context.maintenanceKey}') AND value='paused') AND COALESCE((SELECT SUM(total_bytes) FROM quant_dataset_stages WHERE owner=?),0)+?<=268435456 AND NOT EXISTS(SELECT 1 FROM json_each(?) p WHERE NOT EXISTS(SELECT 1 FROM financial_registry_entries r WHERE r.id=json_extract(p.value,'$.ref') AND r.owner=? AND r.status='active' AND r.sha256=json_extract(p.value,'$.sha256')))`
    ).bind(
      jobId,
      owner,
      planId,
      value.requestId,
      requestHash,
      now,
      now,
      owner,
      owner,
      LIMITS.closureBytes,
      JSON.stringify(fresh.registry),
      owner
    ),
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_dataset_dependencies(job_id,owner,kind,reference_id,content_hash) SELECT ?,?,json_extract(p.value,'$.kind'),json_extract(p.value,'$.referenceId'),json_extract(p.value,'$.contentHash') FROM json_each(?) p WHERE EXISTS(SELECT 1 FROM quant_dataset_jobs WHERE id=? AND owner=?)`
    ).bind(jobId, owner, JSON.stringify(deps), jobId, owner)
  ]);
  old = await env.DB.prepare('SELECT * FROM quant_dataset_jobs WHERE owner=? AND request_id=?')
    .bind(owner, value.requestId)
    .first();
  if (!old)
    fail('DATASET_QUEUE_BLOCKED', '工作区或全局队列、保留空间、维护或证据授权阻止新增任务', 409);
  if (old.request_hash !== requestHash) fail('REQUEST_ID_CONFLICT', '请求标识已有其他任务', 409);
  return { preparation: jobDTO(old) };
}
