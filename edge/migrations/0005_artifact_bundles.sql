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
