-- Primary PostgreSQL schema for AI People Modelling app
CREATE TABLE IF NOT EXISTS job_descriptions (
    id BIGSERIAL PRIMARY KEY,
    position TEXT,
    job_title TEXT,
    grade TEXT,
    filepath TEXT,
    content TEXT,
    original_filename TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_descriptions_filepath ON job_descriptions (filepath);
CREATE INDEX IF NOT EXISTS idx_job_descriptions_job_title ON job_descriptions (job_title);

CREATE TABLE IF NOT EXISTS candidates (
    id BIGSERIAL PRIMARY KEY,
    data TEXT
);

CREATE TABLE IF NOT EXISTS position_profiles (
    id BIGSERIAL PRIMARY KEY,
    data TEXT
);

CREATE TABLE IF NOT EXISTS talent_cards (
    id BIGSERIAL PRIMARY KEY,
    data TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    username TEXT,
    action TEXT,
    module TEXT,
    entity_type TEXT,
    entity_id TEXT,
    details TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_module_action ON audit_logs (module, action);
