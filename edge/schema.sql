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

/* Independent financial workspace; old research claims never read these tables. */
CREATE TABLE IF NOT EXISTS financial_registry_entries (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('calendar','unit_proof')),
 owner TEXT NOT NULL, object_key TEXT NOT NULL, sha256 TEXT NOT NULL,
 byte_length INTEGER NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_inputs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, parent_id TEXT, calendar_ref TEXT NOT NULL,
 proof_refs TEXT NOT NULL, declared_bytes INTEGER NOT NULL, source_key TEXT,
 source_hash TEXT, source_bytes INTEGER, canonical_publication_id TEXT,
 roots TEXT, metadata TEXT, error TEXT, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner,request_id)
);
CREATE INDEX IF NOT EXISTS financial_inputs_owner ON financial_inputs(owner,created_at,id);
CREATE TABLE IF NOT EXISTS financial_jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, input_id TEXT NOT NULL REFERENCES financial_inputs(id),
 kind TEXT NOT NULL CHECK(kind IN ('financial_validate','financial_revise','financial_prepare')),
 status TEXT NOT NULL, spec TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'queued', lease_token TEXT, lease_until TEXT, deadline TEXT,
 error TEXT, publication_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner,request_id)
);
CREATE INDEX IF NOT EXISTS financial_jobs_queue ON financial_jobs(status,created_at,id);
CREATE INDEX IF NOT EXISTS financial_jobs_owner ON financial_jobs(owner,input_id,created_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS financial_jobs_one_active ON financial_jobs(owner)
 WHERE status IN ('queued','running','cancel_requested');
CREATE TABLE IF NOT EXISTS financial_claims (
 request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE REFERENCES financial_jobs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_publications (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE REFERENCES financial_jobs(id),
 manifest_text TEXT NOT NULL, manifest_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'staging',
 total_bytes INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_chunks (
 publication_id TEXT NOT NULL REFERENCES financial_publications(id), collection TEXT NOT NULL,
 ordinal INTEGER NOT NULL, start_row INTEGER, row_count INTEGER, sha256 TEXT NOT NULL,
 byte_length INTEGER NOT NULL, object_key TEXT NOT NULL,
 PRIMARY KEY(publication_id,collection,ordinal)
);
CREATE TABLE IF NOT EXISTS financial_records (
 publication_id TEXT NOT NULL REFERENCES financial_publications(id), collection TEXT NOT NULL,
 ordinal INTEGER NOT NULL, chunk_ordinal INTEGER NOT NULL, item_index INTEGER NOT NULL,
 record_id TEXT, symbol TEXT, state_id TEXT, event_id TEXT, period_end TEXT, date TEXT,
 status TEXT, dependency_start INTEGER, dependency_count INTEGER, through_date TEXT, audit TEXT,
 PRIMARY KEY(publication_id,collection,ordinal)
);
CREATE UNIQUE INDEX IF NOT EXISTS financial_record_ids ON financial_records(publication_id,collection,record_id) WHERE record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS financial_records_filter ON financial_records(publication_id,collection,state_id,symbol,date,status);
CREATE INDEX IF NOT EXISTS financial_records_coordinates ON financial_records(publication_id,collection,symbol,date);
CREATE INDEX IF NOT EXISTS financial_records_dependencies ON financial_records(publication_id,collection,event_id,ordinal);
CREATE TABLE IF NOT EXISTS financial_preparations (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, input_id TEXT NOT NULL REFERENCES financial_inputs(id),
 publication_id TEXT NOT NULL UNIQUE REFERENCES financial_publications(id), roots TEXT NOT NULL,
 metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS financial_preparations_input ON financial_preparations(owner,input_id,created_at,id);

/* Independent provider-enabled acquisition; no existing runner can claim these rows. */
CREATE TABLE IF NOT EXISTS financial_acquisition_plans (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 plan_root TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner,request_id)
);
CREATE INDEX IF NOT EXISTS financial_acquisition_plans_owner ON financial_acquisition_plans(owner,created_at,id);
CREATE TABLE IF NOT EXISTS financial_acquisition_jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES financial_acquisition_plans(id),
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, spec TEXT NOT NULL,
 status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'queued', lease_token TEXT,
 lease_until TEXT, deadline TEXT, result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner,request_id)
);
CREATE INDEX IF NOT EXISTS financial_acquisition_jobs_queue ON financial_acquisition_jobs(status,created_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS financial_acquisition_owner_active ON financial_acquisition_jobs(owner)
 WHERE status IN ('queued','running','cancel_requested');
CREATE TABLE IF NOT EXISTS financial_acquisition_claims (
 request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE REFERENCES financial_acquisition_jobs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_acquisition_requests (
 request_key TEXT PRIMARY KEY, authorization_scope TEXT NOT NULL, definition TEXT NOT NULL,
 job_id TEXT NOT NULL REFERENCES financial_acquisition_jobs(id), attempt_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('intent','received','outcome_unknown','failed')),
 receipt_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_acquisition_cache (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE REFERENCES financial_acquisition_requests(request_key),
 authorization_scope TEXT NOT NULL, object_key TEXT NOT NULL, sha256 TEXT NOT NULL,
 byte_length INTEGER NOT NULL, http_status INTEGER NOT NULL, retrieved_at TEXT NOT NULL, source_kind TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_acquisition_publications (
 job_id TEXT PRIMARY KEY REFERENCES financial_acquisition_jobs(id), manifest TEXT NOT NULL,
 manifest_hash TEXT NOT NULL, input_id TEXT NOT NULL, calendar_ref TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_acquisition_chunks (
 job_id TEXT NOT NULL REFERENCES financial_acquisition_jobs(id), collection TEXT NOT NULL,
 ordinal INTEGER NOT NULL, sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL, object_key TEXT NOT NULL,
 PRIMARY KEY(job_id,collection,ordinal)
);

CREATE INDEX IF NOT EXISTS financial_acquisition_requests_day ON financial_acquisition_requests(created_at);

/* Dedicated provider-free dataset composition; never visible to legacy claims. */
CREATE TABLE IF NOT EXISTS quant_dataset_plans (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 plan_root TEXT NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner,request_id)
);
CREATE TABLE IF NOT EXISTS quant_dataset_jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES quant_dataset_plans(id),
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'queued',
 lease_token TEXT, lease_until TEXT, deadline TEXT, error TEXT, dataset_id TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS quant_dataset_one_active ON quant_dataset_jobs(owner)
 WHERE status IN ('queued','running','cancel_requested');
CREATE TABLE IF NOT EXISTS quant_dataset_claims (
 request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE REFERENCES quant_dataset_jobs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_dataset_stages (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES quant_dataset_jobs(id), owner TEXT NOT NULL,
 lease_token TEXT NOT NULL, dataset_id TEXT NOT NULL, dataset_root TEXT NOT NULL,
 manifest_text TEXT NOT NULL, total_bytes INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'staging',
 summary TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_dataset_parts (
 stage_id TEXT NOT NULL REFERENCES quant_dataset_stages(id), component_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL, object_key TEXT NOT NULL,
 PRIMARY KEY(stage_id,component_id,ordinal)
);
CREATE TABLE IF NOT EXISTS quant_research_datasets (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, dataset_root TEXT NOT NULL,
 stage_id TEXT NOT NULL UNIQUE REFERENCES quant_dataset_stages(id), status TEXT NOT NULL,
 scope TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_datasets_owner ON quant_research_datasets(owner,created_at);
CREATE TABLE IF NOT EXISTS quant_dataset_dependencies (
 job_id TEXT NOT NULL REFERENCES quant_dataset_jobs(id), owner TEXT NOT NULL,
 kind TEXT NOT NULL, reference_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 PRIMARY KEY(job_id,kind,reference_id)
);
CREATE INDEX IF NOT EXISTS quant_dataset_pinned_source ON quant_dataset_dependencies(kind,reference_id);
CREATE TABLE IF NOT EXISTS quant_dataset_coverage (
 dataset_id TEXT NOT NULL REFERENCES quant_research_datasets(id), ordinal INTEGER NOT NULL,
 symbol TEXT NOT NULL, state_id TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(dataset_id,ordinal), UNIQUE(dataset_id,symbol,state_id)
);
CREATE TABLE IF NOT EXISTS quant_run_datasets (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), owner TEXT NOT NULL, dataset_id TEXT NOT NULL REFERENCES quant_research_datasets(id),
 dataset_root TEXT NOT NULL, profile TEXT NOT NULL, admission TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quant_experiment_datasets (
 experiment_id TEXT NOT NULL REFERENCES quant_experiments(id), version INTEGER NOT NULL,
 owner TEXT NOT NULL, dataset_id TEXT NOT NULL REFERENCES quant_research_datasets(id),
 dataset_root TEXT NOT NULL, profile TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(experiment_id,version)
);
CREATE TABLE IF NOT EXISTS quant_universe_scopes (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope_root TEXT NOT NULL,
 spec TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(owner,scope_root)
);
CREATE TABLE IF NOT EXISTS quant_experiment_scopes (
 experiment_id TEXT NOT NULL REFERENCES quant_experiments(id), version INTEGER NOT NULL,
 owner TEXT NOT NULL, scope_id TEXT NOT NULL REFERENCES quant_universe_scopes(id),
 scope_root TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,version)
);
CREATE TABLE IF NOT EXISTS quant_run_scopes (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), owner TEXT NOT NULL,
 scope_id TEXT NOT NULL REFERENCES quant_universe_scopes(id), scope_root TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_market_plans (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope_id TEXT NOT NULL REFERENCES quant_universe_scopes(id),
 scope_root TEXT NOT NULL, plan_root TEXT NOT NULL, profile TEXT NOT NULL,
 spec TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(owner,plan_root)
);
CREATE INDEX IF NOT EXISTS quant_universe_scopes_owner ON quant_universe_scopes(owner,created_at);
CREATE INDEX IF NOT EXISTS quant_market_plans_owner ON quant_market_plans(owner,created_at);
