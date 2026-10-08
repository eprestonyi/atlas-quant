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
