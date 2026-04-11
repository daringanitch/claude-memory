# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Persistent vector memory MCP server for Claude Code. Stores and semantically searches conversational history, notes, and session data across Claude sessions. Registered in `~/.claude/settings.json` as the `claude-memory` MCP server.

## Architecture

Two services, orchestrated by Docker Compose:

- **PostgreSQL 16 + pgvector** (port 5432): Stores memories with 768-dimensional embeddings. Schema initialized by `init.sql`.
- **FastMCP server** (port 3333): `mcp-server/server.py` exposes 17 MCP tools over SSE. Uses `all-mpnet-base-v2` from sentence-transformers to generate embeddings. A `ThreadedConnectionPool` keeps 1–5 persistent DB connections.
- **Ollama** (port 11434, runs on host): Local LLM used by `distill_sessions.py` for session distillation. No API key required. Recommended model: `qwen2.5:7b`.

The `memories` table has GIN indexes on tags and full-text search, an IVFFlat index for cosine similarity vector search, and an auto-updating `updated_at` trigger. Soft-deletes are tracked via a `deleted_at` column; use `purge_memory` to permanently remove.

## Commands

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f mcp-server
docker compose logs -f db

# Stop services
docker compose down

# Stop and delete volumes (destroys all memories)
docker compose down -v

# Rebuild after code changes
docker compose build mcp-server && docker compose up -d mcp-server
```

## Import Script

`import_memories.py` is a standalone CLI tool to bulk-import from multiple sources:

```bash
# From Claude Code session history (~/.claude/projects/)
python import_memories.py --claude-code

# Filter to a specific project name
python import_memories.py --claude-code --project workspace

# From Claude.ai export JSON (Settings → Privacy → Export)
python import_memories.py --claude-ai conversations.json

# From text/markdown files (chunked at 1500 chars with 200-char overlap)
python import_memories.py --text notes.md

# Combined sources
python import_memories.py --claude-code --claude-ai conversations.json --text notes.md

# Custom minimum message length (default: 50 chars)
python import_memories.py --claude-code --min-length 100
```

After a bulk import, clear the search cache so results reflect the new data:

```bash
curl -X POST http://localhost:3333/cache/invalidate
```

The script reads `DATABASE_URL` from environment (default: `postgresql://claude:memory_pass@localhost:5432/memory`).

## MCP Tools

| Tool | Key Parameters | Purpose |
|------|---------------|---------|
| `save_memory` | `content`, `tags[]`, `source`, `project` | Store a memory with auto-embedding; dedup at ≥0.92 cosine similarity |
| `check_memory` | `content` | Dry-run write guard — returns ADD/UPDATE/NOOP with nearest match preview |
| `semantic_search` | `query`, `limit`, `min_similarity`, `project`, `since`, `before` | Vector similarity search (cached 10 min) |
| `search_memories` | `query`, `limit`, `project`, `since`, `before` | Full-text keyword search (cached 10 min) |
| `hybrid_search` | `query`, `limit`, `keyword_weight`, `semantic_weight`, `project`, `since`, `before` | Combined keyword + semantic search |
| `list_memories` | `limit`, `offset`, `tag`, `project`, `since`, `before` | Paginated list of recent memories; returns `{total, limit, offset, memories[]}` |
| `get_memory` | `memory_id` | Fetch a single memory by ID (includes deleted_at so you can see soft-deleted rows) |
| `recent_context` | `project`, `limit` | Recent distilled memories — falls back to active memories if no distilled exist |
| `update_memory` | `memory_id`, `content`, `tags[]`, `force` | Update and re-embed; warns on near-duplicate unless force=True |
| `delete_memory` | `memory_id` | Soft-delete (hidden, recoverable via restore_memory) |
| `restore_memory` | `memory_id` | Restore a soft-deleted memory |
| `purge_memory` | `memory_id` | Permanently delete (must soft-delete first — two-step safety gate) |
| `find_duplicates` | `threshold`, `limit`, `project`, `scan_limit` | Find near-duplicate memory pairs above similarity threshold (scan_limit bounds to most recent N) |
| `bulk_delete` | `tag`, `project`, `source`, `dry_run` | Soft-delete all memories matching filters (dry_run=True by default) |
| `list_tags` | — | All unique tags with counts (active memories only) |
| `get_stats` | — | Memory counts by project/source, deleted count, session import/distill status, cache size |
| `export_memories` | `project`, `tag`, `since`, `before`, `output_format` | Export memories as JSON or markdown |

### HTTP Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness probe — returns `{"status":"ok"}` (200) or `{"status":"degraded"}` (503) |
| `POST /cache/invalidate` | Clear the in-process search cache — call after bulk imports |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://claude:memory_pass@localhost:5432/memory` | PostgreSQL connection |
| `POSTGRES_DB` | `memory` | Database name |
| `POSTGRES_USER` | `claude` | DB user |
| `POSTGRES_PASSWORD` | `memory_pass` | DB password |
| `OLLAMA_URL` | `http://localhost:11434/v1` | Ollama endpoint for distillation (use `host.docker.internal` inside Docker) |
| `DISTILL_MODEL` | `qwen2.5:7b` | Ollama model used by `distill_sessions.py` |
| `DISTILL_WORKERS` | `4` | Parallel sessions during distillation |
| `TRANSFORMERS_OFFLINE` | `1` (in Docker) | Prevents HuggingFace network calls on restart |
| `HF_DATASETS_OFFLINE` | `1` (in Docker) | Prevents HuggingFace datasets network calls on restart |
| `GUARD_NOOP_THRESHOLD` | `0.92` | Cosine similarity above which save/update is skipped as duplicate |
| `GUARD_UPDATE_THRESHOLD` | `0.75` | Cosine similarity above which save suggests update instead |
| `CACHE_MAX_SIZE` | `500` | Max entries in the in-process search cache |
| `CACHE_TTL_SECONDS` | `600` | Search cache TTL (10 minutes) |

Data is persisted to `./data/postgres/` on the host. The HuggingFace model cache is volume-mounted to survive container restarts.

## Tests

```bash
brew install pytest   # one-time
pytest tests/ -v      # 76 tests, no Docker or GPU required
```

All heavy dependencies (sentence-transformers, psycopg2, openai) are mocked by `tests/conftest.py`.
