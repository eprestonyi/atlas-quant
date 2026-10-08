/** Private immutable market source admission; caller-controlled profile cannot grant it. */
import { parse } from "../runtime.mjs";
import { object } from "../financial/common.mjs";
import { validateExpression } from "../factor-language.mjs";
import { validateStoredStatisticalQuant } from "../statistical-quant/validation.mjs";
import { canonical, digest, id, hash, fail, PROFILE, date } from "./common.mjs";
import { readScope, bindWholeScope } from "./scope.mjs";
import { marketAuthorizationScope } from "./planner.mjs";
import {
  DATASET_FORMAT,
  datasetRef,
  validateManifest,
} from "./publication.mjs";
import { loadPlan } from "./queue.mjs";
import {
  AUTO_PROFILE,
  MARKET_RESEARCH_PROFILES,
  researchEnabled,
  supportsMarket,
} from "./admissions.mjs";
export {
  AUTO_PROFILE,
  MARKET_RESEARCH_PROFILES,
  researchEnabled,
  supportsMarket,
};
export function validateMarketRef(ref) {
  object(ref, ["datasetId", "datasetRoot", "format", "version"]);
  id(ref.datasetId);
  hash(ref.datasetRoot);
  if (ref.format !== DATASET_FORMAT || ref.version !== 1)
    fail("MARKET_DATASET_REF", "未知市场来源格式");
  return ref;
}
export function validateCapacity(strategy, profile) {
  const s = validateStoredStatisticalQuant(strategy, {
      scopeSymbolLimit: 1000,
    }),
    automatic = profile === AUTO_PROFILE;
  if (
    !MARKET_RESEARCH_PROFILES.includes(profile) ||
    s.target.kind !== "asset_price" ||
    s.execution.enabled ||
    s.factors.length > 16 ||
    s.model.refitDays < 20 ||
    s.validation.innerFolds !== 2 ||
    s.validation.outerFolds !== 2 ||
    (date(s.universe.end) - date(s.universe.start)) / 86400000 + 1 > 366 ||
    s.model.estimator !== (automatic ? "auto" : "ridge") ||
    !(automatic ? ["mean_reversion"] : ["mean_reversion", "trend"]).includes(
      s.model.family,
    )
  )
    fail(
      "MARKET_RESEARCH_PROFILE",
      "完整池仅支持已验证的目标、机制及模型组合；不会降级或缩池",
      409,
    );
  return s;
}
export async function readyDataset(env, owner, ref) {
  validateMarketRef(ref);
  const d = await env.DB.prepare(
    "SELECT d.* FROM quant_market_datasets d JOIN quant_market_publications p ON p.job_id=d.job_id AND p.owner=d.owner JOIN quant_market_jobs j ON j.id=d.job_id AND j.owner=d.owner WHERE d.id=? AND d.owner=? AND d.dataset_root=? AND d.status='ready' AND p.status='committed' AND p.manifest_hash=d.dataset_root AND j.status='completed'",
  )
    .bind(ref.datasetId, owner, ref.datasetRoot)
    .first();
  if (!d) fail("NOT_FOUND", "完整市场来源未就绪或不属于此工作区", 404);
  const { plan } = await loadPlan(
      env,
      owner,
      (
        await env.DB.prepare("SELECT plan_id FROM quant_market_jobs WHERE id=?")
          .bind(d.job_id)
          .first()
      ).plan_id,
      d.plan_root,
    ),
    m = JSON.parse(d.manifest);
  if ((await digest(m)) !== d.dataset_root || canonical(m) !== d.manifest)
    fail("MARKET_DATASET_INTEGRITY", "市场来源目录损坏", 409);
  validateManifest(m, { plan_root: d.plan_root }, plan);
  return { dataset: d, manifest: m, plan };
}
export async function admitMarketResearch(env, owner, strategy, input) {
  if (!researchEnabled(env))
    fail("MARKET_RESEARCH_DISABLED", "完整市场研究入口尚未启用", 503);
  if (
    input.datasetRef !== undefined ||
    input.dataset !== undefined ||
    input.providerAccess !== undefined
  )
    fail("MARKET_RESEARCH_SOURCE", "冻结市场研究不能同时提交其他来源");
  const ref = validateMarketRef(input.marketDatasetRef),
    scope = await readScope(env, owner, input.universeScopeRef),
    s = validateCapacity(
      bindWholeScope(strategy, scope.scope),
      input.admissionProfile,
    ),
    r = await readyDataset(env, owner, ref);
  if (
    canonical(r.manifest.universeScopeRef) !== canonical(scope.scopeRef) ||
    r.plan.authorizationScope !== marketAuthorizationScope(env)
  )
    fail("MARKET_RESEARCH_SOURCE", "市场来源与完整范围或授权配置不一致", 409);
  const required = new Set(
    s.factors.flatMap((f) => validateExpression(f.expression).fields),
  );
  if ([...required].some((f) => !r.manifest.fields.includes(f)))
    fail(
      "MARKET_FIELDS_MISSING",
      "冻结市场来源未包含全部因子字段；不会补取或伪造",
      409,
    );
  return {
    ...r,
    marketDatasetRef: ref,
    universeScopeRef: scope.scopeRef,
    admissionProfile: input.admissionProfile,
    scope: { ...r.manifest.scope, scopeRoot: scope.scopeRef.scopeRoot },
    strategy: s,
    sourceEvidence: {
      marketDatasetRef: ref,
      universeScopeRef: scope.scopeRef,
      admissionProfile: input.admissionProfile,
      rowValueRoot: r.dataset.row_value_root,
    },
  };
}
export async function experimentMarketBinding(env, owner, eid, version) {
  const row = await env.DB.prepare(
    "SELECT * FROM quant_experiment_market_datasets WHERE experiment_id=? AND version=? AND owner=?",
  )
    .bind(eid, version, owner)
    .first();
  return row
    ? {
        marketDatasetRef: {
          datasetId: row.dataset_id,
          datasetRoot: row.dataset_root,
          format: DATASET_FORMAT,
          version: 1,
        },
        admissionProfile: row.profile,
        universeScopeRef: {
          scopeId: row.scope_id,
          scopeRoot: row.scope_root,
          format: "atlas.quant.universe_scope",
          version: 1,
        },
        scope: parse(row.scope),
      }
    : null;
}
export function experimentMarketStatement(
  env,
  owner,
  eid,
  version,
  a,
  spec,
  now,
) {
  return env.DB.prepare(
    `INSERT INTO quant_experiment_market_datasets(experiment_id,version,owner,dataset_id,dataset_root,profile,scope_id,scope_root,scope,created_at)
 SELECT ?,?,?,?,?,?,?,?,?,? WHERE changes()=1 AND EXISTS(SELECT 1 FROM quant_experiment_versions v JOIN quant_experiments e ON e.id=v.experiment_id WHERE v.experiment_id=? AND v.version=? AND v.spec=? AND e.owner=?) AND NOT EXISTS(SELECT 1 FROM quant_experiment_market_datasets WHERE experiment_id=? AND version=?)`,
  ).bind(
    eid,
    version,
    owner,
    a.marketDatasetRef.datasetId,
    a.marketDatasetRef.datasetRoot,
    a.admissionProfile,
    a.universeScopeRef.scopeId,
    a.universeScopeRef.scopeRoot,
    canonical(a.scope),
    now,
    eid,
    version,
    spec,
    owner,
    eid,
    version,
  );
}
export async function assertRunMarket(env, job) {
  const r = await env.DB.prepare(
    "SELECT * FROM quant_run_market_datasets WHERE job_id=? AND owner=?",
  )
    .bind(job.id, job.owner)
    .first();
  if (!r) fail("MARKET_RUN_BINDING", "研究缺少已批准市场来源关联", 409);
  const scope = await env.DB.prepare(
    "SELECT scope_id,scope_root FROM quant_run_scopes WHERE job_id=? AND owner=?",
  )
    .bind(job.id, job.owner)
    .first();
  if (!scope) fail("MARKET_RUN_BINDING", "研究缺少完整池关联", 409);
  return admitMarketResearch(env, job.owner, parse(job.spec), {
    marketDatasetRef: {
      datasetId: r.dataset_id,
      datasetRoot: r.dataset_root,
      format: DATASET_FORMAT,
      version: 1,
    },
    admissionProfile: r.profile,
    universeScopeRef: {
      scopeId: scope.scope_id,
      scopeRoot: scope.scope_root,
      format: "atlas.quant.universe_scope",
      version: 1,
    },
  });
}
