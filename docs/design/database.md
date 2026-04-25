---
updated: 2026-04-26
---

# Database Schema

Both SQLite and PostgreSQL supported. ORM uses `sa.JSON` columns (JSONB on PostgreSQL, TEXT with JSON serialization on SQLite). UUIDs stored as CHAR(32) on SQLite. Timestamps stored as ISO 8601 strings on SQLite.

**See also:**
- [Sessions](sessions.md) — `sessions`, `session_pending_messages`, `drafts` lifecycle
- [Telemetry](telemetry.md) — `session_turns`, `tool_calls`, `telemetry_minutely` semantics
- [Linear Integration](linear.md) — `linear_issues` table
- [Configuration](configuration.md) — `DATABASE_URL` env var

---

## Tables

- [sessions](#sessions-table)
- [session_messages](#session_messages-table)
- [llm_calls](#llm_calls-table)
- [tool_executions](#tool_executions-table)
- [tasks](#tasks-table)
- [task_sessions](#task_sessions-table)
- [artifacts](#artifacts-table)
- [linear_issues](#linear_issues-table)
- [session_turns](#session_turns-table)
- [tool_calls](#tool_calls-table)
- [telemetry_minutely](#telemetry_minutely-table)
- [session_pending_messages](#session_pending_messages-table)
- [drafts](#drafts-table)

> Logical schema below uses PostgreSQL syntax for illustration; SQLAlchemy ORM handles dialect differences.

---

### `sessions` table

Archived sessions.

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    backend_id VARCHAR(36) NOT NULL DEFAULT '',  -- owning backend instance ID (multi-backend isolation)
    created_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    session_type VARCHAR(20) NOT NULL,       -- 'one-shot', 'conversational', 'long-running'
    status VARCHAR(20) NOT NULL,             -- 'completed', 'failed', 'cancelled'
    title VARCHAR(200),                      -- auto-generated human-readable title
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX ix_sessions_backend_id ON sessions(backend_id);
```

### `session_messages` table

All messages within a session (prompts, responses, tool calls, tool output).

```sql
CREATE TABLE session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    sequence INTEGER NOT NULL,
    message_type VARCHAR(30) NOT NULL,       -- 'user_prompt', 'llm_text', 'tool_call', 'tool_output', 'error', 'session_end_ask'
    content TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, sequence)
);
```

### `llm_calls` table

LLM API call log (per-turn, no FK to sessions — sessions are in-memory until archival).

```sql
CREATE TABLE llm_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,                    -- indexed, no FK
    message_id VARCHAR(255) NOT NULL,            -- Anthropic message ID "msg_..."
    model VARCHAR(255) NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    stop_reason VARCHAR(50) NOT NULL,
    has_tool_calls BOOLEAN NOT NULL DEFAULT false,
    request_messages JSONB NOT NULL,             -- full messages array sent to LLM
    response_text TEXT,                          -- generated text only (nullable)
    service_tier VARCHAR(50),
    inference_geo VARCHAR(100)
);
CREATE INDEX ix_llm_calls_session_id ON llm_calls(session_id);
CREATE INDEX ix_llm_calls_started_at ON llm_calls(started_at);
```

### `tool_executions` table

Tool execution log.

```sql
CREATE TABLE tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    tool_name VARCHAR(255) NOT NULL,
    tool_input JSONB NOT NULL,
    tool_output TEXT,
    exit_code INTEGER,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL              -- 'running', 'completed', 'failed', 'timeout'
);
```

### `tasks` table

Persistent, cross-session work items.

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    title VARCHAR(300) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'todo',  -- 'todo', 'in_progress', 'review', 'done'
    source VARCHAR(20) NOT NULL DEFAULT 'user',  -- 'user' or 'ai'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plan_artifact_id UUID REFERENCES artifacts(id) ON DELETE SET NULL  -- linked pre-planning artifact
);
CREATE INDEX ix_tasks_backend_id ON tasks(backend_id);
CREATE INDEX ix_tasks_status ON tasks(status);
```

### `task_sessions` table

Many-to-many: tasks ↔ sessions.

```sql
CREATE TABLE task_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    attached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, session_id)
);
CREATE INDEX ix_task_sessions_task_id ON task_sessions(task_id);
CREATE INDEX ix_task_sessions_session_id ON task_sessions(session_id);
```

### `artifacts` table

Discovered file artifacts (markdown, text files, etc.).

```sql
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_extension VARCHAR(20),
    file_size BIGINT NOT NULL DEFAULT 0,
    mime_type VARCHAR(100),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_at TIMESTAMPTZ NOT NULL,
    session_id UUID REFERENCES sessions(id),
    file_exists BOOLEAN NOT NULL DEFAULT true,  -- false when file deleted but record retained
    UNIQUE(backend_id, file_path)
);
CREATE INDEX ix_artifacts_backend_id ON artifacts(backend_id);
CREATE INDEX ix_artifacts_session_id ON artifacts(session_id);
```

### `linear_issues` table

Cached Linear issues (synced from Linear GraphQL API). See [Linear Integration](linear.md).

```sql
CREATE TABLE linear_issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    linear_id VARCHAR(255) NOT NULL,        -- Linear's internal issue ID
    identifier VARCHAR(50) NOT NULL,         -- Human-readable ID, e.g. "ENG-123"
    title VARCHAR(500) NOT NULL,
    description TEXT,
    priority INTEGER NOT NULL DEFAULT 0,     -- 0=none 1=urgent 2=high 3=medium 4=low
    state_name VARCHAR(100) NOT NULL,        -- Display name, e.g. "In Progress"
    state_type VARCHAR(50) NOT NULL,         -- triage|backlog|unstarted|started|completed|cancelled
    assignee_id VARCHAR(255),
    assignee_name VARCHAR(255),
    team_id VARCHAR(255) NOT NULL,
    team_name VARCHAR(255),
    url TEXT NOT NULL,                       -- Linear issue URL
    labels TEXT NOT NULL DEFAULT '[]',       -- JSON array of label names
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    UNIQUE(backend_id, linear_id)
);
CREATE INDEX ix_linear_issues_backend_id ON linear_issues(backend_id);
CREATE INDEX ix_linear_issues_state_type ON linear_issues(state_type);
```

### `session_turns` table

Telemetry: one row per LLM API turn (prompt → streaming response). See [Telemetry](telemetry.md).

```sql
CREATE TABLE session_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    turn_index INTEGER NOT NULL,            -- 0-based, monotone per session
    ts_start TIMESTAMPTZ NOT NULL,          -- user prompt received / LLM call initiated
    ts_first_token TIMESTAMPTZ,            -- first TEXT_CHUNK or TOOL_START emitted
    ts_end TIMESTAMPTZ,                    -- LLM streaming complete (NULL if interrupted)
    llm_duration_ms INTEGER,               -- ts_end - ts_start in ms (NULL if interrupted)
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    model VARCHAR(255),                     -- e.g. "claude-opus-4-6"
    provider VARCHAR(50),                   -- "anthropic", "bedrock", "openai"
    interrupted BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_session_turns_session_id ON session_turns(session_id);
CREATE INDEX idx_session_turns_backend_id_ts ON session_turns(backend_id, ts_start);
```

### `tool_calls` table

Telemetry: one row per tool invocation (shell, http, worktree).

```sql
CREATE TABLE tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id UUID REFERENCES session_turns(id) ON DELETE SET NULL,
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    turn_index INTEGER,
    tool_call_index INTEGER NOT NULL DEFAULT 0,  -- 0-based within turn
    tool_name VARCHAR(255) NOT NULL,
    ts_start TIMESTAMPTZ NOT NULL,
    ts_end TIMESTAMPTZ,
    duration_ms INTEGER,                         -- NULL if session interrupted mid-call
    status VARCHAR(20) NOT NULL DEFAULT 'ok',    -- 'ok', 'error', 'cancelled'
    executor_type VARCHAR(50),                   -- 'shell', 'http', 'worktree', etc.
    error_message TEXT
);
CREATE INDEX idx_tool_calls_session_id ON tool_calls(session_id);
CREATE INDEX idx_tool_calls_backend_id_ts ON tool_calls(backend_id, ts_start);
CREATE INDEX idx_tool_calls_tool_name ON tool_calls(backend_id, tool_name);
```

### `telemetry_minutely` table

Pre-aggregated 1-minute buckets for fast time-series queries.

```sql
CREATE TABLE telemetry_minutely (
    id BIGSERIAL PRIMARY KEY,
    backend_id VARCHAR(36) NOT NULL,
    bucket TIMESTAMPTZ NOT NULL,            -- truncated to minute
    session_id UUID,                        -- NULL = global rollup across all sessions
    tokens_sent BIGINT NOT NULL DEFAULT 0,
    tokens_received BIGINT NOT NULL DEFAULT 0,
    cache_creation BIGINT NOT NULL DEFAULT 0,
    cache_read BIGINT NOT NULL DEFAULT 0,
    llm_duration_sum_us BIGINT NOT NULL DEFAULT 0,
    llm_duration_count INTEGER NOT NULL DEFAULT 0,
    tool_duration_sum_us BIGINT NOT NULL DEFAULT 0,
    tool_duration_count INTEGER NOT NULL DEFAULT 0,
    inter_tool_gap_sum_us BIGINT NOT NULL DEFAULT 0,
    inter_tool_gap_count INTEGER NOT NULL DEFAULT 0,
    inter_turn_gap_sum_us BIGINT NOT NULL DEFAULT 0,
    inter_turn_gap_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    parallel_tool_calls INTEGER NOT NULL DEFAULT 0,
    UNIQUE(backend_id, bucket, session_id)
);
CREATE INDEX idx_telemetry_minutely_lookup ON telemetry_minutely(backend_id, bucket, session_id);
```

### `session_pending_messages` table

Queued user messages waiting for the agent to become free. See [Queued User Messages](sessions.md#queued-user-messages) for full lifecycle. Rows survive backend restarts; attachments disk-spilled under `data/pending_attachments/<session_id>/<queued_id>/`.

```sql
CREATE TABLE session_pending_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    queued_id VARCHAR(36) NOT NULL UNIQUE,       -- client-visible UUID
    position INTEGER NOT NULL,                   -- FIFO within session; dense, renumbered on drain/cancel
    content TEXT NOT NULL,                       -- routing text (may contain #mentions)
    display_content TEXT NOT NULL,               -- chat-visible text
    attachments_path TEXT,                       -- absolute path to attachment dir (NULL when no attachments)
    project_name TEXT,
    selected_worktree_path TEXT,
    task_id VARCHAR(36),
    submitted_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_pending_session_position ON session_pending_messages(session_id, position);
```

### `drafts` table

One unsent message draft per session. Auto-deleted on session delete (CASCADE).

```sql
CREATE TABLE drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL    -- set explicitly on every write (never relies on trigger)
);
CREATE INDEX ix_drafts_session_id ON drafts(session_id);
```
