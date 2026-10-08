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
