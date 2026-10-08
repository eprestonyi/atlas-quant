/** Published capacity declarations are scoped to a fresh, explicit runner capability. */
import { parse } from "../runtime.mjs";
import { PROFILE } from "./common.mjs";

export const AUTO_PROFILE = "pooled_asset_1000_auto_candidate_v1";
export const MARKET_RESEARCH_PROFILES = Object.freeze([PROFILE, AUTO_PROFILE]);
export const researchEnabled = (env) => env.MARKET_RESEARCH_ENABLED === "true";
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
      const automatic = admissionProfile === AUTO_PROFILE;
      const available =
        enabled && online && supportsMarket(capability, admissionProfile);
      return {
        admissionProfile,
        available,
        families: automatic ? ["mean_reversion"] : ["mean_reversion", "trend"],
        estimator: automatic ? "auto" : "ridge",
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
