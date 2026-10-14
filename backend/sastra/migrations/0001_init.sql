-- SASTRA schema v1. Portable SQL: runs on SQLite (default, tested) and PostgreSQL
-- (adapter path). Keys are TEXT UUIDs, times are UNIX epoch seconds (DOUBLE PRECISION).
-- Note: statements are split on semicolons, so never put a semicolon inside a comment.

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    totp_secret_enc TEXT,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    device_name TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    last_seen_at DOUBLE PRECISION,
    revoked_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq BIGINT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'general',
    created_at DOUBLE PRECISION NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    privacy TEXT NOT NULL DEFAULT 'normal',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    project_id TEXT,
    subject TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    embedding TEXT,
    contradicts_id TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_memories_scope ON memories(project_id, category, status);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    project_id TEXT,
    title TEXT NOT NULL,
    request TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    mode INTEGER NOT NULL,
    plan TEXT NOT NULL DEFAULT '[]',
    step_index INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    idempotency_key TEXT UNIQUE,
    limits TEXT NOT NULL,
    usage TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at DOUBLE PRECISION,
    lease_owner TEXT,
    lease_expires_at DOUBLE PRECISION,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_tasks_queue ON tasks(state, next_run_at);

CREATE TABLE IF NOT EXISTS task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts DOUBLE PRECISION NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_task_events ON task_events(task_id, ts);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    risk TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at DOUBLE PRECISION,
    decision_note TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    scope TEXT NOT NULL,
    max_uses INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    expires_at DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    revoked_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    tool TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    args TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT,
    error TEXT,
    duration_ms INTEGER,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    task_id TEXT,
    project_id TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_usage_ts ON usage_ledger(ts);

CREATE TABLE IF NOT EXISTS data_flows (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    destination TEXT NOT NULL,
    provider TEXT NOT NULL,
    data_class TEXT NOT NULL,
    bytes_out INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    retention_note TEXT NOT NULL,
    task_id TEXT
);

CREATE TABLE IF NOT EXISTS research_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    question TEXT NOT NULL,
    report_md TEXT NOT NULL,
    sources TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size BIGINT NOT NULL,
    version INTEGER NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0,
    mime TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    request TEXT NOT NULL,
    kind TEXT NOT NULL,
    project_id TEXT,
    interval_minutes INTEGER,
    daily_at TEXT,
    budget_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at DOUBLE PRECISION NOT NULL,
    last_run_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    title TEXT NOT NULL,
    observation TEXT NOT NULL,
    proposal TEXT NOT NULL,
    status TEXT NOT NULL,
    test_result TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    protected INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    decided_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    lesson_id TEXT,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS ventures (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idea',
    budget_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    project_id TEXT,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS venture_entries (
    id TEXT PRIMARY KEY,
    venture_id TEXT NOT NULL REFERENCES ventures(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL,
    basis TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    ts DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    name TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    labels TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(name, ts);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    task_id TEXT,
    read_at DOUBLE PRECISION
);
