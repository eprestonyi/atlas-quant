/* Immutable user edits of an owned, frozen fitted function. No forecast evidence inheritance. */
CREATE TABLE IF NOT EXISTS quant_model_functions (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 name TEXT NOT NULL, artifact_id TEXT NOT NULL, parent_artifact_id TEXT NOT NULL,
 source_ref TEXT NOT NULL, object_key TEXT NOT NULL, object_sha256 TEXT NOT NULL,
 byte_length INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner, request_id)
);
CREATE INDEX IF NOT EXISTS quant_model_functions_owner ON quant_model_functions(owner,created_at DESC,id DESC);
