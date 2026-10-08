/** Independent v0.6.0 -> financial workspace migration, real isolated D1/R2.
 * Historical schema bytes are embedded to support shallow CI checkouts without
 * fetching tags. Origin: v0.6.0 / a906f12ad89c5f84252fcbae7abc9741f59d028f.
 * No provider, model fit or live resource is used.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import { buildWorkerSource } from "../scripts/worker-source.mjs";

const RELEASED_SCHEMA = String.raw`PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategies(id TEXT PRIMARY KEY, owner TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, name TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS strategies_owner ON strategies(owner,updated_at);
CREATE TABLE IF NOT EXISTS strategy_versions(strategy_id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(strategy_id,version));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, data_source TEXT NOT NULL, spec TEXT NOT NULL, dataset_key TEXT, result_key TEXT, summary TEXT, error TEXT, lease_token TEXT, lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner,created_at);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
CREATE TABLE IF NOT EXISTS factors(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, expression TEXT NOT NULL, direction INTEGER NOT NULL, category TEXT NOT NULL, author TEXT NOT NULL, license TEXT NOT NULL, source_url TEXT, fork_of TEXT, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS factors_public ON factors(status,created_at);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, owner TEXT, action TEXT NOT NULL, entity_id TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_buckets(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS data_fields(id TEXT PRIMARY KEY,database_key TEXT NOT NULL,data_type TEXT NOT NULL,numeric_eligible INTEGER NOT NULL,alias TEXT,search_text TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS data_fields_database ON data_fields(database_key,numeric_eligible,id);
CREATE INDEX IF NOT EXISTS data_fields_alias ON data_fields(alias);
CREATE TABLE IF NOT EXISTS research_universes(id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,symbol_count INTEGER NOT NULL,search_text TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_universes_category ON research_universes(category,id);
CREATE TABLE IF NOT EXISTS code_projects(id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,language TEXT NOT NULL,code TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS code_projects_owner ON code_projects(owner,updated_at);

CREATE TABLE IF NOT EXISTS quant_experiments (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, spec TEXT NOT NULL,
  parent_id TEXT, archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_experiments_owner ON quant_experiments(owner, archived, updated_at);
CREATE TABLE IF NOT EXISTS quant_experiment_versions (
  experiment_id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,version)
);
CREATE TABLE IF NOT EXISTS quant_runs (
  job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, experiment_id TEXT NOT NULL,
  experiment_version INTEGER NOT NULL, kind TEXT NOT NULL,
  forecast_artifact_id TEXT, source_forecast_id TEXT,
  snapshot_key TEXT, snapshot_hash TEXT, snapshot_fingerprint TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_runs_experiment ON quant_runs(owner,experiment_id,created_at);
CREATE TABLE IF NOT EXISTS quant_model_versions (
  id TEXT NOT NULL, owner TEXT NOT NULL, experiment_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL, family TEXT NOT NULL, config_hash TEXT NOT NULL,
  metadata TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(owner,id)
);
CREATE TABLE IF NOT EXISTS quant_forecast_artifacts (
  id TEXT NOT NULL, owner TEXT NOT NULL, job_id TEXT NOT NULL,
  experiment_id TEXT NOT NULL, model_version_id TEXT NOT NULL,
  artifact_key TEXT NOT NULL, artifact_hash TEXT NOT NULL,
  dataset_key TEXT NOT NULL, dataset_hash TEXT NOT NULL,
  data_fingerprint TEXT NOT NULL, prediction_config_hash TEXT NOT NULL,
  row_count INTEGER NOT NULL, target_count INTEGER NOT NULL,
  model_fit_count INTEGER NOT NULL, metadata TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(owner,id)
);
CREATE INDEX IF NOT EXISTS quant_forecast_experiment ON quant_forecast_artifacts(owner,experiment_id,created_at);
CREATE TABLE IF NOT EXISTS quant_executions (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, experiment_id TEXT NOT NULL,
  forecast_artifact_id TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_execution_source ON quant_executions(owner,forecast_artifact_id,created_at);
CREATE TABLE IF NOT EXISTS quant_comparisons (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL, members TEXT NOT NULL, metadata TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_comparisons_owner ON quant_comparisons(owner,created_at);

/* Retain compact claim receipts with job history. Do not apply an automatic TTL:
   an offline runner may still hold the encrypted request ID after a lost ACK. */
