/** Published capacity declarations are scoped to a fresh, explicit runner capability. */
import { parse } from "../runtime.mjs";
import { PROFILE } from "./common.mjs";

export const AUTO_PROFILE = "pooled_asset_1000_auto_candidate_v1";
export const TREND_AUTO_PROFILE = "pooled_asset_1000_trend_auto_v1";
export const MARKET_RESEARCH_PROFILES = Object.freeze([
  PROFILE, AUTO_PROFILE, TREND_AUTO_PROFILE,
]);
export function profileModel(profile) {
  if (profile === PROFILE)
    return { families: ["mean_reversion", "trend"], estimator: "ridge" };
  if (profile === AUTO_PROFILE)
    return { families: ["mean_reversion"], estimator: "auto" };
  if (profile === TREND_AUTO_PROFILE)
    return { families: ["trend"], estimator: "auto" };
  return null;
}
export const researchEnabled = (env, profile = null) =>
  env.MARKET_RESEARCH_ENABLED === "true" &&
  (profile !== TREND_AUTO_PROFILE || env.MARKET_TREND_AUTO_ENABLED === "true");
export function supportsMarket(input, profile) {
  return (
    MARKET_RESEARCH_PROFILES.includes(profile) &&
    Array.isArray(input.marketResearchProfiles) &&
    input.marketResearchProfiles.includes(profile) &&
    Array.isArray(input.transportFormats) &&
    input.transportFormats.includes("atlas.quant.bundle/1")
  );
}

export async function marketResearchAdmissions(env) {
  const row = await env.DB.prepare(
    "SELECT value,updated_at FROM meta WHERE key='runner'",
  ).first();
  const capability = parse(row?.value, {});
  const online = !!row && Date.now() - Date.parse(row.updated_at) < 120000;
  const enabled = researchEnabled(env);
  const researchAdmissions = MARKET_RESEARCH_PROFILES.map(
    (admissionProfile) => {
      const declared = profileModel(admissionProfile);
      const available =
        researchEnabled(env, admissionProfile) && online &&
        supportsMarket(capability, admissionProfile);
      return {
        admissionProfile,
        available,
        families: declared.families,
        estimator: declared.estimator,
        targetKind: "asset_price",
        executionEnabled: false,
        maxSymbols: 1000,
        maxCalendarDays: 366,
        maxFactors: 16,
        innerFolds: 2,
        outerFolds: 2,
        minRefitDays: 20,
        reason: !enabled
          ? "MARKET_RESEARCH_DISABLED"
          : !researchEnabled(env, admissionProfile)
            ? "MARKET_TREND_AUTO_DISABLED"
            : !online
              ? "RUNNER_OFFLINE"
              : !supportsMarket(capability, admissionProfile)
                ? "RUNNER_UPGRADE_REQUIRED"
                : null,
      };
    },
  );
  return {
    researchAdmissions,
    preferredResearchAdmission:
      researchAdmissions.find(
        (x) => x.admissionProfile === AUTO_PROFILE && x.available,
      )?.admissionProfile ??
      researchAdmissions.find((x) => x.available)?.admissionProfile ??
      null,
  };
}
