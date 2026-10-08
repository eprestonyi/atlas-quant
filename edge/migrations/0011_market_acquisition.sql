/* A separate one-attempt market preparation queue; existing source formats remain unchanged. */
CREATE TABLE IF NOT EXISTS quant_market_jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, plan_id TEXT NOT NULL,
 plan_root TEXT NOT NULL, authorization_scope TEXT NOT NULL, status TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'queued', lease_token TEXT, lease_until TEXT, deadline TEXT,
 error TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner,request_id)
);
CREATE INDEX IF NOT EXISTS quant_market_jobs_queue ON quant_market_jobs(status,created_at,id);
CREATE TABLE IF NOT EXISTS quant_market_claims (
 request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE REFERENCES quant_market_jobs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_market_requests (
 request_key TEXT PRIMARY KEY, authorization_scope TEXT NOT NULL, definition TEXT NOT NULL,
 job_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state TEXT NOT NULL, receipt_id TEXT,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quant_market_requests_job ON quant_market_requests(job_id,state);
CREATE INDEX IF NOT EXISTS quant_market_requests_day ON quant_market_requests(created_at);
CREATE TABLE IF NOT EXISTS quant_market_receipts (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, authorization_scope TEXT NOT NULL,
 object_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL,
 http_status INTEGER NOT NULL, retrieved_at TEXT NOT NULL, source_kind TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_market_job_receipts (
 job_id TEXT NOT NULL, request_key TEXT NOT NULL, receipt_id TEXT NOT NULL,
 byte_length INTEGER NOT NULL, PRIMARY KEY(job_id,request_key)
);
CREATE TABLE IF NOT EXISTS quant_market_publications (
 job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, lease_token TEXT NOT NULL, manifest TEXT NOT NULL,
 manifest_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_market_parts (
 job_id TEXT NOT NULL, collection TEXT NOT NULL, ordinal INTEGER NOT NULL,
 object_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL,
 PRIMARY KEY(job_id,collection,ordinal)
);
CREATE TABLE IF NOT EXISTS quant_market_datasets (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE,
 dataset_root TEXT NOT NULL, plan_root TEXT NOT NULL, scope_root TEXT NOT NULL,
 profile TEXT NOT NULL, manifest TEXT NOT NULL, row_value_root TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_run_market_datasets (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), owner TEXT NOT NULL, dataset_id TEXT NOT NULL,
 dataset_root TEXT NOT NULL, profile TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quant_experiment_market_datasets (
 experiment_id TEXT NOT NULL, version INTEGER NOT NULL, owner TEXT NOT NULL,
 dataset_id TEXT NOT NULL, dataset_root TEXT NOT NULL, profile TEXT NOT NULL,
 scope_id TEXT NOT NULL, scope_root TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(experiment_id,version), FOREIGN KEY(experiment_id,version) REFERENCES quant_experiment_versions(experiment_id,version)
);
