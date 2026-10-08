/** Whole-filter membership is an immutable owner-bound input, not a sampler. */
import { NOW, random } from "../runtime.mjs";
import { resolveUniverseRules } from "../studio.mjs";
import {
  SCOPE_FORMAT,
  LIMITS,
  object,
  hash,
  date,
  digest,
  canonical,
  byteLength,
  scopeRef,
  validateScopeRef,
  fail,
} from "./common.mjs";

export async function freezeScope(env, owner, input) {
  object(input, [
    "selection",
    "expectedResolutionHash",
    "expectedSnapshotHash",
    "start",
    "end",
  ]);
  hash(input.expectedResolutionHash);
  hash(input.expectedSnapshotHash);
  const start = date(input.start),
    end = date(input.end);
  if (end <= start || end - start > 366 * 10 * 86400000)
    fail("INVALID_SCOPE", "研究范围须递增且最多十年；容量准入另行检查");
  const resolved = await resolveUniverseRules(env, input.selection);
  if (
    resolved.resolutionHash !== input.expectedResolutionHash ||
    resolved.snapshotHash !== input.expectedSnapshotHash
  )
    fail("UNIVERSE_CHANGED", "目录或规则已变化，请重新解析完整集合", 409);
  if (!resolved.symbolCount || resolved.symbolCount > LIMITS.scopeSymbols)
    fail("UNIVERSE_SCOPE_LIMIT", "完整筛选结果为空或超过目录范围");
  const scope = {
    format: SCOPE_FORMAT,
    version: 1,
    membershipPolicy: "complete_filtered_set",
    symbols: resolved.symbols,
    symbolCount: resolved.symbolCount,
    start: input.start,
    end: input.end,
    selection: resolved.selection,
    resolutionHash: resolved.resolutionHash,
    snapshotHash: resolved.snapshotHash,
    catalogSnapshot: resolved.catalogSnapshot,
    sourceUniverses: resolved.sourceUniverses,
    steps: resolved.steps,
    algorithmVersion: resolved.algorithmVersion,
    historicalMembershipVerified: false,
  };
  const text = canonical(scope),
    root = await digest(scope);
  if (byteLength(text) > LIMITS.scopeBytes)
    fail(
      "UNIVERSE_SCOPE_SIZE",
      "完整规则证据超过有界存储预算；不会删除成员",
      413,
    );
  const identity = random();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO quant_universe_scopes(id,owner,scope_root,spec,created_at)
    SELECT ?,?,?,?,? WHERE (SELECT count(*) FROM quant_universe_scopes WHERE owner=?)<?`,
  )
    .bind(identity, owner, root, text, NOW(), owner, LIMITS.savedScopes)
    .run();
  const row = await env.DB.prepare(
    "SELECT * FROM quant_universe_scopes WHERE owner=? AND scope_root=?",
  )
    .bind(owner, root)
    .first();
  if (!row) fail("UNIVERSE_SCOPE_LIMIT", "已保存完整范围达到数量上限", 429);
  return { scopeRef: scopeRef(row), scope, created: row.id === identity };
}

export async function readScope(env, owner, ref) {
  validateScopeRef(ref);
  const row = await env.DB.prepare(
    "SELECT * FROM quant_universe_scopes WHERE id=? AND owner=? AND scope_root=?",
  )
    .bind(ref.scopeId, owner, ref.scopeRoot)
    .first();
  if (!row) fail("NOT_FOUND", "完整股票池范围不存在", 404);
  let scope;
  try {
    scope = JSON.parse(row.spec);
  } catch {
    fail("UNIVERSE_SCOPE_INTEGRITY", "股票池冻结内容损坏", 409);
  }
  if (
    byteLength(row.spec) > LIMITS.scopeBytes ||
    canonical(scope) !== row.spec ||
    (await digest(scope)) !== row.scope_root
  )
    fail("UNIVERSE_SCOPE_INTEGRITY", "股票池冻结根不匹配", 409);
  return { row, scope, scopeRef: scopeRef(row) };
}

export function bindWholeScope(strategy, scope) {
  const u = strategy?.universe;
  if (
    !u ||
    canonical(u.symbols) !== canonical(scope.symbols) ||
    u.start !== scope.start ||
    u.end !== scope.end
  )
    fail(
      "WHOLE_UNIVERSE_MISMATCH",
      "研究成员和日期必须与冻结完整筛选集合逐项相同；不会截断或补选",
    );
  for (const [key, expected] of [
    ["selection", scope.selection],
    ["resolutionHash", scope.resolutionHash],
    ["snapshotHash", scope.snapshotHash],
  ]) {
    if (u[key] !== undefined && canonical(u[key]) !== canonical(expected))
      fail("WHOLE_UNIVERSE_MISMATCH", "研究规则证据与冻结范围不一致");
  }
  if (u.subsetPolicy !== undefined && u.subsetPolicy !== "all")
    fail("WHOLE_UNIVERSE_MISMATCH", "新完整范围不接受手选或截取子集");
  return {
    ...strategy,
    universe: {
      ...u,
      symbols: [...scope.symbols],
      selection: scope.selection,
      resolutionHash: scope.resolutionHash,
      snapshotHash: scope.snapshotHash,
      subsetPolicy: "all",
      catalogSnapshot: {
        hash: scope.snapshotHash,
        asOf: scope.catalogSnapshot.asOf ?? null,
        historicalMembershipVerified: false,
      },
    },
  };
}

export async function experimentScope(env, owner, experimentId, version) {
  const row = await env.DB.prepare(
    "SELECT scope_id id,scope_root FROM quant_experiment_scopes WHERE owner=? AND experiment_id=? AND version=?",
  )
    .bind(owner, experimentId, version)
    .first();
  return row ? scopeRef(row) : null;
}

export function experimentScopeStatement(
  env,
  owner,
  experimentId,
  version,
  ref,
  spec,
  now,
) {
  return env.DB.prepare(
    `INSERT INTO quant_experiment_scopes(experiment_id,version,owner,scope_id,scope_root,created_at)
    SELECT v.experiment_id,v.version,?,?,?,? FROM quant_experiment_versions v JOIN quant_experiments e ON e.id=v.experiment_id
    WHERE v.experiment_id=? AND v.version=? AND e.owner=? AND v.spec=?
    AND NOT EXISTS(SELECT 1 FROM quant_experiment_scopes s WHERE s.experiment_id=v.experiment_id AND s.version=v.version)`,
  ).bind(
    owner,
    ref.scopeId,
    ref.scopeRoot,
    now,
    experimentId,
    version,
    owner,
    spec,
  );
}
