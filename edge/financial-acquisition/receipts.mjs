import { acquisitionAdmission, utcWindow, budgetState } from "./admission.mjs";
/** One durable logical intent precedes any provider call. UNKNOWN never retries. */
import {
  hash,
  hashBytes,
  integer,
  string,
  random,
  utf8,
} from "../financial/common.mjs";
import {
  requireAcquisition,
  authorizationScope,
  object,
  id,
  fail,
  parse,
  NOW,
  leasedJob,
  requireJobAuthorization,
} from "./common.mjs";
import { canonical, profile } from "./planner.mjs";
export function requestFor(job, key) {
  hash(key);
  const request = parse(job.spec).requests.find((x) => x.requestKey === key);
  if (!request) fail("NOT_FOUND", "任务未授权此来源请求", 404);
  return request;
}
export function allowReceiptKind(env, kind) {
  if (
    kind !== "provider" &&
    !(kind === "fixture" && env.ALLOW_ACQUISITION_FIXTURES === "true")
  )
    fail("SOURCE_KIND", "此环境不接受该来源类型", 409);
}
/** Server-only admission helper; now is captured once by beginRequest.
 * The UTC date used for both the count and inserted row cannot roll mid-SQL. */
export async function reserveIntent(env, job, request, attemptId, now) {
  requireJobAuthorization(env, job);
  now = new Date(now).toISOString();
  const window = utcWindow(now),
    a = acquisitionAdmission(env);
  if (!a) fail("FINANCIAL_ACQUISITION_UNAVAILABLE", "获取配置无效", 503);
  return env.DB.prepare(
    `INSERT OR IGNORE INTO financial_acquisition_requests(request_key,authorization_scope,definition,job_id,attempt_id,state,created_at,updated_at)
    SELECT ?,?,?,?,?,'intent',?,? WHERE EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>=? AND deadline>=?)
    AND (SELECT COUNT(*) FROM financial_acquisition_requests WHERE created_at>=? AND created_at<?)<?`,
  )
    .bind(
      request.requestKey,
      request.authorizationScope,
      canonical(
        Object.fromEntries(
          Object.entries(request).filter(
            ([key]) => !["requestKey", "cache"].includes(key),
          ),
        ),
      ),
      job.id,
      attemptId,
      now,
      now,
      job.id,
      job.lease_token,
      now,
      now,
      window.start,
      window.end,
      a.maxDaily,
    )
    .run();
}
export async function beginRequest(env, jobId, key, value) {
  object(value, ["leaseToken", "attemptId"]);
  id(value.attemptId);
  const job = await leasedJob(env, jobId, value.leaseToken),
    request = requestFor(job, key);
  requireJobAuthorization(env, job);
  if (job.status !== "running") fail("CANCELLED", "任务正在取消", 409);
  if (request.authorizationScope !== authorizationScope(env))
    fail("ACQUISITION_SCOPE_CHANGED", "授权范围已经改变", 409);
  const now = NOW(),
    inserted = await reserveIntent(env, job, request, value.attemptId, now);
  const row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_requests WHERE request_key=?",
  )
    .bind(key)
    .first();
  if (!row) {
    if ((await budgetState(env, now)).newProviderRequestsRemaining === 0)
      fail(
        "ACQUISITION_DAILY_BUDGET",
        "今日新来源请求额度已用完；没有发送此请求",
        429,
      );
    fail("STALE_LEASE", "任务已停止", 409);
  }
  if (row.state === "received")
    return { state: "received", receiptId: row.receipt_id, maySend: false };
  if (
    row.job_id !== jobId ||
    row.attempt_id !== value.attemptId ||
    row.state !== "intent"
  )
    fail("REQUEST_REQUIRES_REVIEW", "同一来源请求已发出，不能自动重试", 409);
  return {
    state: "intent",
    attemptId: row.attempt_id,
    maySend: !!inserted.meta.changes,
  };
}
export async function markUnknown(env, jobId, key, value) {
  object(value, ["leaseToken", "attemptId", "reason"]);
  id(value.attemptId);
  string(value.reason, 700);
  const job = await leasedJob(env, jobId, value.leaseToken);
  requestFor(job, key);
  await env.DB.prepare(
    "UPDATE financial_acquisition_requests SET state='outcome_unknown',error=?,updated_at=? WHERE request_key=? AND job_id=? AND attempt_id=? AND state='intent'",
  )
    .bind(
      JSON.stringify({
        code: "PROVIDER_OUTCOME_UNKNOWN",
        message: value.reason,
      }),
      NOW(),
      key,
      jobId,
      value.attemptId,
    )
    .run();
  const row = await env.DB.prepare(
    "SELECT state FROM financial_acquisition_requests WHERE request_key=? AND job_id=? AND attempt_id=?",
  )
    .bind(key, jobId, value.attemptId)
    .first();
  if (!row) fail("NOT_FOUND", "来源意图不存在", 404);
  return {
    state: row.state,
    manualReviewRequired: row.state === "outcome_unknown",
  };
}
export const receiptDTO = (row) => ({
  receiptId: row.id,
  requestKey: row.request_key,
  sha256: row.sha256,
  byteLength: row.byte_length,
  httpStatus: row.http_status,
  retrievedAt: row.retrieved_at,
  sourceKind: row.source_kind,
});
export async function receiptFor(env, job, key) {
  const request = requestFor(job, key);
  const row = await env.DB.prepare(
    "SELECT c.* FROM financial_acquisition_cache c JOIN financial_acquisition_requests r ON r.receipt_id=c.id WHERE c.request_key=? AND c.authorization_scope=? AND r.state='received'",
  )
    .bind(key, request.authorizationScope)
    .first();
  if (!row) fail("NOT_FOUND", "来源回执尚不可读取", 404);
  allowReceiptKind(env, row.source_kind);
  if (
    request.cache.status === "frozen" &&
    (request.cache.receiptId !== row.id || request.cache.sha256 !== row.sha256)
  )
    fail("CACHE_CHANGED", "冻结来源身份不一致", 409);
  return row;
}
export async function rawReceipt(env, row) {
  const stored = await env.ARTIFACTS.get(row.object_key);
  if (
    !stored ||
    stored.size !== row.byte_length ||
    stored.size > profile.maxResponseBytes
  )
    fail("SOURCE_INTEGRITY", "来源回执不可读取", 409);
  const raw = new Uint8Array(await stored.arrayBuffer());
  if ((await hashBytes(raw)) !== row.sha256)
    fail("SOURCE_INTEGRITY", "来源回执哈希不一致", 409);
  return raw;
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
  integer(meta.byteLength, 0, profile.maxResponseBytes);
  integer(meta.httpStatus, 100, 599);
  string(meta.retrievedAt, 40);
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(meta.retrievedAt) ||
    !Number.isFinite(Date.parse(meta.retrievedAt)) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(meta.retrievedAt)
  )
    fail("INVALID_INPUT", "回执时间无效");
  allowReceiptKind(env, meta.sourceKind);
  const job = await leasedJob(env, jobId, token),
    request = requestFor(job, key);
  const intent = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_requests WHERE request_key=?",
  )
    .bind(key)
    .first();
  if (
    !intent ||
    intent.job_id !== jobId ||
    intent.attempt_id !== meta.attemptId
  )
    fail("NOT_FOUND", "来源意图不存在", 404);
  if (raw.length !== meta.byteLength || (await hashBytes(raw)) !== meta.sha256)
    fail("SOURCE_INTEGRITY", "回执长度或哈希不一致", 409);
  if (intent.state === "received") {
    const old = await receiptFor(env, job, key);
    if (
      old.sha256 !== meta.sha256 ||
      old.byte_length !== meta.byteLength ||
      old.http_status !== meta.httpStatus ||
      old.retrieved_at !== meta.retrievedAt ||
      old.source_kind !== meta.sourceKind
    )
      fail("RECEIPT_IMMUTABLE", "回执不可更改", 409);
    return { ...receiptDTO(old), idempotent: true };
  }
  if (intent.state !== "intent")
    fail("REQUEST_REQUIRES_REVIEW", "未知结果只能人工核对", 409);
  const idValue = random(),
    objectKey = `financial-acquisition/receipts/${key}/${meta.sha256}`;
  // Content-addressed immutable bytes: never delete a shared winner after a CAS.
  await env.ARTIFACTS.put(objectKey, raw, {
    httpMetadata: { contentType: "application/octet-stream" },
  });
  const now = NOW(),
    keys = parse(job.spec).requests.map((x) => x.requestKey),
    marks = keys.map(() => "?").join(",");
  await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO financial_acquisition_cache(id,request_key,authorization_scope,object_key,sha256,byte_length,http_status,retrieved_at,source_kind,created_at)
      SELECT ?,?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM financial_acquisition_requests WHERE request_key=? AND job_id=? AND attempt_id=? AND state='intent')
      AND EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND status IN ('running','cancel_requested') AND lease_token=? AND lease_until>=? AND deadline>=?)
      AND COALESCE((SELECT SUM(byte_length) FROM financial_acquisition_cache WHERE request_key IN (${marks})),0)+?<=?`,
    ).bind(
      idValue,
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
      jobId,
      meta.attemptId,
      jobId,
      token,
      now,
      now,
      ...keys,
      raw.length,
      profile.maxTotalBytes,
    ),
    env.DB.prepare(
      "UPDATE financial_acquisition_requests SET state='received',receipt_id=(SELECT id FROM financial_acquisition_cache WHERE request_key=?),updated_at=? WHERE request_key=? AND state='intent' AND EXISTS(SELECT 1 FROM financial_acquisition_cache WHERE request_key=?)",
    ).bind(key, now, key, key),
  ]);
  const row = await receiptFor(env, job, key);
  if (
    row.sha256 !== meta.sha256 ||
    row.byte_length !== meta.byteLength ||
    row.http_status !== meta.httpStatus ||
    row.retrieved_at !== meta.retrievedAt ||
    row.source_kind !== meta.sourceKind
  )
    fail("RECEIPT_IMMUTABLE", "另一请求已保存不同回执", 409);
  return receiptDTO(row);
}
