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

Python 3.12, using `FastMCP` from `mcp[cli]`. Load `all-mpnet-base-v2` from `sentence-transformers` at startup (768-dim embeddings). Use a `psycopg2.pool.ThreadedConnectionPool(1, 5)` for DB connections, exposed via a `@contextmanager db_conn()` helper.

**Critical `db_conn()` implementation**: The connection pool reuses connections across calls. Always rollback at entry (clears any leftover transaction from previous pool use) and in the finally block (ensures clean state before return). Do NOT set `conn.autocommit = False` explicitly — it's the psycopg2 default and calling it while a transaction is open throws `set_session cannot be used inside a transaction`:

```python
@contextmanager
def db_conn():
    conn = _pool.getconn()
    try:
        conn.rollback()         # clean any leftover transaction
        register_vector(conn)
        yield conn
    finally:
        conn.rollback()         # clean before returning to pool
        _pool.putconn(conn)
```

Use structured logging throughout (`logging.basicConfig` with timestamp, level, name format). Log errors via `log.error(...)` before returning `❌ Error: {e}` strings — never propagate exceptions to the MCP caller.

Expose these 11 tools:

- `save_memory(content, tags=[], source="claude-code", project="")` — embed and insert; semantic dedup check at ≥0.92 cosine similarity before inserting
- `semantic_search(query, limit=10, min_similarity=0.3, project=None, since=None, before=None)` — vector cosine search; `since`/`before` accept ISO date strings for time-range filtering
- `search_memories(query, limit=10, project=None, since=None, before=None)` — PostgreSQL full-text + ILIKE fallback; supports time-range filtering
- `list_memories(limit=20, tag=None, project=None, since=None, before=None)` — recent memories, optional filters; supports time-range filtering
- `get_memory(memory_id)` — fetch a single memory by ID with full content and timestamps
- `recent_context(project=None, limit=10)` — recent distilled memories (tag='distilled'); ideal for session-start recall
- `update_memory(memory_id, content=None, tags=None)` — update and re-embed if content changes
- `delete_memory(memory_id)` — delete by ID
- `list_tags()` — all unique tags with counts
- `get_stats()` — total memories, breakdown by project, top sources, session import/distill status
- `export_memories(project=None, tag=None, since=None, before=None, output_format="json")` — export as JSON or markdown; `output_format` is either `"json"` or `"markdown"`

Build SQL queries for the search/list tools dynamically using condition lists and params arrays so filters compose cleanly. Wrap the full-text OR condition in parentheses when ANDing with additional filters.

Add a `_parse_dt(value, name)` helper that returns `(datetime, None)` on success or `(None, error_str)` on invalid input, used by all tools with date parameters.

## Dockerfile

`python:3.12-slim`. Set `ENV PYTHONUNBUFFERED=1`. Pre-download the model at build time with a `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"` layer so startup is fast.

## Docker Compose

- `db`: `pgvector/pgvector:pg16`, healthcheck with `pg_isready`, mount `init.sql` as entrypoint init script, persist data to `./data/postgres/`, container name `claude-memory-db`
- `mcp-server`: build from `./mcp-server/Dockerfile`, depends on `db` health, mount a named volume for the HuggingFace model cache; load `~/.claude/.env` via `env_file` (optional, `required: false`), container name `claude-memory-mcp`
- Credentials: db=`memory`, user=`claude`, password=`memory_pass`
- No `version:` key (deprecated in modern Compose)

## Import script (`import_memories.py`)

Standalone CLI at the repo root. Uses the same embedding model and DB connection as the server. Use structured logging (`logging.getLogger("import")`). Three import sources:

**Claude Code sessions** (`--claude-code`, `--project NAME`):
- Read JSONL files from `~/.claude/projects/*/`
- Extract `user` and `assistant` messages, skip if under `--min-length` (default 50)
- Handle content as either plain string or list of `{type: "text", text: "..."}` blocks
- Use `ON CONFLICT DO NOTHING` to avoid duplicates on re-runs
- Tags: `["claude-code-session", "role:{role}", "project:{project_name}"]`
- Preserve original timestamps from the JSONL `timestamp` field
- Decode project directory names dynamically using `Path.home()` — no hardcoded usernames
- Track each session in `imported_sessions`; skip sessions already marked `distilled=TRUE`
- Catch `psycopg2.Error` and general `Exception` separately in per-session error handling

**Claude.ai export** (`--claude-ai FILE`):
- Accept `conversations.json` from Claude.ai Settings → Privacy → Export
- Handle flexible JSON structure (list or dict with `conversations` key)
- Extract `chat_messages` or `messages`, handle both `sender` and `role` fields
- Tags: `["claude-ai", "role:{role}", "convo:{name}"]`

**Text/markdown files** (`--text FILE...`):
- Chunk at 1500 chars with 200-char overlap
- Tags: `["text-import", "file:{name}", "type:{ext}"]`

Do NOT import numpy — it is not needed.

## Distillation script (`distill_sessions.py`)

