import { body } from "../runtime.mjs";
import {
  json,
  hash,
  readBytes,
  utf8,
  LIMITS,
  fixedStream,
} from "../financial/common.mjs";
import { profile } from "./planner.mjs";
import {
  leasedJob,
  parse,
  fail,
  jobDTO,
  requireJobAuthorization,
} from "./common.mjs";
import { heartbeat, claim, failJob } from "./jobs.mjs";
import {
  beginRequest,
  markUnknown,
  putReceipt,
  receiptFor,
  rawReceipt,
  receiptDTO,
} from "./receipts.mjs";
import {
  beginPublication,
  getPublication,
  publicationStatus,
  putPart,
  complete,
} from "./publication.mjs";
function decodeMeta(value) {
  if (
    typeof value !== "string" ||
    value.length > 4096 ||
    !/^[a-zA-Z0-9_-]+$/.test(value)
  )
    fail("INVALID_INPUT", "回执元数据无效");
  try {
    return JSON.parse(atob(value.replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    fail("INVALID_INPUT", "回执元数据无效");
  }
}
export async function acquisitionRunnerApi(req, env, path) {
  if (!path.startsWith("/runner/financial-acquire/")) return null;
  const route = path.slice("/runner/financial-acquire".length);
  if (route === "/heartbeat" && req.method === "POST")
    return json(await heartbeat(env, await body(req, 4096)));
  if (route === "/claim" && req.method === "POST")
    return json(await claim(env, await body(req, 4096)));
  const m =
    /^\/jobs\/([a-f0-9-]+)\/(input|status|fail|complete|requests|publication)(?:\/([a-z0-9]+))?(?:\/(begin|receipt|unknown|0|[1-9][0-9]?))?$/.exec(
      route,
    );
  if (!m) fail("NOT_FOUND", "来源获取服务接口不存在", 404);
  const [, jobId, action, key, operation] = m,
    token = req.headers.get("X-Acquisition-Lease");
  if (action === "fail" && req.method === "POST")
    return json(await failJob(env, jobId, await body(req, 4096)));
  if (action === "complete" && req.method === "POST")
    return json(await complete(env, jobId, await body(req, 4096)));
  if (action === "requests" && operation === "begin" && req.method === "POST")
    return json(await beginRequest(env, jobId, key, await body(req, 4096)));
  if (action === "requests" && operation === "unknown" && req.method === "POST")
    return json(await markUnknown(env, jobId, key, await body(req, 4096)));
  if (action === "requests" && operation === "receipt" && req.method === "PUT")
    return json(
      await putReceipt(
        env,
        jobId,
        key,
        token,
        decodeMeta(req.headers.get("X-Acquisition-Receipt")),
        await readBytes(req, profile.maxResponseBytes),
      ),
    );
  if (action === "publication" && req.method === "POST")
    return json(
      await beginPublication(env, jobId, await body(req, 128 * 1024)),
    );
  const job = await leasedJob(env, jobId, token, {
    terminal: action === "status",
  });
  if (action === "status" && req.method === "GET")
    return json({ job: jobDTO(job), result: parse(job.result) });
  if (action === "input" && req.method === "GET") {
    requireJobAuthorization(env, job);
    const plan = await env.DB.prepare(
      "SELECT spec FROM financial_acquisition_plans WHERE id=? AND owner=?",
    )
      .bind(job.plan_id, job.owner)
      .first();
    return json({
      job: { id: job.id, kind: "financial_acquire", planId: job.plan_id },
      reviewedPlan: parse(plan.spec),
      executionPlan: parse(job.spec),
      limits: {
        responseBytes: profile.maxResponseBytes,
        totalBytes: profile.maxTotalBytes,
        packageBytes: LIMITS.packageBytes,
        calendarBytes: LIMITS.registryEntryBytes,
        chunkBytes: LIMITS.chunkBytes,
        manifestBytes: 128 * 1024,
      },
    });
  }
  if (
    action === "requests" &&
    operation === "receipt" &&
    req.method === "GET"
  ) {
    requireJobAuthorization(env, job);
    const row = await receiptFor(env, job, key),
      raw = await rawReceipt(env, row);
    return new Response(raw, {
      encodeBody: "manual",
      headers: {
        "content-type": "application/octet-stream",
        "cache-control": "no-store, no-transform",
        "content-encoding": "identity",
        "content-length": String(raw.length),
        "x-content-sha256": row.sha256,
        "X-Acquisition-Receipt": btoa(JSON.stringify(receiptDTO(row)))
          .replaceAll("+", "-")
          .replaceAll("/", "_")
          .replaceAll("=", ""),
        "X-Acquisition-Source-Kind": row.source_kind,
        "X-Acquisition-Http-Status": String(row.http_status),
      },
    });
  }
  if (action === "publication") {
    const pub = await getPublication(env, jobId),
      expected = new URL(req.url).searchParams.get("manifestSha256");
    if (hash(expected) !== pub.manifest_hash)
      fail("ROOT_MISMATCH", "输出版本不一致", 409);
    if (req.method === "GET" && !key)
      return json(await publicationStatus(env, pub));
    if (req.method === "PUT" && key && operation !== undefined)
      return json(
        await putPart(
          env,
          jobId,
          token,
          expected,
          key,
          Number(operation),
          await readBytes(req, LIMITS.chunkBytes),
        ),
      );
  }
  fail("NOT_FOUND", "来源获取服务接口不存在", 404);
}
