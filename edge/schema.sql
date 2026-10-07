PRAGMA foreign_keys = ON;
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
