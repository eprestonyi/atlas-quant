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
