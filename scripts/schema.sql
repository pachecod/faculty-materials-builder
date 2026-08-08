-- Postgres catalog for read-only Render viewer (optional; local viewer uses publish/*.json)

CREATE TABLE IF NOT EXISTS publish_runs (
  id            BIGSERIAL PRIMARY KEY,
  built_at      TIMESTAMPTZ,
  packet_count  INT,
  content_hash  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS packets (
  id              BIGSERIAL PRIMARY KEY,
  path            TEXT NOT NULL UNIQUE,
  name            TEXT NOT NULL,
  section         TEXT,
  pages           INT DEFAULT 0,
  need_content    TEXT,
  level           TEXT,
  official_at     TIMESTAMPTZ,
  content_hash    TEXT,
  publish_run_id  BIGINT REFERENCES publish_runs(id) ON DELETE SET NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS packets_section_idx ON packets (section);
