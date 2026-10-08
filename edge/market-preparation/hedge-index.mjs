/** Bounded query metadata for server-admitted whole-asset construction fits. */
import { canonical, digest, fail } from "./common.mjs";

export const MARKET_HEDGE_POLICY = "source_asset_targets_v1";
const reject = () =>
  fail(
    "MARKET_HEDGE_REFERENCES",
    "资产构造目标引用与冻结完整股票池不一致",
    409,
  );

export async function marketAssetTargets(symbols) {
  if (
    !Array.isArray(symbols) ||
    !symbols.length ||
    symbols.length > 1000 ||
    new Set(symbols).size !== symbols.length ||
    symbols.some((s) => typeof s !== "string" || !/^\d{6}\.(SH|SZ)$/.test(s))
  )
    reject();
  const targets = [];
  for (const symbol of [...symbols].sort()) {
    const content = {
      kind: "asset_price",
      symbols: [symbol],
      quantities: [1],
      unit: "CNY_adjusted_research_price",
      construction: "single_asset",
      formationStart: null,
      formationEnd: null,
      hedgeAudit: {},
    };
    targets.push({
      id: "target_" + (await digest(content)).slice(0, 24),
      ...content,
    });
  }
  return targets;
}

/** expectedIds is server-derived after owner/lease/source admission, never a DTO. */
export async function marketHedgeMetadata(row, expectedIds) {
  if (
    !Array.isArray(expectedIds) ||
    !expectedIds.length ||
    expectedIds.length > 1000 ||
    new Set(expectedIds).size !== expectedIds.length ||
    expectedIds.some(
      (id) => typeof id !== "string" || !/^target_[a-f0-9]{24}$/.test(id),
    ) ||
    !Array.isArray(row.targetIds) ||
    canonical(row.targetIds) !== canonical(expectedIds)
  )
    reject();
  return {
    targetIndexPolicy: MARKET_HEDGE_POLICY,
    targetCount: expectedIds.length,
    targetIdsRoot: await digest(expectedIds),
  };
}
