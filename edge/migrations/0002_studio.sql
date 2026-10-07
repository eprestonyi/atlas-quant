CREATE TABLE IF NOT EXISTS data_fields(id TEXT PRIMARY KEY,database_key TEXT NOT NULL,data_type TEXT NOT NULL,numeric_eligible INTEGER NOT NULL,alias TEXT,search_text TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS data_fields_database ON data_fields(database_key,numeric_eligible,id);
CREATE INDEX IF NOT EXISTS data_fields_alias ON data_fields(alias);
CREATE TABLE IF NOT EXISTS research_universes(id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,symbol_count INTEGER NOT NULL,search_text TEXT NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_universes_category ON research_universes(category,id);
CREATE TABLE IF NOT EXISTS code_projects(id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,language TEXT NOT NULL,code TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS code_projects_owner ON code_projects(owner,updated_at);
