import { acquisitionAdmission } from "./admission.mjs";
import { random, hash } from "../financial/common.mjs";
import { createPlanSpec, digest } from "./planner.mjs";
import {
  requireAcquisition,
  authorizationScope,
  object,
  id,
  fail,
  NOW,
  parse,
  planDTO,
  jobDTO,
  ownedPlan,
  cacheState,
  runnerInfo,
} from "./common.mjs";

export async function createPlan(env, owner, value) {
  requireAcquisition(env, owner);
  const spec = await createPlanSpec(value, authorizationScope(env));
  const requestHash = await digest(value);
  let row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_plans WHERE owner=? AND request_id=?",
  )
    .bind(owner, value.requestId)
    .first();
  if (row) {
    if (row.request_hash !== requestHash)
      fail("REQUEST_ID_CONFLICT", "请求标识已有其他计划", 409);
    return { plan: planDTO(row), idempotent: true };
  }
  const requests = [];
  for (const request of spec.requests)
    requests.push({ ...request, cache: await cacheState(env, request) });
  const plan = {
    ...spec,
    requests,
    budget: {
      ...spec.budget,
      cachedRequests: requests.filter((x) => x.cache.status === "frozen")
        .length,
      newRequests: requests.filter((x) => x.cache.status === "missing").length,
    },
    blockedReasons: [
      ...new Set(
        requests
          .filter((x) => !["frozen", "missing"].includes(x.cache.status))
          .map((x) => x.cache.reasonCode),
      ),
    ],
  };
  // The displayed frozen cache choices are part of the reviewed plan identity.
  delete plan.planRoot;
  plan.planRoot = await digest(plan);
  const now = NOW(),
    planId = random();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_acquisition_plans
    (id,owner,request_id,request_hash,plan_root,spec,created_at) SELECT ?,?,?,?,?,?,? WHERE (SELECT COUNT(*) FROM financial_acquisition_plans WHERE owner=?)<200`,
  )
    .bind(
      planId,
      owner,
      value.requestId,
      requestHash,
      plan.planRoot,
      JSON.stringify(plan),
      now,
      owner,
    )
    .run();
  row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_plans WHERE owner=? AND request_id=?",
  )
    .bind(owner, value.requestId)
    .first();
  if (!row) fail("ACQUISITION_PLAN_BUDGET", "工作区最多保留200份获取计划", 413);
  if (row.request_hash !== requestHash)
    fail("REQUEST_ID_CONFLICT", "请求标识已有其他计划", 409);
  return { plan: planDTO(row) };
}
export async function startPlan(env, owner, planId, value) {
  requireAcquisition(env, owner);
  object(value, ["requestId", "expectedPlanRoot"]);
  id(value.requestId);
  hash(value.expectedPlanRoot);
  const source = await ownedPlan(env, owner, planId),
    plan = parse(source.spec);
  if (source.plan_root !== value.expectedPlanRoot)
    fail("ROOT_MISMATCH", "计划已改变，请重新核对", 409);
  if (
    plan.requests.some((x) => x.authorizationScope !== authorizationScope(env))
  )
    fail(
      "ACQUISITION_SCOPE_CHANGED",
      "数据授权范围已改变，请重新建立计划",
      409,
    );
  const requestHash = await digest({ planId, ...value });
  let old = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_jobs WHERE owner=? AND request_id=?",
  )
    .bind(owner, value.requestId)
    .first();
  if (old) {
    if (old.request_hash !== requestHash)
      fail("REQUEST_ID_CONFLICT", "请求标识已有其他任务", 409);
    return { job: jobDTO(old), idempotent: true };
  }
  if (!(await runnerInfo(env)).online)
    fail(
      "ACQUISITION_RUNNER_OFFLINE",
      "来源获取服务暂未在线；已核对计划保留",
      503,
    );
  if (plan.blockedReasons.length)
    fail("REQUEST_REQUIRES_REVIEW", "存在未核清请求，不能自动再次读取", 409);
  const requests = [];
  for (const request of plan.requests) {
    const current = await cacheState(env, request);
    if (!["frozen", "missing"].includes(current.status))
      fail("REQUEST_REQUIRES_REVIEW", "同一来源请求正进行或需人工核对", 409);
    if (
      request.cache.status === "frozen" &&
      (current.status !== "frozen" || current.sha256 !== request.cache.sha256)
    )
      fail("CACHE_CHANGED", "已核对的冻结响应不再可读", 409);
    requests.push({ ...request, cache: current });
  }
  const spec = {
    ...plan,
    requests,
    executionPlanRoot: await digest({ planRoot: plan.planRoot, requests }),
  };
  const now = NOW(),
    jobId = random();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_acquisition_jobs
    (id,owner,plan_id,request_id,request_hash,spec,status,created_at,updated_at)
    SELECT ?,?,?,?,?,?,'queued',?,? WHERE NOT EXISTS(SELECT 1 FROM financial_acquisition_jobs
      WHERE owner=? AND status IN ('queued','running','cancel_requested')) AND (SELECT COUNT(*) FROM financial_acquisition_jobs WHERE status IN ('queued','running','cancel_requested'))<? AND NOT EXISTS(SELECT 1 FROM meta WHERE key='financial_acquisition_maintenance' AND value='paused')`,
  )
    .bind(
      jobId,
      owner,
      source.id,
      value.requestId,
      requestHash,
      JSON.stringify(spec),
      now,
      now,
      owner,
      acquisitionAdmission(env).maxActive,
    )
    .run();
  old = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_jobs WHERE owner=? AND request_id=?",
  )
    .bind(owner, value.requestId)
    .first();
  if (!old)
    fail("ACQUISITION_QUEUE_BUSY", "工作区已有获取任务或全局获取队列已满", 409);
  if (old.request_hash !== requestHash)
    fail("REQUEST_ID_CONFLICT", "请求标识已有其他任务", 409);
  return { job: jobDTO(old) };
}
