/** Exact-lease, provider-free access to a committed market source. */
import { json, NOW } from "../runtime.mjs";
import { id, hash, fail, canonical } from "./common.mjs";
import { hashBytes } from "../financial/common.mjs";
import { assertRunMarket } from "./research.mjs";
import { readPart, outputCollections } from "./publication.mjs";

const bytes = (value) => new TextEncoder().encode(value);
export function sourceResponse(raw, root) {
  return new Response(raw, {
    encodeBody: "manual",
    headers: {
      "content-type": "application/octet-stream",
      "content-encoding": "identity",
      "cache-control": "no-store, no-transform",
      "content-length": String(raw.length),
      "x-content-sha256": root,
    },
  });
}

export async function marketResearchApi(req, env, path) {
  if (!path.startsWith("/runner/research-markets/")) return null;
  if (req.method !== "GET") fail("METHOD", "冻结市场仅支持GET", 405);
  const match =
    /^\/runner\/research-markets\/([a-f0-9-]{36})\/(input|manifest|plan|scope|parts)(?:\/(rows|receipts|provenance|raw)\/(\d{1,3}))?$/.exec(
      path,
    );
  if (!match) fail("NOT_FOUND", "市场来源接口不存在", 404);
  const [, jobId, action, name, ordinal] = match,
    token = id(req.headers.get("X-Dataset-Lease"));
  const job = await env.DB.prepare(
    "SELECT * FROM jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>?",
  )
    .bind(id(jobId), token, NOW())
    .first();
  if (!job || job.data_source !== "ready_market")
    fail("STALE_LEASE", "市场研究租约不存在或已终止", 409);
  const admitted = await assertRunMarket(env, job),
    root = admitted.marketDatasetRef.datasetRoot;
  const query = new URL(req.url).searchParams;
  if (
    [...query.keys()].some((k) => k !== "datasetRoot") ||
    (action !== "input" && hash(query.get("datasetRoot")) !== root)
  )
    fail("MARKET_DATASET_ROOT", "需要固定市场来源根", 409);
  const base = `/quant/api/runner/research-markets/${job.id}`;
  const scopeRow = await env.DB.prepare(
    "SELECT spec FROM quant_universe_scopes WHERE id=? AND owner=? AND scope_root=?",
  )
    .bind(
      admitted.universeScopeRef.scopeId,
      job.owner,
      admitted.universeScopeRef.scopeRoot,
    )
    .first();
  if (!scopeRow) fail("MARKET_SOURCE_INTEGRITY", "原范围证据缺失", 409);
  const documents = {
    manifest: admitted.dataset.manifest,
    plan: canonical(admitted.plan),
    scope: scopeRow.spec,
  };
  if (action === "input" && !name) {
    const descriptors = {};
    for (const [key, text] of Object.entries(documents))
      descriptors[key] = {
        sha256: await hashBytes(bytes(text)),
        byteLength: bytes(text).length,
        url: `${base}/${key}?datasetRoot=${root}`,
      };
    return json({
      job: { id: job.id, kind: "forecast" },
      marketDatasetRef: admitted.marketDatasetRef,
      universeScopeRef: admitted.universeScopeRef,
      admissionProfile: admitted.admissionProfile,
      sourceEvidence: admitted.sourceEvidence,
      documents: descriptors,
      partUrlTemplate: `${base}/parts/{collection}/{ordinal}?datasetRoot=${root}`,
    });
  }
  if (documents[action] && !name) {
    const raw = bytes(documents[action]);
    return sourceResponse(raw, await hashBytes(raw));
  }
  if (action === "parts" && name) {
    const descriptor = outputCollections(admitted.manifest)[name]?.chunks[
      Number(ordinal)
    ];
    if (!descriptor || descriptor.ordinal !== Number(ordinal))
      fail("NOT_FOUND", "市场片段不存在", 404);
    const sourceJob = await env.DB.prepare(
      "SELECT * FROM quant_market_jobs WHERE id=? AND owner=? AND status='completed'",
    )
      .bind(admitted.dataset.job_id, job.owner)
      .first();
    if (!sourceJob) fail("MARKET_SOURCE_INTEGRITY", "市场准备任务缺失", 409);
    return sourceResponse(
      await readPart(env, sourceJob, admitted.manifest, name, descriptor),
      descriptor.sha256,
    );
  }
  fail("NOT_FOUND", "市场来源文档不存在", 404);
}
