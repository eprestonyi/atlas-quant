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