CREATE TABLE IF NOT EXISTS runner_claims (
  request_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_bundle_stages (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
  lease_token TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  manifest_text TEXT NOT NULL,
  manifest_key TEXT NOT NULL,
  metadata TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('staging','verified','committed','aborted')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_bundle_owner ON quant_bundle_stages(owner,bundle_id,status);
CREATE TABLE IF NOT EXISTS quant_bundle_chunks (
  stage_id TEXT NOT NULL REFERENCES quant_bundle_stages(id),
  collection TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  start_row INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  object_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(stage_id,collection,ordinal)
);
CREATE TABLE IF NOT EXISTS quant_bundle_records (
  stage_id TEXT NOT NULL REFERENCES quant_bundle_stages(id),
  collection TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  chunk_ordinal INTEGER NOT NULL,
  item_index INTEGER NOT NULL,
  row_id TEXT,
  date TEXT,
  target_id TEXT,
  fit_id TEXT,
  reference_id TEXT,
  status TEXT,
  matured INTEGER NOT NULL DEFAULT 0,
  flags INTEGER NOT NULL DEFAULT 0,
  group_key TEXT,
  metadata TEXT NOT NULL,
  PRIMARY KEY(stage_id,collection,ordinal)
);
CREATE UNIQUE INDEX IF NOT EXISTS quant_bundle_record_ids ON quant_bundle_records(stage_id,collection,row_id) WHERE row_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS quant_bundle_record_page ON quant_bundle_records(stage_id,collection,date,target_id,row_id);
CREATE INDEX IF NOT EXISTS quant_bundle_record_status ON quant_bundle_records(stage_id,collection,status,date);
CREATE INDEX IF NOT EXISTS quant_bundle_record_target ON quant_bundle_records(stage_id,collection,target_id,date);
CREATE INDEX IF NOT EXISTS quant_bundle_record_reference ON quant_bundle_records(stage_id,collection,reference_id);
CREATE INDEX IF NOT EXISTS quant_bundle_record_group ON quant_bundle_records(stage_id,collection,group_key,date);
CREATE TABLE IF NOT EXISTS quant_bundle_runs (
  job_id TEXT PRIMARY KEY REFERENCES jobs(id),
  owner TEXT NOT NULL,
  stage_id TEXT NOT NULL REFERENCES quant_bundle_stages(id)
);
CREATE TABLE IF NOT EXISTS quant_bundle_forecasts (
  owner TEXT NOT NULL,
  forecast_id TEXT NOT NULL,
  stage_id TEXT NOT NULL REFERENCES quant_bundle_stages(id),
  dataset_stage_id TEXT,
  PRIMARY KEY(owner,forecast_id)
);
`;
const RELEASED_SCHEMA_SHA256 =
  "aa60d21f0fde20559acb1ca1fd5a90ba01af608ebbd3bded593dbd8a9a06620a";
const sha = (value) => createHash("sha256").update(value).digest("hex");
assert.equal(sha(RELEASED_SCHEMA), RELEASED_SCHEMA_SHA256);
const migration = await fs.readFile(
  new URL("../edge/migrations/0006_financial_workspace.sql", import.meta.url),
  "utf8",
);
const script = await buildWorkerSource({
  buildId: "independent-financial-upgrade-review",
});
const oldTables = [
  ...RELEASED_SCHEMA.matchAll(/CREATE TABLE IF NOT EXISTS (\w+)\s*\(/g),
]
  .map((x) => x[1])
  .sort();
const now = "2026-10-01T12:00:00.000Z";
const spec =
  '{ "name": "旧研究：保留原始字节", "research": {"mode":"legacy_long_only"}, "factor": null, "weight": 1.0 }';

async function oldSnapshot(db) {
  const result = {};
  for (const name of oldTables) {
    const rows = (
      await db.prepare(`SELECT * FROM ${name} ORDER BY rowid`).all()
    ).results;
    const schema = await db
      .prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name=?")
      .bind(name)
      .first();
    result[name] = { schema: schema.sql, rows };
  }
  return result;
}

async function upgraded(t, enabled = false, currentWorker = false) {
  const mf = new Miniflare({
    modules: true,
    script,
    compatibilityDate: "2026-08-01",
    d1Databases: ["DB"],
    r2Buckets: ["ARTIFACTS"],
    bindings: {
      RUNNER_SECRET: "isolated-upgrade-review",
      ...(enabled ? { FINANCIAL_WORKSPACE_ENABLED: "true" } : {}),
    },
  });
  t.after(() => mf.dispose());
  const db = await mf.getD1Database("DB"),
    bucket = await mf.getR2Bucket("ARTIFACTS");
  await db.exec(RELEASED_SCHEMA.replaceAll("\n", " "));
  const owner = randomUUID(),
    token = randomUUID() + "." + randomUUID();
  const ids = {
    strategy: randomUUID(),
    queued: randomUUID(),
    completed: randomUUID(),
    failed: randomUUID(),
    receipt: randomUUID(),
  };
  await db
    .prepare("INSERT INTO workspaces VALUES(?,?,?,?)")
    .bind(owner, sha(token), "旧工作区", now)
    .run();
  await db
    .prepare("INSERT INTO strategies VALUES(?,?,?,?,?,?,?)")
    .bind(ids.strategy, owner, 7, "不可覆盖策略", spec, now, now)
    .run();
  await db
    .prepare("INSERT INTO strategy_versions VALUES(?,?,?,?)")
    .bind(ids.strategy, 7, spec, now)
    .run();
  for (const [status, jobId] of [
    ["queued", ids.queued],
    ["completed", ids.completed],
    ["failed", ids.failed],
  ]) {
    await db
      .prepare(
        "INSERT INTO jobs(id,owner,name,status,data_source,spec,result_key,summary,error,lease_token,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
      )
      .bind(
        jobId,
        owner,
        "迁移保留 " + status,
        status,
        "demo",
        spec,
        status === "completed" ? "legacy/retained.json" : null,
        status === "completed"
          ? '{"evidenceStatus":"NO_VALIDATED_EDGE","fixture":true}'
          : null,
        status === "failed"
          ? '{"code":"RETAIN_ME","message":"历史失败不得删除"}'
          : null,
        status === "queued" ? null : randomUUID(),
        null,
        now,
        now,
      )
      .run();
  }
  await db
    .prepare("INSERT INTO runner_claims VALUES(?,?,?)")
    .bind(ids.receipt, ids.completed, now)
    .run();
  await bucket.put(
    "legacy/retained.json",
    '{"migrationEvidenceOnly":true,"preserve":-0.0}',
  );
  const before = await oldSnapshot(db);
  await db.exec(migration.replaceAll("\n", " "));
  const firstSchema = (
    await db
      .prepare(
        "SELECT type,name,sql FROM sqlite_master WHERE name LIKE 'financial_%' ORDER BY type,name",
      )
      .all()
  ).results;
  await db.exec(migration.replaceAll("\n", " "));
  const secondSchema = (
    await db
      .prepare(
        "SELECT type,name,sql FROM sqlite_master WHERE name LIKE 'financial_%' ORDER BY type,name",
      )
      .all()
  ).results;
  assert.deepEqual(
    firstSchema,
    secondSchema,
    "Repeated migration must not mutate the new schema",
  );
  assert.deepEqual(
    await oldSnapshot(db),
    before,
    "Both migrations must preserve every old table and stored row exactly",
  );
  assert.equal(
    await (await bucket.get("legacy/retained.json")).text(),
    '{"migrationEvidenceOnly":true,"preserve":-0.0}',
  );
  // A current Worker requires the complete additive migration chain even when
  // every new capability is disabled. The 0006-only test below remains isolated.
  if (currentWorker) {
    for (const filename of [
      "0007_financial_acquisition.sql",
      "0008_hosted_datasets.sql",
    ]) {
      const sql = (
        await fs.readFile(
          new URL("../edge/migrations/" + filename, import.meta.url),
          "utf8",
        )
      ).replaceAll("\n", " ");
      await db.exec(sql);
      await db.exec(sql);
    }
    assert.deepEqual(
      await oldSnapshot(db),
      before,
      "Current additive migrations preserve every released row and schema",
    );
    assert.deepEqual(
      (await db.prepare("PRAGMA foreign_key_check").all()).results,
      [],
    );
  }
  const cookie = "aq_session=" + token;
  async function request(path, data) {
    const response = await mf.dispatchFetch(
      "https://upgrade.test/quant/api" + path,
      {
        method: data === undefined ? "GET" : "POST",
        headers: {
          "content-type": "application/json",
          ...(path.startsWith("/runner/")
            ? { authorization: "Bearer isolated-upgrade-review" }
            : { cookie }),
        },
        body: data === undefined ? undefined : JSON.stringify(data),
      },
    );
    return { status: response.status, body: await response.json() };
  }
  async function financialJob() {
    const inputId = randomUUID(),
      jobId = randomUUID();
    await db
      .prepare(
        "INSERT INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      )
      .bind(
        inputId,
        owner,
        "新财务准备",
        "validating",
        randomUUID(),
        "[]",
        0,
        randomUUID(),
        "a".repeat(64),
        now,
        now,
      )
      .run();
    await db
      .prepare(
        "INSERT INTO financial_jobs(id,owner,input_id,kind,status,spec,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
      )
      .bind(
        jobId,
        owner,
        inputId,
        "financial_validate",
        "queued",
        "{}",
        randomUUID(),
        "b".repeat(64),
        now,
        now,
      )
      .run();
    return jobId;
  }
  return { db, bucket, request, financialJob, ids, owner, before, firstSchema };
}

test("published v0.6.0 schema accepts 0006 twice without altering historical rows or evidence", async (t) => {
  const { db, firstSchema } = await upgraded(t);
  assert.equal(
    await db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='quant_run_datasets'",
      )
      .first(),
    null,
    "0006-specific migration test must not apply later dataset tables",
  );
  const tables = firstSchema
    .filter((x) => x.type === "table")
    .map((x) => x.name)
    .sort();
  assert.deepEqual(tables, [
    "financial_chunks",
    "financial_claims",
    "financial_inputs",
    "financial_jobs",
    "financial_preparations",
    "financial_publications",
    "financial_records",
    "financial_registry_entries",
  ]);
  const columns = (
    await db.prepare("PRAGMA table_info(financial_records)").all()
  ).results.map((x) => x.name);
  assert.ok(columns.includes("through_date") && columns.includes("audit"));
  assert.deepEqual(
    (await db.prepare("PRAGMA foreign_key_check").all()).results,
    [],
  );
});

test("migration does not enable the financial flag or let new work enter the old queue", async (t) => {
  const { db, request, financialJob, ids } = await upgraded(t, false, true);
  const financialId = await financialJob();
  const capabilities = await request("/financial/capabilities");
  assert.equal(capabilities.status, 200);
  assert.equal(capabilities.body.enabled, false);
  assert.deepEqual(capabilities.body.operations, {
    upload: false,
    validate: false,
    prepare: false,
    researchBinding: false,
  });
  const blockedUpload = await request("/financial/inputs", {
    requestId: randomUUID(),
    name: "Disabled capability must not create input",
    byteLength: 2,
    calendarRef: randomUUID(),
    proofRefs: [],
  });
  assert.equal(blockedUpload.status, 503);
  assert.equal(
    blockedUpload.body.error.code,
    "FINANCIAL_CAPABILITY_UNAVAILABLE",
  );
  const denied = await request("/runner/financial/claim", {
    requestId: randomUUID(),
    capability: "financial-input/v1",
    engineVersion: "0.6.0",
  });
  assert.equal(denied.status, 503);
  assert.equal(denied.body.error.code, "FINANCIAL_CAPABILITY_UNAVAILABLE");
  assert.equal(
    (await db.prepare("SELECT COUNT(*) n FROM financial_claims").first()).n,
    0,
  );
  const claimed = await request("/runner/claim", {
    requestId: randomUUID(),
    engineVersion: "0.3.0",
  });
  assert.equal(claimed.status, 200);
  assert.equal(claimed.body.job.id, ids.queued);
  assert.equal(
    (
      await db
        .prepare("SELECT status FROM financial_jobs WHERE id=?")
        .bind(financialId)
        .first()
    ).status,
    "queued",
  );
  const noFinancialFallback = await request("/runner/claim", {
    requestId: randomUUID(),
    engineVersion: "0.6.0",
  });
  assert.equal(noFinancialFallback.body.job, null);
});

test("enabled financial and research consumers keep distinct claims, leases, heartbeats and completion authority", async (t) => {
  const { db, request, financialJob, ids } = await upgraded(t, true, true);
  const financialId = await financialJob(),
    requestId = randomUUID();
  const financial = await request("/runner/financial/claim", {
    requestId,
    capability: "financial-input/v1",
    engineVersion: "0.6.0",
  });
  assert.equal(financial.status, 200);
  assert.equal(financial.body.job.id, financialId);
  assert.equal(
    (
      await db
        .prepare("SELECT status FROM jobs WHERE id=?")
        .bind(ids.queued)
        .first()
    ).status,
    "queued",
  );
  const financialBefore = await db
    .prepare("SELECT * FROM financial_jobs WHERE id=?")
    .bind(financialId)
    .first();
  const research = await request("/runner/claim", {
    requestId,
    engineVersion: "0.6.0",
  });
  assert.equal(research.status, 200);
  assert.equal(research.body.job.id, ids.queued);
  assert.deepEqual(
    await db
      .prepare("SELECT * FROM financial_jobs WHERE id=?")
      .bind(financialId)
      .first(),
    financialBefore,
  );
  const researchBefore = await db
    .prepare("SELECT * FROM jobs WHERE id=?")
    .bind(ids.queued)
    .first();
  const wrongOldHeartbeat = await request("/runner/heartbeat", {
    id: financialId,
    leaseToken: financial.body.job.leaseToken,
  });
  assert.equal(wrongOldHeartbeat.body.leaseValid, false);
  const wrongNewHeartbeat = await request("/runner/financial/heartbeat", {
    capability: "financial-input/v1",
    engineVersion: "0.6.0",
    state: "busy",
    phase: "checking_inputs",
    jobId: ids.queued,
    leaseToken: research.body.job.leaseToken,
  });
  assert.equal(wrongNewHeartbeat.status, 409);
  assert.equal(
    (
      await request("/runner/complete", {
        id: financialId,
        leaseToken: financial.body.job.leaseToken,
        error: { code: "WRONG_QUEUE", message: "must reject" },
      })
    ).status,
    409,
  );
  assert.equal(
    (
      await request("/runner/financial/fail", {
        jobId: ids.queued,
        leaseToken: research.body.job.leaseToken,
        error: { code: "WRONG_QUEUE", message: "must reject" },
      })
    ).status,
    409,
  );
  assert.deepEqual(
    await db.prepare("SELECT * FROM jobs WHERE id=?").bind(ids.queued).first(),
    researchBefore,
  );
  assert.deepEqual(
    await db
      .prepare("SELECT * FROM financial_jobs WHERE id=?")
      .bind(financialId)
      .first(),
    financialBefore,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT job_id FROM runner_claims WHERE request_id=?")
        .bind(requestId)
        .first()
    ).job_id,
    ids.queued,
  );
  assert.equal(
    (
      await db
        .prepare("SELECT job_id FROM financial_claims WHERE request_id=?")
        .bind(requestId)
        .first()
    ).job_id,
    financialId,
  );
  assert.equal(
    (
      await request("/runner/claim", {
        requestId: randomUUID(),
        engineVersion: "0.6.0",
      })
    ).body.job,
    null,
  );
  assert.equal(
    (
      await request("/runner/financial/claim", {
        requestId: randomUUID(),
        capability: "financial-input/v1",
        engineVersion: "0.6.0",
      })
    ).body.job,
    null,
  );
  assert.deepEqual(
    (await db.prepare("PRAGMA foreign_key_check").all()).results,
    [],
  );
});
