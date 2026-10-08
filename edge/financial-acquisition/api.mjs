import { acquisitionAdmission, budgetState } from "./admission.mjs";
import { body } from "../runtime.mjs";
import { json, pageQuery } from "../financial/common.mjs";
import { profile } from "./planner.mjs";
import {
  acquisitionEnabled,
  authorizationScope,
  runnerInfo,
  ownedPlan,
  ownedJob,
  planDTO,
  jobDTO,
  parse,
  expireAcquisitions,
  fail,
  NOW,
} from "./common.mjs";
import { createPlan, startPlan } from "./plans.mjs";
import { cancelJob } from "./jobs.mjs";
export async function acquisitionApi(req, env, path, owner) {
  if (!path.startsWith("/financial/acquisition-")) return null;
  if (path === "/financial/acquisition-capabilities" && req.method === "GET")
    return json({
      enabled: acquisitionEnabled(env, owner),
      audience: acquisitionAdmission(env)?.audience || "unavailable",
      maintenancePaused: !!(await env.DB.prepare(
        "SELECT 1 FROM meta WHERE key='financial_acquisition_maintenance' AND value='paused'",
      ).first()),
      budget: await budgetState(env, NOW()),
      testFixtureMode: env.ALLOW_ACQUISITION_FIXTURES === "true",
      profile: profile.id,
      limits: {
        maxSymbols: 2,
        sameVenue: true,
        maxProviderRequests: 7,
        maxResponseBytes: profile.maxResponseBytes,
        maxTotalBytes: profile.maxTotalBytes,
        maxCalendarDays: 366,
      },
      runner: await runnerInfo(env),
      researchBinding: false,
      unitVerified: false,
    });
  if (path === "/financial/acquisition-plans" && req.method === "POST")
    return json(await createPlan(env, owner, await body(req, 16384)), 201);
  if (path === "/financial/acquisition-jobs" && req.method === "GET") {
    const { page, pageSize, offset } = pageQuery(new URL(req.url));
    const results = await env.DB.batch([
      env.DB.prepare(
        "SELECT * FROM financial_acquisition_jobs WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
      ).bind(owner, pageSize, offset),
      env.DB.prepare(
        "SELECT COUNT(*) n FROM financial_acquisition_jobs WHERE owner=?",
      ).bind(owner),
    ]);
    return json({
      items: results[0].results.map(jobDTO),
      total: results[1].results[0].n,
      page,
      pageSize,
    });
  }
  const m =
    /^\/financial\/acquisition-(plans|jobs)\/([a-f0-9-]+)(?:\/(start|cancel))?$/.exec(
      path,
    );
  if (m) {
    const [, kind, id, action] = m;
    if (kind === "plans" && !action && req.method === "GET")
      return json({ plan: planDTO(await ownedPlan(env, owner, id)) });
    if (kind === "plans" && action === "start" && req.method === "POST")
      return json(await startPlan(env, owner, id, await body(req, 4096)), 202);
    if (kind === "jobs" && !action && req.method === "GET") {
      await ownedJob(env, owner, id);
      await expireAcquisitions(env);
      const rows = await env.DB.batch([
        env.DB.prepare(
          "SELECT * FROM financial_acquisition_jobs WHERE id=? AND owner=?",
        ).bind(id, owner),
        env.DB.prepare(
          `SELECT json_extract(p.value,'$.requestKey') request_key,json_extract(p.value,'$.endpoint') endpoint,r.state,c.id receipt_id,c.sha256,c.byte_length,c.retrieved_at
          FROM financial_acquisition_jobs j,json_each(j.spec,'$.requests') p
          LEFT JOIN financial_acquisition_requests r ON r.request_key=json_extract(p.value,'$.requestKey')
          LEFT JOIN financial_acquisition_cache c ON c.id=r.receipt_id WHERE j.id=? AND j.owner=? ORDER BY p.key`,
        ).bind(id, owner),
      ]);
      return json({
        job: jobDTO(rows[0].results[0]),
        receipts: rows[1].results.map((x) => ({
          requestKey: x.request_key,
          endpoint: x.endpoint,
          status: x.state || "not_started",
          receiptId: x.receipt_id || null,
          sha256: x.sha256 || null,
          byteLength: x.byte_length ?? null,
          retrievedAt: x.retrieved_at || null,
        })),
      });
    }
    if (kind === "jobs" && action === "cancel" && req.method === "POST")
      return json(await cancelJob(env, owner, id));
  }
  fail("NOT_FOUND", "来源获取接口不存在", 404);
}
