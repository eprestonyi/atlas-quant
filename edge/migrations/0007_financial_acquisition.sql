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
