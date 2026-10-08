import { body, json } from "../runtime.mjs";
import { readBytes } from "../financial/common.mjs";
import { fail, hash, LIMITS } from "./common.mjs";
import {
  claim,
  heartbeat,
  failJob,
  leased,
  loadPlan,
  requireEnabled,
  jobView,
} from "./queue.mjs";
import {
  begin,
  unknown,
  putReceipt,
  getReceipt,
  readReceipt,
  receiptView,
} from "./receipts.mjs";
import {
  beginPublication,
  putPart,
  complete,
  publication,
  publicationStatus,
  PUBLICATION_LIMITS,
} from "./publication.mjs";
function metadata(token) {
  if (
    typeof token !== "string" ||
    token.length > 4096 ||
    !/^[a-zA-Z0-9_-]+$/.test(token)
  )
    fail("MARKET_RECEIPT", "回执头无效");
  try {
    return JSON.parse(atob(token.replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    fail("MARKET_RECEIPT", "回执头解析失败");
  }
}
export async function marketRunnerApi(req, env, path) {
  if (!path.startsWith("/runner/market-acquire/")) return null;
  const route = path.slice("/runner/market-acquire".length);
  if (route === "/claim" && req.method === "POST")
    return json(await claim(env, await body(req, 4096)));
  if (route === "/heartbeat" && req.method === "POST")
    return json(await heartbeat(env, await body(req, 4096)));
  const m =
    /^\/jobs\/([a-f0-9-]{36})\/(input|status|fail|complete|publication|requests)(?:\/([a-z0-9]+))?(?:\/(begin|unknown|receipt|0|[1-9][0-9]{0,2}))?$/.exec(
      route,
    );
  if (!m) fail("NOT_FOUND", "市场准备接口不存在", 404);
  const [, jid, action, key, op] = m,
    token = req.headers.get("X-Acquisition-Lease");
  if (action === "fail" && req.method === "POST")
    return json(await failJob(env, jid, await body(req, 4096)));
  if (action === "complete" && req.method === "POST")
    return json(await complete(env, jid, await body(req, 4096)));
  if (action === "requests" && op === "begin" && req.method === "POST")
    return json(await begin(env, jid, key, await body(req, 4096)));
  if (action === "requests" && op === "unknown" && req.method === "POST")
    return json(await unknown(env, jid, key, await body(req, 4096)));
  if (action === "requests" && op === "receipt" && req.method === "PUT")
    return json(
      await putReceipt(
        env,
        jid,
        key,
        token,
        metadata(req.headers.get("X-Acquisition-Receipt")),
        await readBytes(req, 256 * 1024),
      ),
    );
  if (action === "publication" && req.method === "POST")
    return json(await beginPublication(env, jid, await body(req, 300 * 1024)));
  const job = await leased(env, jid, token, action === "status");
  if (action === "status" && req.method === "GET")
    return json({ job: jobView(job) });
  if (action === "input" && req.method === "GET") {
    requireEnabled(env, job);
    const { plan } = await loadPlan(env, job.owner, job.plan_id, job.plan_root);
    return json({
      job: { id: job.id, kind: "market_acquire", planId: job.plan_id },
      plan,
      publicationLimits: PUBLICATION_LIMITS,
    });
  }
  if (action === "requests" && op === "receipt" && req.method === "GET") {
    requireEnabled(env, job);
    const row = await getReceipt(env, job, key),
      raw = await readReceipt(env, row);
    return new Response(raw, {
      encodeBody: "manual",
      headers: {
        "content-type": "application/octet-stream",
        "content-encoding": "identity",
        "cache-control": "no-store, no-transform",
        "content-length": String(raw.length),
        "x-content-sha256": row.sha256,
        "X-Acquisition-Receipt": btoa(JSON.stringify(receiptView(row)))
          .replaceAll("+", "-")
          .replaceAll("/", "_")
          .replaceAll("=", ""),
      },
    });
  }
  if (action === "publication") {
    const p = await publication(env, jid),
      root = hash(new URL(req.url).searchParams.get("manifestSha256"));
    if (root !== p.manifest_hash)
      fail("MARKET_PUBLICATION_CONFLICT", "市场目录版本不符", 409);
    if (req.method === "GET" && !key)
      return json(await publicationStatus(env, p));
    if (req.method === "PUT" && key && op !== undefined)
      return json(
        await putPart(
          env,
          jid,
          token,
          root,
          key,
          Number(op),
          await readBytes(req, PUBLICATION_LIMITS.chunkBytes),
        ),
      );
  }
  fail("NOT_FOUND", "市场准备操作不存在", 404);
}
