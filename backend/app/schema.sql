-- ==============================================================================
-- Catchup Database Schema (PostgreSQL + pgvector)
-- Defined in Step 1 per PROJECT2.md specification.
-- Idempotent: safe to run multiple times.
-- ==============================================================================

-- 1. Enable the pgvector extension for code chunk embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Repositories table
-- Tracks added GitHub repositories, processing status, and the latest explained commit.
CREATE TABLE IF NOT EXISTS repos (
    id SERIAL PRIMARY KEY,
    github_repo_id BIGINT UNIQUE,
    name VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'mapping' CHECK (status IN ('mapping', 'ready', 'failed')),
    last_explained_commit VARCHAR(64),
    mapped_commit_sha VARCHAR(64),
    mapping_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE repos ADD COLUMN IF NOT EXISTS mapped_commit_sha VARCHAR(64);
ALTER TABLE repos ADD COLUMN IF NOT EXISTS mapping_error TEXT;

CREATE INDEX IF NOT EXISTS idx_repos_status ON repos(status);
CREATE INDEX IF NOT EXISTS idx_repos_name ON repos(name);

-- 3. Code Chunks table
-- Stores functions, classes, and file-level chunks with their 384-dimensional pgvector embeddings.
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file VARCHAR(500) NOT NULL,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(50) NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding vector(384),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_repo_id ON chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_chunks_repo_file ON chunks(repo_id, file);
CREATE INDEX IF NOT EXISTS idx_chunks_name ON chunks(repo_id, name);

-- 4. References table
-- Records "who calls whom" connections between code chunks.
-- ("references" is a SQL reserved word, enclosed in double quotes)
CREATE TABLE IF NOT EXISTS "references" (
    id SERIAL PRIMARY KEY,
    caller_chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    callee_chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_caller_callee UNIQUE (caller_chunk_id, callee_chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_references_caller_chunk ON "references"(caller_chunk_id);
CREATE INDEX IF NOT EXISTS idx_references_callee_chunk ON "references"(callee_chunk_id);

-- 5. Test Links table
-- Records which test files cover which code chunk.
CREATE TABLE IF NOT EXISTS test_links (
    id SERIAL PRIMARY KEY,
    test_file VARCHAR(500) NOT NULL,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_test_file_chunk UNIQUE (test_file, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_test_links_chunk_id ON test_links(chunk_id);
CREATE INDEX IF NOT EXISTS idx_test_links_test_file ON test_links(test_file);

-- 6. Pushes table
-- Records pushes to the repository's default branch.
CREATE TABLE IF NOT EXISTS pushes (
    id SERIAL PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    before_commit VARCHAR(64) NOT NULL,
    after_commit VARCHAR(64) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pushes_repo_id ON pushes(repo_id);
CREATE INDEX IF NOT EXISTS idx_pushes_status ON pushes(status);

-- 7. Jobs table
-- Job queue for background worker processing.
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    payload JSONB NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_push_commit
    ON jobs ((payload->>'repo_id'), (payload->>'after_commit'))
    WHERE payload->>'type' = 'explain_push';

-- 8. Explanations table
-- Stores generated push explanations, evidence used, and execution metrics.
CREATE TABLE IF NOT EXISTS explanations (
    id SERIAL PRIMARY KEY,
    push_id INTEGER NOT NULL UNIQUE REFERENCES pushes(id) ON DELETE CASCADE,
    content JSONB NOT NULL,
    evidence_used JSONB NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    model VARCHAR(100) NOT NULL,
    extra_evidence BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_explanations_push_id ON explanations(push_id);

-- 9. Durable optional email outbox; one notification at most per completed job.
CREATE TABLE IF NOT EXISTS push_email_notifications (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    repository VARCHAR(255) NOT NULL,
    branch VARCHAR(255),
    before_commit VARCHAR(64) NOT NULL,
    after_commit VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    provider_message_id VARCHAR(255),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_push_email_notifications_pending
    ON push_email_notifications(status, next_attempt_at, created_at);
