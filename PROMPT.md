# Claude Memory — Genesis Prompt

> Paste this into Claude Code to build the entire claude-memory system from scratch.

---

Build me a persistent vector memory system for Claude Code. It should store my past Claude sessions, notes, and conversations in a local database so that every new Claude Code session can recall what I've worked on before.

## Architecture

Two Docker containers managed by Docker Compose:

1. **PostgreSQL 16 with pgvector** — stores memories as text + 768-dimensional vector embeddings
2. **FastMCP server on port 3333** — exposes memory tools to Claude via MCP over SSE

## Database schema

Two tables:

**`memories`** — the primary storage table:
- `id` SERIAL PRIMARY KEY
- `content` TEXT NOT NULL
- `tags` TEXT[] DEFAULT '{}'
- `source` VARCHAR(100) DEFAULT 'claude-code'
- `project` VARCHAR(100) DEFAULT ''
- `embedding` vector(768)
- `created_at` / `updated_at` TIMESTAMP with auto-update trigger

**`imported_sessions`** — tracks which Claude Code sessions have been imported and distilled:
- `session_id` VARCHAR(100) PRIMARY KEY
- `project` VARCHAR(100) DEFAULT ''
- `imported_at` TIMESTAMP DEFAULT NOW()
- `message_count` INT DEFAULT 0
- `distilled` BOOLEAN DEFAULT FALSE

Indexes:
- IVFFlat on `embedding` for cosine similarity search (lists=100)
- GIN on `tags` array
- GIN on `content` for full-text search
- BTREE on `created_at DESC`
- BTREE on `project`

## MCP server (`mcp-server/server.py`)

Python 3.12, using `FastMCP` from `mcp[cli]`. Load `all-mpnet-base-v2` from `sentence-transformers` at startup (768-dim embeddings). Use a `psycopg2.pool.ThreadedConnectionPool(1, 5)` for DB connections, exposed via a `@contextmanager db_conn()` helper that calls `register_vector` on each connection.

Expose these 10 tools:

- `save_memory(content, tags=[], source="claude-code", project="")` — embed and insert; semantic dedup check at ≥0.92 cosine similarity before inserting
- `semantic_search(query, limit=10, min_similarity=0.3, project=None)` — vector cosine search, return JSON
- `search_memories(query, limit=10, project=None)` — PostgreSQL full-text + ILIKE fallback, return JSON
- `list_memories(limit=20, tag=None, project=None)` — recent memories, optional tag/project filter, return JSON
- `get_memory(memory_id)` — fetch a single memory by ID with full content and timestamps
- `recent_context(project=None, limit=10)` — recent distilled memories (tag='distilled'); ideal for session-start recall
- `update_memory(memory_id, content=None, tags=None)` — update and re-embed if content changes
- `delete_memory(memory_id)` — delete by ID
- `list_tags()` — all unique tags with counts, return JSON
- `get_stats()` — total memories, breakdown by project, top sources, session import/distill status

All tools must use try/except and return `❌ Error: {e}` strings on failure — never propagate exceptions.

Run with `mcp.run(transport="sse")`. Bind to `host="0.0.0.0", port=3333` on the `FastMCP(...)` constructor.

## Dockerfile

`python:3.12-slim`. Set `ENV PYTHONUNBUFFERED=1`. Pre-download the model at build time with a `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"` layer so startup is fast.

## Docker Compose

- `db`: `pgvector/pgvector:pg16`, healthcheck with `pg_isready`, mount `init.sql` as entrypoint init script, persist data to `./data/postgres/`
- `mcp-server`: build from `./mcp-server/Dockerfile`, depends on `db` health, mount a named volume for the HuggingFace model cache so the model isn't re-downloaded on restart; load `~/.claude/.env` via `env_file` (optional)
- Credentials: db=`memory`, user=`claude`, password=`memory_pass`
- No `version:` key (deprecated in modern Compose)

## Import script (`import_memories.py`)

Standalone CLI at the repo root. Uses the same embedding model and DB connection as the server. Three import sources:

