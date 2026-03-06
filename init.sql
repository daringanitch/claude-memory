CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memories (
  id          SERIAL PRIMARY KEY,
  content     TEXT         NOT NULL,
  tags        TEXT[]       DEFAULT '{}',
  source      VARCHAR(100) DEFAULT 'claude-code',
  embedding   vector(384),
  created_at  TIMESTAMP    DEFAULT NOW(),
  updated_at  TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX idx_memories_tags      ON memories USING GIN(tags);
CREATE INDEX idx_memories_created   ON memories(created_at DESC);
CREATE INDEX idx_memories_fts       ON memories USING GIN(to_tsvector('english', content));
CREATE INDEX idx_memories_embedding ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
BEFORE UPDATE ON memories
FOR EACH ROW EXECUTE FUNCTION update_updated_at();
