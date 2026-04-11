# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Persistent vector memory MCP server for Claude Code. Stores and semantically searches conversational history, notes, and session data across Claude sessions. Registered in `~/.claude/settings.json` as the `claude-memory` MCP server.

## Architecture

Two services, orchestrated by Docker Compose:

- **PostgreSQL 16 + pgvector** (port 5432): Stores memories with 768-dimensional embeddings. Schema initialized by `init.sql`. Migrations in `migrations/`.
- **FastMCP server** (port 3333): `mcp-server/server.py` exposes 15 MCP tools over SSE. Uses `all-mpnet-base-v2` from sentence-transformers to generate embeddings. A `ThreadedConnectionPool` keeps 1–5 persistent DB connections. Includes an in-process LRU search cache (500 entries, 10 min TTL).
- **Ollama** (port 11434, runs on host): Local LLM used by `distill_sessions.py` for session distillation. No API key required. Recommended model: `qwen2.5:7b`.

The `memories` table has GIN indexes on tags and full-text search, an IVFFlat index for cosine similarity vector search, a `content_hash` generated column (MD5, UNIQUE) for exact-duplicate detection, a `deleted_at` column for soft deletes, and an auto-updating `updated_at` trigger.

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

The script reads `DATABASE_URL` from environment (default: `postgresql://claude:memory_pass@localhost:5432/memory`).

## MCP Tools

| Tool | Key Parameters | Purpose |
|------|---------------|---------|
| `save_memory` | `content`, `tags[]`, `source`, `project` | Store a memory with auto-embedding; dedup at ≥0.92 cosine similarity |
| `check_memory` | `content` | Dry-run write guard — returns ADD/UPDATE/NOOP decision without writing |
| `semantic_search` | `query`, `limit`, `min_similarity`, `project`, `since`, `before` | Vector similarity search; results cached per query |
| `search_memories` | `query`, `limit`, `project`, `since`, `before` | Full-text keyword search; results cached per query |
| `hybrid_search` | `query`, `limit`, `keyword_weight`, `semantic_weight`, `project`, `since`, `before` | Combined ts_rank + cosine similarity; weights must sum to 1.0 (default 0.7/0.3) |
| `list_memories` | `limit`, `offset`, `tag`, `project`, `since`, `before` | Paginated list; returns `{total, limit, offset, memories[]}` |
| `get_memory` | `memory_id` | Fetch a single memory by ID (includes soft-deleted rows — check `deleted_at`) |
| `recent_context` | `project`, `limit` | Recent distilled memories for session-start recall; falls back to recent active memories if nothing distilled yet |
| `update_memory` | `memory_id`, `content`, `tags[]`, `force` | Update and re-embed; warns on near-duplicate unless `force=True` |
| `delete_memory` | `memory_id` | Soft-delete (hidden, recoverable via `restore_memory`) |
| `restore_memory` | `memory_id` | Restore a soft-deleted memory |
| `purge_memory` | `memory_id` | Permanently delete — must soft-delete first (two-step safety gate) |
| `list_tags` | — | All unique tags with counts (active memories only) |
| `get_stats` | — | Memory/session counts, soft-deleted count, distill failure cap stats, search cache stats — single DB round-trip |
| `export_memories` | `project`, `tag`, `since`, `before`, `output_format` | Export as JSON or markdown |

### HTTP Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness/readiness probe — returns `{"status":"ok"}` (200) or `{"status":"degraded"}` (503) |

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
| `CACHE_MAX_SIZE` | `500` | Max search cache entries (LRU eviction) |
| `CACHE_TTL_SECONDS` | `600` | Search cache TTL in seconds |

Data is persisted to `./data/postgres/` on the host. The HuggingFace model cache is volume-mounted to survive container restarts.

## Test Suite

```bash
brew install pytest   # one-time
pytest tests/ -v      # 63 tests, no Docker or GPU required
```

Tests cover: `_parse_dt`, `save_memory`, `semantic_search`, `search_memories`, `list_memories` (including pagination), `delete_memory`, `export_memories`, `hybrid_search`, cache helpers, cache invalidation on writes, distillation failure cap, and `get_pending_sessions` filtering.

All heavy dependencies (sentence-transformers, psycopg2, pgvector, openai, starlette, mcp) are mocked in `tests/conftest.py` — no Docker or GPU required to run the suite.
