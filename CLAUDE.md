# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Persistent vector memory MCP server for Claude Code. Stores and semantically searches conversational history, notes, and session data across Claude sessions. Registered in `~/.claude/settings.json` as the `claude-memory` MCP server.

## Architecture

Two services, orchestrated by Docker Compose:

- **PostgreSQL 16 + pgvector** (port 5432): Stores memories with 384-dimensional embeddings. Schema initialized by `init.sql`.
- **FastMCP server** (port 3333): `mcp-server/server.py` exposes 7 MCP tools over SSE. Uses `all-MiniLM-L6-v2` from sentence-transformers to generate embeddings.

The `memories` table has GIN indexes on tags and full-text search, an IVFFlat index for cosine similarity vector search, and an auto-updating `updated_at` trigger.

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
| `save_memory` | `content`, `tags[]`, `source` | Store a memory with auto-embedding |
| `semantic_search` | `query`, `limit`, `min_similarity` | Vector similarity search |
| `search_memories` | `query`, `limit` | Full-text keyword search |
| `list_memories` | `limit`, `tag` | List recent memories, optionally by tag |
| `update_memory` | `memory_id`, `content`, `tags[]` | Update and re-embed a memory |
| `delete_memory` | `memory_id` | Delete by ID |
| `list_tags` | — | All unique tags with counts |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://claude:memory_pass@localhost:5432/memory` | PostgreSQL connection |
| `POSTGRES_DB` | `memory` | Database name |
| `POSTGRES_USER` | `claude` | DB user |
| `POSTGRES_PASSWORD` | `memory_pass` | DB password |

Data is persisted to `./data/postgres/` on the host. The HuggingFace model cache is volume-mounted to survive container restarts.

## No Test Suite

There is no automated test suite. Verify functionality by checking logs (`docker compose logs -f mcp-server`) or querying the database directly:

```bash
docker exec -it claude-memory-db-1 psql -U claude -d memory -c "SELECT id, source, tags, created_at FROM memories ORDER BY created_at DESC LIMIT 10;"
```