**Claude Code sessions** (`--claude-code`, `--project NAME`):
- Read JSONL files from `~/.claude/projects/*/`
- Extract `user` and `assistant` messages, skip if under `--min-length` (default 50)
- Handle content as either plain string or list of `{type: "text", text: "..."}` blocks
- Use `ON CONFLICT DO NOTHING` to avoid duplicates on re-runs
- Tags: `["claude-code-session", "role:{role}", "project:{project_name}"]`
- Preserve original timestamps from the JSONL `timestamp` field
- Decode project directory names dynamically using `Path.home()` — no hardcoded usernames
- Track each session in `imported_sessions`; skip sessions already marked `distilled=TRUE`

**Claude.ai export** (`--claude-ai FILE`):
- Accept `conversations.json` from Claude.ai Settings → Privacy → Export
- Handle flexible JSON structure (list or dict with `conversations` key)
- Extract `chat_messages` or `messages`, handle both `sender` and `role` fields
- Tags: `["claude-ai", "role:{role}", "convo:{name}"]`

**Text/markdown files** (`--text FILE...`):
- Chunk at 1500 chars with 200-char overlap
- Tags: `["text-import", "file:{name}", "type:{ext}"]`

## Distillation script (`distill_sessions.py`)

Standalone CLI at the repo root. Reads sessions from `imported_sessions` where `distilled=FALSE`, sends the raw message transcript to Claude haiku (`claude-haiku-4-5-20251001`), extracts durable memories (decisions, patterns, bug fixes), stores them tagged `["distilled", "project:{name}", ...]`, deletes the raw messages, and marks the session `distilled=TRUE`.

```bash
python distill_sessions.py               # distill all pending
python distill_sessions.py --project X   # filter by project
python distill_sessions.py --dry-run     # preview without writing
```

Requires `ANTHROPIC_API_KEY`. Max transcript size: 80,000 chars (~20k tokens).

## Auto-import script (`import-cron.sh`)

Shell script at repo root. Uses `$HOME` and `SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"` — no hardcoded paths. Steps:

1. `docker compose up -d` (ensure services running)
2. Run `import_memories.py --claude-code` inside the mcp-server container (has sentence-transformers)
3. Run `distill_sessions.py` inside the mcp-server container
4. Log to `/tmp/claude-memory-import.log`

## Register with Claude Code

Add to `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "claude-memory": {
      "type": "sse",
      "url": "http://localhost:3333/sse"
    }
  }
}
```

## Global Claude Code instruction

Create `~/.claude/CLAUDE.md` with instructions telling Claude to, at the start of every new session:
1. Call `semantic_search` with the current project name to recall prior context
2. Briefly summarize what was found

Also include a note to save key decisions, bug root causes, and user preferences to memory during sessions using descriptive tags like `["project:{name}", "type:decision|bug|preference|pattern"]`.

Include any system-level notes relevant to the user's environment (e.g. package manager preferences).

## macOS auto-import (LaunchAgent)

Create a LaunchAgent plist at `~/Library/LaunchAgents/com.claude.memory-import.plist` that:
- Calls `import-cron.sh` via `/bin/zsh`
- Runs every 3600 seconds
- Runs at load (`RunAtLoad: true`)
- Includes `/opt/homebrew/bin` in PATH

Validate with `plutil -lint` and load with `launchctl bootstrap gui/$(id -u)`.

**Important**: sentence-transformers is not available via brew — always run import/distill scripts inside the Docker container, never on the host directly.

## Files to create

```
claude-memory/
├── docker-compose.yml
├── init.sql
├── import_memories.py
├── distill_sessions.py
├── import-cron.sh          (chmod +x)
├── .gitignore              (exclude data/, .claude/, __pycache__, .env, *.log, .DS_Store)
├── CLAUDE.md               (project-level guidance for Claude Code)
└── mcp-server/
    ├── Dockerfile          (python:3.12-slim, PYTHONUNBUFFERED=1, pre-download model, CMD server.py)
    ├── requirements.txt    (mcp[cli], psycopg2-binary, pgvector, uvicorn, sentence-transformers, numpy, anthropic)
    └── server.py
```
