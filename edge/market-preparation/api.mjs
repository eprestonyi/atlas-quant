import { marketArchiveResponse } from "./archive.mjs";
import { readyDataset, validateMarketRef } from "./research.mjs";
import { DATASET_FORMAT, datasetRef } from "./publication.mjs";
import { pageQuery } from "../financial/common.mjs";
import { parse } from "../runtime.mjs";
import { marketResearchAdmissions } from "./admissions.mjs";
import { start, cancel, ownedJob, jobView, enabled } from "./queue.mjs";
import { NOW, random, json, body, rate } from "../runtime.mjs";
import {
  LIMITS,
  object,
  id,
  canonical,
  byteLength,
  digest,
  scopeRef,
  fail,
} from "./common.mjs";
import { freezeScope, readScope } from "./scope.mjs";
import {
  createMarketPlan,
  marketAuthorizationScope,
  planView,
} from "./planner.mjs";

export async function marketPreparationApi(req, env, path, owner) {
  if (path === "/market-datasets" && req.method === "GET") {
    const { page, pageSize, offset } = pageQuery(new URL(req.url));
    const rows = await env.DB.batch([
      env.DB.prepare(
        "SELECT id,dataset_root,created_at,json_extract(manifest,'$.universeScopeRef') scope_ref,json_extract(manifest,'$.scope') scope,json_extract(manifest,'$.fields') fields,json_extract(manifest,'$.rowCount') row_count,json_extract(manifest,'$.sourceKind') source_kind FROM quant_market_datasets WHERE owner=? AND status='ready' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
      ).bind(owner, pageSize, offset),
      env.DB.prepare(
        "SELECT count(*) n FROM quant_market_datasets WHERE owner=? AND status='ready'",
      ).bind(owner),
    ]);
    return json({
      items: rows[0].results.map((r) => ({
        marketDatasetRef: datasetRef(r),
        universeScopeRef: parse(r.scope_ref),
        scope: parse(r.scope),
        fields: parse(r.fields),
        rowCount: r.row_count,
        sourceKind: r.source_kind,
        createdAt: r.created_at,
      })),
      total: rows[1].results[0].n,
      page,
      pageSize,
      ...(await marketResearchAdmissions(env)),
    });
  }
  const datasetMatch = /^\/market-datasets\/([a-f0-9-]{36})(\/download)?$/.exec(
    path,
  );
  if (datasetMatch && req.method === "GET") {
    const query = new URL(req.url).searchParams;
    if ([...query.keys()].some((k) => k !== "datasetRoot"))
      fail("UNKNOWN_PROPERTY", "来源读取参数无效");
    const ref = {
      datasetId: datasetMatch[1],
      datasetRoot: query.get("datasetRoot"),
      format: DATASET_FORMAT,
      version: 1,
    };
    const a = await readyDataset(env, owner, ref);
    if (datasetMatch[2]) return marketArchiveResponse(env, a, owner);
    return json({
      marketDatasetRef: ref,
      universeScopeRef: a.manifest.universeScopeRef,
      scope: a.manifest.scope,
      fields: a.manifest.fields,
      rowCount: a.manifest.rowCount,
      sourceKind: a.manifest.sourceKind,
      createdAt: a.dataset.created_at,
      ...(await marketResearchAdmissions(env)),
    });
  }
  const started = /^\/market-preparation-plans\/([a-f0-9-]{36})\/start$/.exec(
    path,
  );
  if (started && req.method === "POST")
    return json(
      await start(env, owner, started[1], await body(req, 4096)),
      202,
    );
  const job = /^\/market-preparation-jobs\/([a-f0-9-]{36})(\/cancel)?$/.exec(
    path,
  );
  if (job && req.method === "GET" && !job[2])
    return json({
      job: jobView(await ownedJob(env, owner, job[1])),
      ...(await marketResearchAdmissions(env)),
    });
  if (job && req.method === "POST" && job[2])
    return json(await cancel(env, owner, job[1]));

  if (path === "/universe-scopes" && req.method === "POST") {
    await rate(env, "universe-scope:" + owner, 30, 60);
    const result = await freezeScope(env, owner, await body(req, 200000));
    return json(result, result.created ? 201 : 200);
  }
  const match = /^\/universe-scopes\/([a-f0-9-]{36})$/.exec(path);
  if (match && req.method === "GET") {
    id(match[1]);
    const row = await env.DB.prepare(
      "SELECT id,scope_root FROM quant_universe_scopes WHERE id=? AND owner=?",
    )
      .bind(match[1], owner)
      .first();
    if (!row) fail("NOT_FOUND", "完整股票池范围不存在", 404);
    const result = await readScope(env, owner, scopeRef(row));
    return json({ scopeRef: result.scopeRef, scope: result.scope });
  }
  if (path === "/market-preparation-plans" && req.method === "POST") {
    await rate(env, "market-plan:" + owner, 20, 60);
    const input = await body(req, 20000);
    object(input, ["scopeRef", "profile", "requiredFields"]);
    const frozen = await readScope(env, owner, input.scopeRef);
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en", {
        timeZone: "Asia/Hong_Kong",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      })
        .formatToParts(new Date())
        .map((x) => [x.type, x.value]),
    );
    const plan = await createMarketPlan(
      frozen.scope,
      frozen.scopeRef,
      input,
      marketAuthorizationScope(env),
      parts.year + parts.month + parts.day,
    );
    const text = canonical(plan);
    if (byteLength(text) > LIMITS.planBytes)
      fail("MARKET_PLAN_SIZE", "完整请求计划超过元数据预算；不会删减请求", 413);
    const planId = random();
    await env.DB.prepare(
      `INSERT OR IGNORE INTO quant_market_plans(id,owner,scope_id,scope_root,plan_root,profile,spec,created_at)
      SELECT ?,?,?,?,?,?,?,? WHERE (SELECT count(*) FROM quant_market_plans WHERE owner=?)<?`,
    )
      .bind(
        planId,
        owner,
        frozen.row.id,
        frozen.row.scope_root,
        plan.planRoot,
        plan.profile,
        text,
        NOW(),
        owner,
        LIMITS.savedPlans,
      )
      .run();
    const row = await env.DB.prepare(
      "SELECT * FROM quant_market_plans WHERE owner=? AND plan_root=?",
    )
      .bind(owner, plan.planRoot)
      .first();
    if (!row) fail("MARKET_PLAN_LIMIT", "市场准备计划已达上限", 429);
    return json(
      {
        ...planView(row, plan, enabled(env)),
        ...(await marketResearchAdmissions(env)),
      },
      row.id === planId ? 201 : 200,
    );
  }
  const planMatch =
    /^\/market-preparation-plans\/([a-f0-9-]{36})(\/requests)?$/.exec(path);
  if (planMatch && req.method === "GET") {
    const row = await env.DB.prepare(
      "SELECT * FROM quant_market_plans WHERE owner=? AND id=?",
    )
      .bind(owner, id(planMatch[1]))
      .first();
    if (!row) fail("NOT_FOUND", "市场准备计划不存在", 404);
    let plan;
    try {
      plan = JSON.parse(row.spec);
    } catch {
      fail("MARKET_PLAN_INTEGRITY", "冻结计划损坏", 409);
    }
    const { planRoot, ...content } = plan;
    if (
      canonical(plan) !== row.spec ||
      planRoot !== row.plan_root ||
      (await digest(content)) !== planRoot
    )
      fail("MARKET_PLAN_INTEGRITY", "冻结计划根不匹配", 409);
    if (!planMatch[2])
      return json({
        ...planView(row, plan, enabled(env)),
        ...(await marketResearchAdmissions(env)),
      });
    const query = new URL(req.url).searchParams,
      page = Number(query.get("page") ?? 1),
      pageSize = Number(query.get("pageSize") ?? 50);
    if (
      [...query.keys()].some((x) => !["page", "pageSize"].includes(x)) ||
      !Number.isInteger(page) ||
      page < 1 ||
      page > 10000 ||
      !Number.isInteger(pageSize) ||
      pageSize < 1 ||
      pageSize > 100
    )
      fail("INVALID_PAGE", "请求页须为有效整数，每页最多100项");
    return json({
      items: plan.requests.slice((page - 1) * pageSize, page * pageSize),
      total: plan.requests.length,
      page,
      pageSize,
      planRoot,
    });
  }
  return null;
}
