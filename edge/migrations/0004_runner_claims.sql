/* Retain compact claim receipts with job history. Do not apply an automatic TTL:
   an offline runner may still hold the encrypted request ID after a lost ACK. */
CREATE TABLE IF NOT EXISTS runner_claims (
  request_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
  created_at TEXT NOT NULL
);