Standalone CLI at the repo root. Use structured logging (`logging.getLogger("distill")`). Reads sessions from `imported_sessions` where `distilled=FALSE`, sends the raw message transcript to Claude Haiku (`claude-haiku-4-5-20251001`), extracts durable memories (decisions, patterns, bug fixes), stores them tagged `["distilled", "project:{name}", ...]`, deletes the raw messages, and marks the session `distilled=TRUE`.

```bash
python distill_sessions.py               # distill all pending
python distill_sessions.py --project X   # filter by project
python distill_sessions.py --dry-run     # preview without writing
```

Requires `ANTHROPIC_API_KEY`. Max transcript size: 80,000 chars (~20k tokens).

Catch `json.JSONDecodeError`, `anthropic.APIError`, `psycopg2.Error`, and general `Exception` separately. Do NOT import numpy.

## Auto-import script (`import-cron.sh`)

Shell script at repo root. Uses `$HOME` and `SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"` — no hardcoded paths. Steps:

1. `docker compose up -d` (ensure services running)
2. Run `import_memories.py --claude-code` inside the mcp-server container
3. Run `distill_sessions.py` inside the mcp-server container (pass `ANTHROPIC_API_KEY` via `-e`, not `--env-file` which isn't supported in all Docker Compose versions)
4. Log to `/tmp/claude-memory-import.log`

## LaunchAgent setup script (`setup-launchagent.sh`)

Script that auto-generates and installs `~/Library/LaunchAgents/com.claude-memory.import.plist`. Should:
- Write the plist with `StartInterval: 3600`, stdout/stderr log paths in `/tmp/`, and PATH including `/opt/homebrew/bin`
- Unload any existing agent before reloading
- Print useful commands (tail logs, start now, disable, re-enable)

## Backup and restore scripts

`backup.sh`: use `docker exec` to run `pg_dump --format=custom --compress=9` against the `claude-memory-db` container, save to `./backups/claude-memory-TIMESTAMP.pgdump`. Ensure the container is running first.

`restore.sh`: accept a dump file path as argument, prompt for `YES` confirmation, drop and recreate the `memory` database, restore via `pg_restore`. Remind user to restart the MCP server after restore.

## Quickstart script (`quickstart.sh`)

One-command setup script that chains all the above steps: start Docker, wait for DB health, import Claude Code sessions, distill (if API key available), register with Claude Code via `claude mcp add --scope user --transport sse`, optionally install the LaunchAgent. Print a helpful summary at the end.

## Test suite (`tests/`)

`tests/conftest.py`: patch heavy dependencies via `sys.modules` before any source module imports them — sentence_transformers, psycopg2, pgvector, anthropic, mcp. Make `@mcp.tool()` a pass-through decorator so decorated functions remain callable.

`tests/test_import.py`: test `extract_text()` for plain strings, list of content blocks, empty lists, non-text block types.

`tests/test_distill.py`: test `parse_distilled()` for valid JSON, JSON with preamble, empty array, no array, multiple items; test `build_transcript()` for joining, skipping empty content, truncation.

`tests/test_server.py`: test `_parse_dt()` for valid dates, valid datetimes, empty/None, invalid; test tool functions (`save_memory`, `semantic_search`, `list_memories`, `delete_memory`, `export_memories`) with mocked DB connections — cover success paths, empty results, error conditions, invalid parameters.

37 tests total. Only `pytest` required to run them — no Docker, no GPU, no API keys.

## CI (`./github/workflows/ci.yml`)

GitHub Actions workflow: trigger on push/PR to main, matrix of Python 3.11 and 3.12, install only `pytest`, run `pytest tests/ -v`.

## Register with Claude Code

Use `claude mcp add` — NOT `settings.json` (the `mcpServers` key in settings.json is not read by Claude Code):

```bash
claude mcp add --scope user --transport sse claude-memory http://localhost:3333/sse
```

## Global Claude Code instruction

Create `~/.claude/CLAUDE.md` with instructions telling Claude to, at the start of every new session:
1. Call `semantic_search` with the current project name to recall prior context
2. Briefly summarize what was found

Also include a note to save key decisions, bug root causes, and user preferences to memory during sessions using descriptive tags like `["project:{name}", "type:decision|bug|preference|pattern"]`.

## Files to create

```
claude-memory/
├── quickstart.sh            (chmod +x — one-command setup)
├── docker-compose.yml
├── init.sql
├── import_memories.py
├── distill_sessions.py
├── import-cron.sh           (chmod +x)
├── setup-launchagent.sh     (chmod +x)
├── backup.sh                (chmod +x)
├── restore.sh               (chmod +x)
├── .gitignore               (exclude data/, .claude/, __pycache__, .env, *.log, .DS_Store, backups/)
├── CLAUDE.md
├── tests/
│   ├── conftest.py
│   ├── test_import.py
│   ├── test_distill.py
│   └── test_server.py
├── .github/workflows/ci.yml
└── mcp-server/
    ├── Dockerfile
    ├── requirements.txt     (mcp[cli], psycopg2-binary, pgvector, uvicorn, sentence-transformers, anthropic)
    └── server.py
```
