/** Busy heartbeats keep the last explicit capability declaration for this version. */
import { parse } from "./runtime.mjs";
import { ApiError } from "./errors.mjs";

const FIELDS = [
  "factorPreprocessFormats",
  "functionSearchFormats",
  "returnStudyFormats",
  "contextSourceFormats",
  "datasetFormats",
  "snapshotFormats",
  "transportFormats",
  "financialResearchProfiles",
  "marketResearchProfiles",
];

export async function recordRunnerCapabilities(env, input, now, isClaim) {
  const prior = await env.DB.prepare(
    "SELECT value FROM meta WHERE key='runner'",
  ).first();
  const saved = parse(prior?.value, {});
  const changedVersion =
    input.engineVersion !== undefined &&
    input.engineVersion !== saved.engineVersion;
  const next = {
    state: input.state || input.status || "ready",
    engineVersion: input.engineVersion ?? saved.engineVersion ?? null,
  };
  for (const field of FIELDS) {
    if (
      input[field] !== undefined &&
      (!Array.isArray(input[field]) ||
        input[field].length > 32 ||
        input[field].some((x) => typeof x !== "string" || !x || x.length > 120))
    )
      throw new ApiError(
        "INVALID_RUNNER_CAPABILITY",
        "计算能力声明须为有界字符串列表",
      );
    next[field] =
      input[field] ?? (!isClaim && !changedVersion ? (saved[field] ?? []) : []);
  }
  await env.DB.prepare(
    "INSERT INTO meta(key,value,updated_at) VALUES('runner',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
  )
    .bind(JSON.stringify(next), now)
    .run();
}
