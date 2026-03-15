# claude-memory

Persistent vector memory for Claude Code. Stores your Claude sessions, notes, and conversations in a local PostgreSQL database with semantic search — so every new session can recall what you've worked on before.

## How it works

Two Docker containers:

- **PostgreSQL 16 + pgvector** — stores memories as text + 768-dimensional embeddings
- **FastMCP server (port 3333)** — exposes 11 tools to Claude via the MCP protocol over SSE

When registered as an MCP server, Claude can search your memory by meaning (`semantic_search`), keyword (`search_memories`), or tag (`list_memories`) — and save new memories automatically during a session. A connection pool keeps 1–5 persistent DB connections so tool calls are fast.

## Quick start

**Prerequisites:** Docker, [Claude Code](https://claude.ai/code), [Ollama](https://ollama.com)

```bash
# 1. Install Ollama and pull a model
brew install ollama
ollama serve &
ollama pull qwen2.5:7b   # recommended — or llama3.2:3b for lower RAM

# 2. Clone and start
git clone https://github.com/daringanitch/claude-memory
cd claude-memory
bash quickstart.sh
```

`quickstart.sh` handles everything in order:
1. Starts Docker services and waits for the DB to be healthy
2. Imports your existing `~/.claude/projects` session history
3. Distills sessions into durable memories using a local Ollama LLM (no API key required)
4. Registers the MCP server with Claude Code at user scope (available in every project)
5. Optionally installs the hourly auto-import LaunchAgent (macOS)

Then open a new `claude` session and try `list_memories`.

## Manual setup

```bash
# Start services
docker compose up -d

# Register with Claude Code (user scope — works in any directory)
claude mcp add --scope user --transport sse claude-memory http://localhost:3333/sse

# Verify
claude mcp get claude-memory
```

## Importing past sessions

Run import scripts inside the container (sentence-transformers isn't available via brew):

```bash
# Claude Code session history (~/.claude/projects/)
docker compose run --rm -T \
  -v ~/.claude/projects:/root/.claude/projects:ro \
  -v $(pwd)/import_memories.py:/app/import_memories.py:ro \
  mcp-server python /app/import_memories.py --claude-code

# Filter to one project
  mcp-server python /app/import_memories.py --claude-code --project my-project

# Claude.ai export (Settings → Privacy → Export data → conversations.json)
  mcp-server python /app/import_memories.py --claude-ai /path/to/conversations.json

# Plain text or markdown files (chunked at 1500 chars, 200-char overlap)
  mcp-server python /app/import_memories.py --text notes.md

# Raise minimum message length (default: 50 chars)
  mcp-server python /app/import_memories.py --claude-code --min-length 100
```

## Distilling sessions

Raw imported messages are verbose. `distill_sessions.py` uses a local Ollama LLM to extract durable knowledge — decisions, patterns, bug root causes — and replaces raw messages with concise, searchable memories. No API key required.

**Ollama setup (one-time):**
```bash
brew install ollama
ollama serve &
ollama pull qwen2.5:7b   # ~4.7GB, best quality
# or: ollama pull llama3.2:3b  (~2GB, faster)
```

```bash
# Distill all pending sessions (4 parallel workers by default)
docker compose run --rm -T \
  -e OLLAMA_URL="http://host.docker.internal:11434/v1" \
  -v $(pwd)/distill_sessions.py:/app/distill_sessions.py:ro \
  mcp-server python /app/distill_sessions.py

# Preview without writing to DB
  mcp-server python /app/distill_sessions.py --dry-run

# Filter to one project
  mcp-server python /app/distill_sessions.py --project my-project

# Tune parallelism or swap models
  mcp-server python /app/distill_sessions.py --workers 8 --model llama3.2:3b
```

Speed notes: sessions are processed in parallel (`--workers`, default 4), embeddings are batched per session, and DB inserts are bulk operations — roughly 4x faster than sequential processing.

## Auto-import (macOS)

Install a LaunchAgent that runs `import-cron.sh` every hour — importing new Claude Code sessions and distilling them automatically:

```bash
bash setup-launchagent.sh

# Check logs
tail -f /tmp/claude-memory-import.log

# Trigger manually
launchctl start com.claude-memory.import
```

## Backup and restore

```bash
# Snapshot the database (saved to ./backups/)
bash backup.sh

# Restore from a snapshot (destructive — prompts for confirmation)
bash restore.sh backups/claude-memory-2026-03-08T12-00-00.pgdump
```

## MCP tools

| Tool | Key Parameters | Description |
|------|---------------|-------------|
| `save_memory` | `content`, `tags[]`, `source`, `project` | Save a note; auto-deduplicates at ≥0.92 cosine similarity |
| `semantic_search` | `query`, `limit`, `min_similarity`, `project`, `since`, `before` | Search by **meaning** using vector cosine similarity |
| `search_memories` | `query`, `limit`, `project`, `since`, `before` | Search by **keyword** using PostgreSQL full-text search |
| `list_memories` | `limit`, `tag`, `project`, `since`, `before` | List recent memories, optionally filtered |
| `get_memory` | `memory_id` | Fetch a single memory by ID with full content |
| `recent_context` | `project`, `limit` | Recent distilled memories — use at session start for context recall |
| `update_memory` | `memory_id`, `content`, `tags[]` | Update content or tags (re-embeds automatically if content changes) |
| `delete_memory` | `memory_id` | Delete a memory by ID |
| `list_tags` | — | List all tags with occurrence counts |
| `get_stats` | — | Memory counts by project/source, session import and distill status |
| `export_memories` | `project`, `tag`, `since`, `before`, `output_format` | Export memories as JSON or markdown |

`since` and `before` accept ISO date strings: `"2026-01-01"` or `"2026-01-01T12:00:00"`.

## Tests

```bash
brew install pytest   # one-time
pytest tests/ -v      # 37 tests, no Docker or GPU required
```

All heavy dependencies (sentence-transformers, psycopg2, openai) are mocked by `tests/conftest.py`.

## Global Claude Code integration

Add to `~/.claude/CLAUDE.md` to instruct Claude to recall context automatically at the start of every session:

```markdown
## Session Start — Memory Recall

At the start of every new session, use the claude-memory MCP server:
1. Call semantic_search with the current project name to recall prior context
2. Briefly summarize what was found

Save key decisions, bug root causes, and user preferences using save_memory
with descriptive tags like ["project:name", "type:decision|bug|preference|pattern"].
```

## Database schema

```sql
CREATE TABLE memories (
  id         SERIAL PRIMARY KEY,
  content    TEXT         NOT NULL,
  tags       TEXT[]       DEFAULT '{}',
  source     VARCHAR(100) DEFAULT 'claude-code',
  project    VARCHAR(100) DEFAULT '',
  embedding  vector(768),
  created_at TIMESTAMP    DEFAULT NOW(),
  updated_at TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE imported_sessions (
  session_id    VARCHAR(100) PRIMARY KEY,
  project       VARCHAR(100) DEFAULT '',
  imported_at   TIMESTAMP    DEFAULT NOW(),
  message_count INT          DEFAULT 0,
  distilled     BOOLEAN      DEFAULT FALSE
);
```

Indexes: IVFFlat for vector cosine search, GIN for tag arrays and full-text search, BTREE on `created_at` and `project`.

## Configuration

| Variable | Default |
|----------|---------|
| `POSTGRES_DB` | `memory` |
| `POSTGRES_USER` | `claude` |
| `POSTGRES_PASSWORD` | `memory_pass` |
| `DATABASE_URL` | `postgresql://claude:memory_pass@db:5432/memory` |
| `OLLAMA_URL` | `http://localhost:11434/v1` (use `http://host.docker.internal:11434/v1` inside Docker) |
| `DISTILL_MODEL` | `qwen2.5:7b` |
| `DISTILL_WORKERS` | `4` |

Data is persisted to `./data/postgres/`. The HuggingFace model cache is stored in a named Docker volume (`model_cache`) so `all-mpnet-base-v2` isn't re-downloaded on restart.

## Stack

- [pgvector](https://github.com/pgvector/pgvector) — vector similarity search for PostgreSQL
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [sentence-transformers](https://www.sbert.net/) — `all-mpnet-base-v2` for 768-dim embeddings
- [Model Context Protocol](https://modelcontextprotocol.io/) — tool interface for Claude
- [Ollama](https://ollama.com) — local LLM inference for session distillation (Qwen2.5, Llama3.2, etc.)
