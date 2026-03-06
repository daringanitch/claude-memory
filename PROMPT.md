# Claude Memory — Genesis Prompt

> Paste this into Claude Code to build the entire claude-memory system from scratch.

---

Build me a persistent vector memory system for Claude Code. It should store my past Claude sessions, notes, and conversations in a local database so that every new Claude Code session can recall what I've worked on before.

## Architecture

Two Docker containers managed by Docker Compose:

1. **PostgreSQL 16 with pgvector** — stores memories as text + vector embeddings
2. **FastMCP server on port 3333** — exposes memory tools to Claude via MCP over SSE

## Database schema

Single `memories` table:
- `id` SERIAL PRIMARY KEY
- `content` TEXT NOT NULL
- `tags` TEXT[] DEFAULT '{}'
- `source` VARCHAR(100) DEFAULT 'claude-code'
- `embedding` vector(384)
- `created_at` / `updated_at` TIMESTAMP with auto-update trigger

Indexes:
- IVFFlat on `embedding` for cosine similarity search (lists=100)
- GIN on `tags` array
- GIN on `content` for full-text search
- BTREE on `created_at DESC`

## MCP server (`mcp-server/server.py`)

Python 3.12, using `FastMCP` from `mcp[cli]`. Load `all-MiniLM-L6-v2` from `sentence-transformers` at startup (384-dim embeddings). Connect to PostgreSQL via `psycopg2` + `pgvector`.

Expose these 7 tools:

- `save_memory(content, tags=[], source="claude-code")` — embed and insert
- `semantic_search(query, limit=10, min_similarity=0.3)` — vector cosine search, return JSON
- `search_memories(query, limit=10)` — PostgreSQL full-text + ILIKE fallback, return JSON
- `list_memories(limit=20, tag=None)` — recent memories, optional tag filter, return JSON
- `update_memory(memory_id, content=None, tags=None)` — update and re-embed if content changes
- `delete_memory(memory_id)` — delete by ID
- `list_tags()` — all unique tags with counts, return JSON

Run with `mcp.run(transport="sse")`. Bind to `host="0.0.0.0", port=3333` on the `FastMCP(...)` constructor.

## Docker Compose

- `db`: `pgvector/pgvector:pg16`, healthcheck with `pg_isready`, mount `init.sql` as entrypoint init script, persist data to `./data/postgres/`
- `mcp-server`: build from `./mcp-server/Dockerfile`, depends on `db` health, mount a named volume for the HuggingFace model cache so the model isn't re-downloaded on restart
- Credentials: db=`memory`, user=`claude`, password=`memory_pass`

## Import script (`import_memories.py`)

Standalone CLI at the repo root. Uses the same embedding model and DB connection as the server. Three import sources:

**Claude Code sessions** (`--claude-code`, `--project NAME`):
- Read JSONL files from `~/.claude/projects/*/`
- Extract `user` and `assistant` messages, skip if under `--min-length` (default 50)
- Handle content as either plain string or list of `{type: "text", text: "..."}` blocks
- Use `ON CONFLICT DO NOTHING` to avoid duplicates on re-runs
- Tags: `["claude-code-session", "role:{role}", "project:{project_name}"]`
- Preserve original timestamps from the JSONL `timestamp` field

**Claude.ai export** (`--claude-ai FILE`):
- Accept `conversations.json` from Claude.ai Settings → Privacy → Export
- Handle flexible JSON structure (list or dict with `conversations` key)
- Extract `chat_messages` or `messages`, handle both `sender` and `role` fields
- Tags: `["claude-ai", "role:{role}", "convo:{name}"]`

**Text/markdown files** (`--text FILE...`):
- Chunk at 1500 chars with 200-char overlap
- Tags: `["text-import", "file:{name}", "type:{ext}"]`

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

Create `import-cron.sh` at the repo root:
- Ensure `docker compose up -d` is running
- Run the import script inside the mcp-server container (it has sentence-transformers) using `docker compose run --rm -T` with `~/.claude/projects` mounted read-only and `import_memories.py` bind-mounted into `/app/`
- Log to `/tmp/claude-memory-import.log`

Create a LaunchAgent plist at `~/Library/LaunchAgents/com.claude.memory-import.plist` that:
- Calls `import-cron.sh` via `/bin/zsh`
- Runs every 3600 seconds
- Runs at load (`RunAtLoad: true`)
- Includes `/opt/homebrew/bin` in PATH

Validate with `plutil -lint` and load with `launchctl bootstrap gui/$(id -u)`.

**Important**: sentence-transformers is not available via brew — always run the import script inside the Docker container, never on the host directly.

## Files to create

```
claude-memory/
├── docker-compose.yml
├── init.sql
├── import_memories.py
├── import-cron.sh          (chmod +x)
├── .gitignore              (exclude data/, .claude/, __pycache__, .env, *.log, .DS_Store)
├── CLAUDE.md               (project-level guidance for Claude Code)
└── mcp-server/
    ├── Dockerfile          (python:3.12-slim, pip install -r requirements.txt, CMD server.py)
    ├── requirements.txt    (mcp[cli], psycopg2-binary, pgvector, uvicorn, sentence-transformers, numpy)
    └── server.py
```

After building, also:
- Initialize a git repo, create a GitHub repo, and push
- Import all existing Claude Code sessions from `~/.claude/projects/` into the DB
- Write a README.md covering quick start, import usage, MCP tools, schema, and configuration
