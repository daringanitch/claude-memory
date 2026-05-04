# claude-memory

Persistent vector memory for Claude Code. Stores your Claude sessions, notes, and conversations in a local PostgreSQL database with semantic search — so every new session can recall what you've worked on before. Automatically learns your preferences and working patterns by analysing session behaviour, building a richer picture of how you work over time.

**[Watch the explainer →](https://youtu.be/RudczUqssIo)**

## What's new

| Date | Feature |
|------|---------|
| 2026-05-04 | **Web UI** — single-page React app served at `GET http://localhost:3333/ui`; Timeline River SVG visualization, semantic search with similarity bars, memory detail pane with related memories, full-content reader overlay, preferences dashboard, settings/danger zone |
| 2026-05-04 | **REST API** — 10 HTTP endpoints (`/api/memories`, `/api/recall`, `/api/stats`, `/api/projects`, `/api/tags`, `/api/preferences`, `/api/memories/:id/related`, etc.) served alongside the MCP server; no separate service needed |
| 2026-05-04 | **Behavioral preference extraction** — `behavioral_pass.py` runs a targeted LLM pass over already-distilled sessions to extract HOW the user works (`type:behavior` memories); surfaces in a three-tier preference model: explicit → signals → inferred |
| 2026-05-04 | **Richer distillation prompt** — `distill_sessions.py` Part B now explicitly instructs the model to extract behavioral observations (workflow habits, communication style, decision patterns) tagged `type:behavior` |
| 2026-04-12 | **`startup_context` tool** — single-call session-start snapshot combining behavioral signals and recent work; no search query needed (inspired by MemPalace layered loading) |
| 2026-04-12 | **Behavioral signal extraction** — `extract_signals.py` parses session JSONL files without an LLM to produce preference memories from correction signals and pattern memories from tool/command/file habits |
| 2026-04-11 | **`find_duplicates` + `bulk_delete` tools** — surface near-duplicate memory pairs and soft-delete memories in bulk by tag, project, or source |
| 2026-04-11 | **`hybrid_search` tool** — combined keyword + semantic search with configurable weights |
| 2026-04-11 | **Search cache** — 10-minute in-process cache for `semantic_search` and `search_memories`; cleared via `POST /cache/invalidate` |
| 2026-04-11 | **Distillation failure cap** — sessions that fail distillation 3 times are skipped automatically; use `--reset-failures` to retry |
| 2026-04-11 | **Soft deletes + health endpoint** — memories can be hidden and restored; `/health` liveness probe added |
| 2026-03-23 | **Deduplication** — `content_hash` unique constraint prevents duplicate memories at insert time |
| 2026-03-14 | **Local Ollama distillation** — sessions distilled via local LLM (no API key); parallel worker support |
| 2026-03-08 | **Test suite, export, time filtering** — 76 tests, `export_memories` tool, `since`/`before` filters on all search tools |

## How it works

Two Docker containers:

- **PostgreSQL 16 + pgvector** — stores memories as text + 768-dimensional embeddings
- **FastMCP server (port 3333)** — exposes 18 MCP tools to Claude over SSE, a REST API with 10 HTTP endpoints, and a browser UI at `GET /ui`

When registered as an MCP server, Claude can search your memory by meaning (`semantic_search`), keyword (`search_memories`), or hybrid scoring (`hybrid_search`) — and save new memories automatically during a session. A connection pool keeps 1–5 persistent DB connections so tool calls are fast.

Open `http://localhost:3333/ui` for a visual browser: Timeline River SVG of your memory history, semantic search with similarity scores, full-content reader overlay, and a Preferences dashboard that surfaces what the system has inferred about how you work.

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

After a bulk import, clear the search cache so results reflect the new data:

```bash
curl -X POST http://localhost:3333/cache/invalidate
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

# Reset distillation failures so capped sessions can be retried
  mcp-server python /app/distill_sessions.py --reset-failures           # reset all capped
  mcp-server python /app/distill_sessions.py --reset-failures abc12345  # reset one session
```

Sessions that fail distillation 3 times are capped and skipped automatically. Use `--reset-failures` to retry them.

Speed notes: sessions are processed in parallel (`--workers`, default 4), embeddings are batched per session, and DB inserts are bulk operations — roughly 4x faster than sequential processing.

## Behavioral signal extraction ✨ New

`extract_signals.py` parses session JSONL files directly — no LLM required — to produce two classes of memory:

**Per-session (saved immediately):**
- **Correction signals** — user messages that negate or correct Claude's previous action (e.g. "don't do that", "stop", "actually no") are saved as `type:preference` memories, building an automatic picture of your implicit preferences over time.

**Per-project (aggregated, refreshed on every run):**
- **Workflow fingerprint** — breakdown of tool categories used (execution, file editing, search, web, etc.)
- **Command habits** — most frequently run shell commands
- **File hotspots** — files accessed 2+ times across sessions

```bash
# Preview what would be extracted (no DB writes)
docker compose run --rm -T \
  -v ~/.claude/projects:/root/.claude/projects:ro \
  -v $(pwd)/extract_signals.py:/app/extract_signals.py:ro \
  mcp-server python /app/extract_signals.py --dry-run

# Run for real
docker compose run --rm -T \
  -v ~/.claude/projects:/root/.claude/projects:ro \
  -v $(pwd)/extract_signals.py:/app/extract_signals.py:ro \
  mcp-server python /app/extract_signals.py

# Filter to one project
  mcp-server python /app/extract_signals.py --project my-project
```

Signal memories are tagged `type:preference`, `type:pattern`, or `type:behavior` with `source:signals` so they're distinguishable from distilled or manually saved memories. Aggregate pattern memories are upserted on each run so they stay current as new sessions accumulate.

## Behavioral pass (LLM extraction)

`behavioral_pass.py` runs a targeted LLM pass over already-distilled sessions to extract implicit behavioral observations — HOW the user works, not just what was built. It reads transcripts directly from the original JSONL files on disk (raw messages are deleted from the DB after distillation).

**What it extracts:** workflow habits, tooling instincts, communication style (terse vs. detailed), decision-making speed, quality habits (tests, docs, diffs), correction patterns.

Results are stored as `type:behavior` memories and surface in the **Inferred** tier of `GET /api/preferences` and the Preferences section of the web UI.

```bash
# Run on all distilled sessions (skips already-processed ones)
python behavioral_pass.py

# Filter to one project
python behavioral_pass.py --project my-project

# Preview without writing to DB
python behavioral_pass.py --dry-run

# Re-run even if behavioral memories already exist
python behavioral_pass.py --force
```

Requires Ollama running on the host. Uses `DISTILL_MODEL` env var (default: `qwen2.5:7b`).

## Auto-import (macOS)

Install a LaunchAgent that runs `import-cron.sh` every hour — importing new Claude Code sessions, distilling them, and extracting behavioral signals automatically:

The hourly pipeline runs four steps in sequence:
1. `import_memories.py` — imports new sessions from `~/.claude/projects`
2. `distill_sessions.py` — summarises sessions into durable memories via Ollama
3. `extract_signals.py` — extracts behavioral signals without an LLM
4. `behavioral_pass.py` — LLM pass over distilled sessions to extract `type:behavior` memories

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
| `startup_context` | `project` | **Session-start snapshot** — behavioral signals + recent distilled memories in one compact call; no search query needed |
| `save_memory` | `content`, `tags[]`, `source`, `project` | Save a note; auto-deduplicates at ≥0.92 cosine similarity |
| `check_memory` | `content` | Dry-run write guard — returns ADD/UPDATE/NOOP with nearest match preview |
| `semantic_search` | `query`, `limit`, `min_similarity`, `project`, `since`, `before` | Search by **meaning** using vector cosine similarity (cached 10 min) |
| `search_memories` | `query`, `limit`, `project`, `since`, `before` | Search by **keyword** using PostgreSQL full-text search (cached 10 min) |
| `hybrid_search` | `query`, `limit`, `keyword_weight`, `semantic_weight`, `project`, `since`, `before` | Combined keyword + semantic search with configurable weights |
| `list_memories` | `limit`, `offset`, `tag`, `project`, `since`, `before` | Paginated list; returns `{total, limit, offset, memories[]}` |
| `get_memory` | `memory_id` | Fetch a single memory by ID with full content |
| `recent_context` | `project`, `limit` | Recent distilled memories — falls back to active memories if none distilled |
| `update_memory` | `memory_id`, `content`, `tags[]`, `force` | Update content or tags (re-embeds automatically if content changes) |
| `delete_memory` | `memory_id` | Soft-delete (hidden from search, recoverable) |
| `restore_memory` | `memory_id` | Restore a soft-deleted memory |
| `purge_memory` | `memory_id` | Permanently delete (must soft-delete first — two-step safety gate) |
| `find_duplicates` | `threshold`, `limit`, `project`, `scan_limit` | Find near-duplicate memory pairs; `scan_limit` bounds the scan (default 500) |
| `bulk_delete` | `tag`, `project`, `source`, `dry_run` | Soft-delete all matching memories (`dry_run=True` by default — preview first) |
| `list_tags` | — | List all tags with occurrence counts |
| `get_stats` | — | Memory counts by project/source, deleted count, session import and distill status |
| `export_memories` | `project`, `tag`, `since`, `before`, `output_format` | Export memories as JSON or markdown |

`since` and `before` accept ISO date strings: `"2026-01-01"` or `"2026-01-01T12:00:00"`.

### HTTP endpoints

#### Web UI
| Endpoint | Purpose |
|----------|---------|
| `GET /ui` | Single-page React app — browse memories, search, read full content, manage preferences |

#### REST API
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness probe — `{"status":"ok"}` (200) or `{"status":"degraded"}` (503) |
| `/cache/invalidate` | POST | Clear in-process search cache — call after bulk imports to avoid stale results |
| `/api/stats` | GET | Memory counts, storage estimate, project count |
| `/api/projects` | GET | Distinct projects with memory counts |
| `/api/tags` | GET | All tags with occurrence counts (active memories only) |
| `/api/memories` | GET | Paginated list. Params: `project`, `tag`, `since`, `before`, `limit`, `offset` |
| `/api/memories/:id` | GET | Single memory by ID |
| `/api/memories/:id/related` | GET | Nearest-neighbour memories. Param: `limit` (default 3) |
| `/api/recall` | POST | Semantic search. Body: `{"query": "...", "threshold": 0.78}` |
| `/api/preferences` | GET | Behavioral preferences grouped by tier: explicit → signals → inferred |
| `/api/memories` | DELETE | Bulk soft-delete. Params: `project`, `tag` |

## Migrations

Schema changes that need to be applied to existing databases are in `migrations/`:

```bash
# Apply migration (idempotent — safe to re-run)
docker exec -i claude-memory-db psql -U claude -d memory \
  < migrations/003_distill_failure_cap.sql
```

| File | What it adds |
|------|-------------|
| `001_soft_deletes.sql` | `deleted_at` column on `memories` |
| `002_content_hash_dedup.sql` | `content_hash` unique index for insert dedup |
| `003_distill_failure_cap.sql` | `distill_failures` column on `imported_sessions` |
| `004_signals_extracted.sql` | `signals_extracted` column on `imported_sessions` |

## Tests

```bash
brew install pytest   # one-time
pytest tests/ -v      # 76 tests, no Docker or GPU required
```

All heavy dependencies (sentence-transformers, psycopg2, openai) are mocked by `tests/conftest.py`.

## Global Claude Code integration

Add to `~/.claude/CLAUDE.md` to instruct Claude to recall context automatically at the start of every session:

```markdown
## Session Start — Memory Recall

At the start of every new session, use the claude-memory MCP server:
1. Call startup_context with the last segment of the current working directory as the project name
   (e.g. cwd /home/user/projects/my-app → startup_context("my-app"))
2. Call semantic_search for deeper recall on specific topics if needed
3. Briefly summarize what was found

Save key decisions, bug root causes, and user preferences using save_memory
with descriptive tags like ["project:name", "type:decision|bug|preference|pattern"].
```

## Database schema

```sql
CREATE TABLE memories (
  id           SERIAL PRIMARY KEY,
  content      TEXT         NOT NULL,
  content_hash VARCHAR(64)  GENERATED ALWAYS AS (md5(content)) STORED UNIQUE,
  tags         TEXT[]       DEFAULT '{}',
  source       VARCHAR(100) DEFAULT 'claude-code',
  project      VARCHAR(100) DEFAULT '',
  embedding    vector(768),
  created_at   TIMESTAMP    DEFAULT NOW(),
  updated_at   TIMESTAMP    DEFAULT NOW(),
  deleted_at   TIMESTAMP    DEFAULT NULL
);

CREATE TABLE imported_sessions (
  session_id        VARCHAR(100) PRIMARY KEY,
  project           VARCHAR(100) DEFAULT '',
  imported_at       TIMESTAMP    DEFAULT NOW(),
  message_count     INT          DEFAULT 0,
  distilled           BOOLEAN      DEFAULT FALSE,
  distill_failures    INT          DEFAULT 0,
  signals_extracted   BOOLEAN      DEFAULT FALSE
);
```

Indexes: IVFFlat for vector cosine search, GIN for tag arrays and full-text search, BTREE on `created_at`, `project`, and `deleted_at`.

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
| `TRANSFORMERS_OFFLINE` | `1` (set in Docker) |
| `HF_DATASETS_OFFLINE` | `1` (set in Docker) |
| `GUARD_NOOP_THRESHOLD` | `0.92` — cosine similarity above which `save_memory` is skipped as a duplicate |
| `GUARD_UPDATE_THRESHOLD` | `0.75` — cosine similarity above which `save_memory` suggests updating instead |
| `CACHE_MAX_SIZE` | `500` — max entries in the in-process search cache |
| `CACHE_TTL_SECONDS` | `600` — search cache TTL (10 minutes) |

Data is persisted to `./data/postgres/`. The HuggingFace model cache is stored in a named Docker volume (`model_cache`) so `all-mpnet-base-v2` isn't re-downloaded on restart.

## Stack

- [pgvector](https://github.com/pgvector/pgvector) — vector similarity search for PostgreSQL
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [sentence-transformers](https://www.sbert.net/) — `all-mpnet-base-v2` for 768-dim embeddings
- [Model Context Protocol](https://modelcontextprotocol.io/) — tool interface for Claude
- [Ollama](https://ollama.com) — local LLM inference for session distillation (Qwen2.5, Llama3.2, etc.)
