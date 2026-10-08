/** Receipts remain immutable across jobs; cache identity includes authorization. */
import { NOW, random } from "../runtime.mjs";
import { object, integer, string, hashBytes } from "../financial/common.mjs";
import { id, hash, fail, canonical } from "./common.mjs";
import { MARKET_LIMITS } from "./planner.mjs";
import { leased, loadPlan, requireEnabled } from "./queue.mjs";
export async function requestFor(env, job, key) {
  hash(key);
  const { plan } = await loadPlan(env, job.owner, job.plan_id, job.plan_root);
  const request = plan.requests.find((r) => r.requestKey === key);
  if (!request) fail("NOT_FOUND", "计划未授权此市场请求", 404);
  return request;
}
export function receiptKind(env, kind) {
  if (
    kind !== "provider" &&
    !(kind === "fixture" && env.ALLOW_MARKET_FIXTURES === "true")
  )
    fail("MARKET_SOURCE_KIND", "生产环境拒绝合成回执", 409);
}
export const receiptView = (r) => ({
  receiptId: r.id,
  requestKey: r.request_key,
  sha256: r.sha256,
  byteLength: r.byte_length,
  httpStatus: r.http_status,
  retrievedAt: r.retrieved_at,
  sourceKind: r.source_kind,
});
export async function linkReceipt(env, job, row) {
  const now = NOW();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_market_job_receipts(job_id,request_key,receipt_id,byte_length)
 SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND lease_token=? AND status IN('running','cancel_requested') AND lease_until>=? AND deadline>=?)
 AND COALESCE((SELECT SUM(byte_length) FROM quant_market_job_receipts WHERE job_id=?),0)+?<=?`,
  )
    .bind(
      job.id,
      row.request_key,
      row.id,
      row.byte_length,
      job.id,
      job.lease_token,
      now,
      now,
      job.id,
      row.byte_length,
      MARKET_LIMITS.maxRawBytes,
    )
    .run();
  const found = await env.DB.prepare(
    "SELECT receipt_id FROM quant_market_job_receipts WHERE job_id=? AND request_key=?",
  )
    .bind(job.id, row.request_key)
    .first();
  if (found?.receipt_id !== row.id)
    fail("MARKET_RAW_BUDGET", "回执总量超限或租约终止", 409);
}
export async function getReceipt(env, job, key) {
  const request = await requestFor(env, job, key);
  const row = await env.DB.prepare(
    "SELECT c.* FROM quant_market_receipts c JOIN quant_market_requests r ON r.receipt_id=c.id WHERE c.request_key=? AND c.authorization_scope=? AND r.state='received'",
  )
    .bind(key, request.authorizationScope)
    .first();
  if (!row) fail("NOT_FOUND", "市场回执尚不存在", 404);
  receiptKind(env, row.source_kind);
  return row;
}
export async function begin(env, jobId, key, v) {
  object(v, ["leaseToken", "attemptId"]);
  id(v.attemptId);
  const job = await leased(env, jobId, v.leaseToken);
  requireEnabled(env, job);
  if (job.status !== "running") fail("CANCELLED", "准备已取消", 409);
  const request = await requestFor(env, job, key);
  const now = NOW(),
    day = now.slice(0, 10) + "T00:00:00.000Z",
    next = new Date(Date.parse(day) + 86400000).toISOString();
  const def = Object.fromEntries(
    Object.entries(request).filter(
      ([k]) => !["ordinal", "requestKey"].includes(k),
    ),
  );
  const result = await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_market_requests(request_key,authorization_scope,definition,job_id,attempt_id,state,created_at,updated_at)
 SELECT ?,?,?,?,?,'intent',?,? WHERE EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?)
 AND (SELECT count(*) FROM quant_market_requests WHERE created_at>=? AND created_at<?)<6004`,
  )
    .bind(
      key,
      request.authorizationScope,
      canonical(def),
      job.id,
      v.attemptId,
      now,
      now,
      job.id,
      job.lease_token,
      now,
      now,
      day,
      next,
    )
    .run();
  const row = await env.DB.prepare(
    "SELECT * FROM quant_market_requests WHERE request_key=?",
  )
    .bind(key)
    .first();
  if (!row) fail("MARKET_REQUEST_BUDGET", "市场意图预算已满或任务终止", 429);
  if (
    row.definition !== canonical(def) ||
    row.authorization_scope !== job.authorization_scope
  )
    fail("MARKET_REQUEST_INTEGRITY", "请求身份不匹配", 409);
  if (row.state === "received") {
    const receipt = await getReceipt(env, job, key);
    await linkReceipt(env, job, receipt);
    return { state: "received", receiptId: receipt.id, maySend: false };
  }
  if (
    row.state !== "intent" ||
    row.job_id !== job.id ||
    row.attempt_id !== v.attemptId
  )
    fail(
      "REQUEST_REQUIRES_REVIEW",
      "来源请求结果未知或进行中；禁止重复读取",
      409,
    );
  return {
    state: "intent",
    attemptId: row.attempt_id,
    maySend: !!result.meta.changes,
  };
}
export async function unknown(env, jobId, key, v) {
  object(v, ["leaseToken", "attemptId", "reason"]);
  id(v.attemptId);
  string(v.reason, 700);
  const job = await leased(env, jobId, v.leaseToken, true);
  await requestFor(env, job, key);
  await env.DB.prepare(
    "UPDATE quant_market_requests SET state='outcome_unknown',error=?,updated_at=? WHERE request_key=? AND job_id=? AND attempt_id=? AND state='intent'",
  )
    .bind(
      JSON.stringify({ code: "PROVIDER_OUTCOME_UNKNOWN", message: v.reason }),
      NOW(),
      key,
      job.id,
      v.attemptId,
    )
    .run();
  const row = await env.DB.prepare(
    "SELECT state FROM quant_market_requests WHERE request_key=? AND job_id=? AND attempt_id=?",
  )
    .bind(key, job.id, v.attemptId)
    .first();
  if (!row) fail("NOT_FOUND", "市场意图不存在", 404);
  return {
    state: row.state,
    manualReviewRequired: row.state === "outcome_unknown",
  };
}
export async function putReceipt(env, jobId, key, token, meta, raw) {
  object(meta, [
    "attemptId",
    "sha256",
    "byteLength",
    "httpStatus",
    "retrievedAt",
    "sourceKind",
  ]);
  id(meta.attemptId);
  hash(meta.sha256);
  integer(meta.byteLength, 0, 256 * 1024);
  integer(meta.httpStatus, 100, 599);
  string(meta.retrievedAt, 40);
  receiptKind(env, meta.sourceKind);
  if (
    !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(meta.retrievedAt) ||
    !Number.isFinite(Date.parse(meta.retrievedAt))
  )
    fail("MARKET_RECEIPT", "回执时间无效");
  const job = await leased(env, jobId, token),
    request = await requestFor(env, job, key);
  if (
    raw.length !== meta.byteLength ||
    raw.length > request.responseBytes ||
    (await hashBytes(raw)) !== meta.sha256
  )
    fail("MARKET_RECEIPT_INTEGRITY", "回执字节与声明不符", 409);
  const intent = await env.DB.prepare(
    "SELECT * FROM quant_market_requests WHERE request_key=?",
  )
    .bind(key)
    .first();
  if (
    !intent ||
    intent.job_id !== job.id ||
    intent.attempt_id !== meta.attemptId
  )
    fail("NOT_FOUND", "原市场意图不存在", 404);
  const matches = (r) =>
    r.sha256 === meta.sha256 &&
    r.byte_length === meta.byteLength &&
    r.http_status === meta.httpStatus &&
    r.retrieved_at === meta.retrievedAt &&
    r.source_kind === meta.sourceKind;
  if (intent.state === "received") {
    const old = await getReceipt(env, job, key);
    if (!matches(old)) fail("MARKET_RECEIPT_IMMUTABLE", "回执不能修改", 409);
    await linkReceipt(env, job, old);
    return receiptView(old);
  }
  if (intent.state !== "intent")
    fail("REQUEST_REQUIRES_REVIEW", "未知结果不能自动覆盖", 409);
  const rid = random(),
    objectKey = `market/receipts/${key}/${meta.sha256}`,
    now = NOW();
  await env.ARTIFACTS.put(objectKey, raw, {
    httpMetadata: { contentType: "application/octet-stream" },
  });
  await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO quant_market_receipts(id,request_key,authorization_scope,object_key,sha256,byte_length,http_status,retrieved_at,source_kind,created_at)
 SELECT ?,?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_market_requests WHERE request_key=? AND job_id=? AND attempt_id=? AND state='intent')
 AND EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND lease_token=? AND status IN('running','cancel_requested') AND lease_until>=? AND deadline>=?)`,
    ).bind(
      rid,
      key,
      request.authorizationScope,
      objectKey,
      meta.sha256,
      raw.length,
      meta.httpStatus,
      meta.retrievedAt,
      meta.sourceKind,
      now,
      key,
      job.id,
      meta.attemptId,
      job.id,
      token,
      now,
      now,
    ),
    env.DB.prepare(
      "UPDATE quant_market_requests SET state='received',receipt_id=(SELECT id FROM quant_market_receipts WHERE request_key=?),updated_at=? WHERE request_key=? AND state='intent' AND EXISTS(SELECT 1 FROM quant_market_receipts WHERE request_key=?)",
    ).bind(key, now, key, key),
  ]);
  const saved = await getReceipt(env, job, key);
  if (!matches(saved))
    fail("MARKET_RECEIPT_IMMUTABLE", "并发回执内容不一致", 409);
  await linkReceipt(env, job, saved);
  return receiptView(saved);
}
export async function readReceipt(env, row) {
  const obj = await env.ARTIFACTS.get(row.object_key);
  if (!obj || obj.size !== row.byte_length || obj.size > 256 * 1024)
    fail("MARKET_RECEIPT_INTEGRITY", "市场原始回执缺失", 409);
  const raw = new Uint8Array(await obj.arrayBuffer());
  if ((await hashBytes(raw)) !== row.sha256)
    fail("MARKET_RECEIPT_INTEGRITY", "市场原始回执损坏", 409);
  return raw;
}
