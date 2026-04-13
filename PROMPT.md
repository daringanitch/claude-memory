# Claude Memory — Genesis Prompt

> Paste this into Claude Code to build the entire claude-memory system from scratch.

---

Build me a persistent vector memory system for Claude Code. It should store my past Claude sessions, notes, and conversations in a local database so that every new Claude Code session can recall what I've worked on before. It should also automatically learn my preferences and working patterns by analysing session behaviour over time.

## Architecture

Two Docker containers managed by Docker Compose:

1. **PostgreSQL 16 with pgvector** — stores memories as text + 768-dimensional vector embeddings
2. **FastMCP server on port 3333** — exposes 18 memory tools to Claude via MCP over SSE

Plus three standalone Python scripts at the repo root that run inside the MCP server container:
- `import_memories.py` — bulk-imports Claude Code sessions, Claude.ai exports, and text files
- `distill_sessions.py` — uses a local Ollama LLM to extract durable facts from raw session transcripts
- `extract_signals.py` — LLM-free behavioural signal extractor (correction preferences, workflow patterns)

## Database schema

**`memories`** — primary storage:
- `id` SERIAL PRIMARY KEY
- `content` TEXT NOT NULL
- `content_hash` TEXT GENERATED ALWAYS AS (md5(content)) STORED — unique index for exact-dedup
- `tags` TEXT[] DEFAULT '{}'
- `source` VARCHAR(100) DEFAULT 'claude-code'
- `project` VARCHAR(100) DEFAULT ''
- `embedding` vector(768)
- `created_at` / `updated_at` TIMESTAMP with auto-update trigger
- `deleted_at` TIMESTAMP DEFAULT NULL — NULL = active, non-NULL = soft-deleted

**`imported_sessions`** — tracks session import/distill/signal state:
- `session_id` VARCHAR(100) PRIMARY KEY
- `project` VARCHAR(100) DEFAULT ''
- `imported_at` TIMESTAMP DEFAULT NOW()
- `message_count` INT DEFAULT 0
- `distilled` BOOLEAN DEFAULT FALSE
- `distill_failures` INT DEFAULT 0 — sessions capped at 3 failures are permanently skipped
- `signals_extracted` BOOLEAN DEFAULT FALSE

Indexes:
- `UNIQUE` on `content_hash` — enforces exact-duplicate prevention atomically
- IVFFlat on `embedding` for cosine similarity search (lists=100)
- GIN on `tags` array
- GIN on `content` for full-text search
- BTREE on `created_at DESC`, `project`, and `deleted_at WHERE deleted_at IS NULL`

## MCP server (`mcp-server/server.py`)

Python 3.12, using `FastMCP` from `mcp[cli]`. Load `all-mpnet-base-v2` from `sentence-transformers` at startup (768-dim embeddings). Use a `psycopg2.pool.ThreadedConnectionPool(1, 5)` for DB connections, exposed via a `@contextmanager db_conn()` helper.

**Critical `db_conn()` implementation**: Always rollback at entry and in the finally block. Do NOT set `conn.autocommit = False` explicitly — it's the psycopg2 default and calling it while a transaction is open throws `set_session cannot be used inside a transaction`:

```python
@contextmanager
def db_conn():
    conn = _pool.getconn()
    try:
        conn.rollback()
        register_vector(conn)
        yield conn
    finally:
        conn.rollback()
        _pool.putconn(conn)
```

**Search cache**: Use an `OrderedDict` (LRU, max 500 entries, 10-minute TTL) to cache `semantic_search` and `search_memories` results. Invalidate on any write. Expose `POST /cache/invalidate` to clear manually.

**Write guard thresholds**:
- `GUARD_NOOP_THRESHOLD = 0.92` — skip save if near-duplicate found at this similarity
- `GUARD_UPDATE_THRESHOLD = 0.75` — suggest update instead of new save

Use structured logging throughout. Log errors via `log.error(...)` before returning `❌ Error: {e}` strings.

### All 18 MCP tools

**Session start:**
- `startup_context(project=None)` — compact session-start snapshot: behavioural signals + recent distilled memories in one call; no search query needed. Returns a formatted markdown block.

**Write:**
- `save_memory(content, tags=[], source="claude-code", project="")` — embed and insert; semantic dedup at ≥0.92 cosine similarity; `ON CONFLICT (content_hash) DO UPDATE SET deleted_at=NULL` to restore exact-duplicate soft-deleted rows
- `check_memory(content)` — dry-run write guard; returns ADD/UPDATE/NOOP with nearest match preview
- `update_memory(memory_id, content=None, tags=None, force=False)` — update and re-embed; warns on near-duplicate unless `force=True`

**Search:**
- `semantic_search(query, limit=10, min_similarity=0.3, project=None, since=None, before=None)` — vector cosine search; cached 10 min
- `search_memories(query, limit=10, project=None, since=None, before=None)` — PostgreSQL full-text + ILIKE fallback; cached 10 min
- `hybrid_search(query, limit=10, keyword_weight=0.7, semantic_weight=0.3, min_semantic_similarity=0.1, project=None, since=None, before=None)` — combined keyword + semantic with configurable weights

**Read:**
- `list_memories(limit=20, offset=0, tag=None, project=None, since=None, before=None)` — paginated; returns `{total, limit, offset, memories[]}`
- `get_memory(memory_id)` — fetch by ID; returns even soft-deleted rows (deleted_at visible)
- `recent_context(project=None, limit=10)` — recent distilled memories; falls back to most recent active if none distilled

**Delete/restore:**
- `delete_memory(memory_id)` — soft-delete (hidden, recoverable)
- `restore_memory(memory_id)` — restore a soft-deleted memory
- `purge_memory(memory_id)` — permanent delete; requires soft-delete first (two-step safety gate)
- `bulk_delete(tag=None, project=None, source=None, dry_run=True)` — soft-delete all matching; `dry_run=True` by default

**Utilities:**
- `list_tags()` — all unique tags with counts (active only)
- `get_stats()` — counts by project/source, deleted count, session import/distill/signals status, cache size
- `export_memories(project=None, tag=None, since=None, before=None, output_format="json")` — export as JSON or markdown
- `find_duplicates(threshold=0.85, limit=20, project=None, scan_limit=500)` — near-duplicate pairs above threshold

### HTTP endpoints

- `GET /health` — liveness probe; returns `{"status":"ok"}` (200) or `{"status":"degraded"}` (503)
- `POST /cache/invalidate` — clear the search cache; call after bulk imports

## Dockerfile

`python:3.12-slim`. Install torch CPU-only first (large layer, cache separately), then `requirements.txt`. Pre-download the embedding model at build time. Set `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` in the compose file to prevent network calls on restart.

## Docker Compose

- `db`: `pgvector/pgvector:pg16`, healthcheck with `pg_isready`, mount `init.sql`, persist to `./data/postgres/`, container name `claude-memory-db`
- `mcp-server`: build `./mcp-server/`, depends on `db` health, named volume for HuggingFace model cache, load `~/.claude/.env` via `env_file` (optional, `required: false`), container name `claude-memory-mcp`
- Credentials: db=`memory`, user=`claude`, password=`memory_pass`
- No `version:` key (deprecated)

## Import script (`import_memories.py`)

Standalone CLI. Structured logging (`logging.getLogger("import")`). Deduplicates using `ON CONFLICT (content_hash) DO NOTHING`. Three sources:

**Claude Code sessions** (`--claude-code`, `--project NAME`, `--min-length 50`):
- Read JSONL from `~/.claude/projects/*/`
- Extract `user`/`assistant` messages; handle content as string or `[{type:"text", text:"..."}]` blocks
- Preserve original timestamps; decode project dirs using `Path.home()` — no hardcoded paths
- Track sessions in `imported_sessions`; skip already-imported sessions

**Claude.ai export** (`--claude-ai FILE`): `conversations.json` from Claude.ai export; handle flexible JSON structure.

**Text/markdown** (`--text FILE...`): chunk at 1500 chars with 200-char overlap.

## Distillation script (`distill_sessions.py`)

Uses a **local Ollama LLM** (no API key required). Default model: `qwen2.5:7b`. Connect via OpenAI-compatible API at `OLLAMA_URL` (default `http://localhost:11434/v1`, use `http://host.docker.internal:11434/v1` inside Docker).

- Reads `imported_sessions` where `distilled=FALSE` and `distill_failures < 3`
- Sends transcript to Ollama; extracts durable memories (decisions, patterns, bug fixes) as JSON array
- Stores memories tagged `["distilled", "project:{name}", ...]`; deletes raw messages; marks `distilled=TRUE`
- Sessions failing 3 times get capped (`distill_failures >= 3`); use `--reset-failures` to retry
- Parallel processing via `ThreadPoolExecutor` (`--workers 4` default); batch embedding

```bash
python distill_sessions.py                          # all pending
python distill_sessions.py --project X              # filter by project
python distill_sessions.py --dry-run                # preview without writing
python distill_sessions.py --model llama3.2:3b      # model override
python distill_sessions.py --reset-failures         # reset all capped sessions
python distill_sessions.py --reset-failures abc123  # reset one session
```

## Signal extraction script (`extract_signals.py`)

LLM-free behavioural analysis. Reads session JSONL files directly. Marks processed sessions via `signals_extracted` column. Two types of output:

**Per-session** (saved immediately):
- Correction signals: user messages matching negation/correction patterns immediately after assistant tool_use → `type:preference` memories tagged `source:signals`

**Per-project** (aggregated, upserted on every run):
- Workflow fingerprint: tool category breakdown → `type:pattern`
- Command habits: most-used bash commands → `type:pattern`
- File hotspots: files accessed 2+ times → `type:pattern`

Aggregate memories use `source = signals/aggregate/{type}/{project}` — delete-then-insert so they stay current.

```bash
python extract_signals.py            # all pending sessions
python extract_signals.py --dry-run  # preview without writing
python extract_signals.py --project X
```

## Auto-import script (`import-cron.sh`)

Shell script at repo root. Uses `DOCKER=$(which docker || echo /usr/local/bin/docker)` — no hardcoded paths. Three steps:

1. `docker compose up -d` (ensure services running), sleep 8
2. Run `import_memories.py --claude-code` inside the mcp-server container
3. Run `distill_sessions.py` inside the mcp-server container (pass `OLLAMA_URL=http://host.docker.internal:11434/v1`)
4. Run `extract_signals.py` inside the mcp-server container
5. Log all output to `/tmp/claude-memory-import.log`

## LaunchAgent setup script (`setup-launchagent.sh`)

Generates and installs `~/Library/LaunchAgents/com.claude-memory.import.plist`:
- `StartInterval: 3600`, `RunAtLoad: false`
- stdout/stderr to `/tmp/`, PATH including `/opt/homebrew/bin`, HOME env var set
- Unload existing before reloading

## Migrations (`migrations/`)

Schema changes are in numbered SQL files, idempotent with `IF NOT EXISTS`:
- `001_add_content_hash_dedup.sql` — adds `content_hash` generated column + unique index
- `002_soft_deletes.sql` — adds `deleted_at` column
- `003_distill_failure_cap.sql` — adds `distill_failures` column
- `004_signals_extracted.sql` — adds `signals_extracted` column

`extract_signals.py` also applies migration 004 automatically at startup via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

## Quickstart script (`quickstart.sh`)

Six steps: start Docker → import sessions → distill (if Ollama running) → extract signals → register with Claude Code → optionally install LaunchAgent.

```bash
claude mcp add --scope user --transport sse claude-memory http://localhost:3333/sse
```

## Test suite (`tests/`)

`tests/conftest.py`: patch heavy deps via `sys.modules` before imports — sentence_transformers, psycopg2, pgvector, openai, mcp. Make `@mcp.tool()` a pass-through decorator.

Key test files:
- `test_import.py` — `extract_text()` for strings, content blocks, edge cases
- `test_distill.py` — `parse_distilled()`, `build_transcript()` including truncation
- `test_server.py` — all tool functions with mocked DB; cover success, empty results, errors, invalid params, cache behaviour, soft-delete flow, dedup logic

76 tests total. Only `pytest` required — no Docker, GPU, or API keys.

## CI (`.github/workflows/ci.yml`)

Trigger on push/PR to main. Python 3.11 and 3.12 matrix. Steps: install pytest + pip-audit, run `pip-audit --fail-on high`, run `pytest tests/ -v --cov --cov-fail-under=80`.

## Global Claude Code instruction

Create `~/.claude/CLAUDE.md` telling Claude to, at the start of every new session:
1. Call `startup_context` with the last segment of the current working directory as the project name (e.g. cwd `/home/user/projects/my-app` → `startup_context("my-app")`)
2. Call `semantic_search` for deeper recall on specific topics if needed
3. Briefly summarize what was found

Also save key decisions, bug root causes, and preferences using `save_memory` with tags `["project:{name}", "type:decision|bug|preference|pattern"]`.

## Files to create

```
claude-memory/
├── quickstart.sh
├── docker-compose.yml
├── init.sql
├── import_memories.py
├── distill_sessions.py
├── extract_signals.py
├── import-cron.sh
├── setup-launchagent.sh
├── backup.sh
├── restore.sh
├── CLAUDE.md
├── CONTRIBUTING.md
├── .gitignore               (exclude data/, venv/, __pycache__, .env, *.log, .DS_Store, backups/)
├── migrations/
│   ├── 001_add_content_hash_dedup.sql
│   ├── 002_soft_deletes.sql
│   ├── 003_distill_failure_cap.sql
│   └── 004_signals_extracted.sql
├── tests/
│   ├── conftest.py
│   ├── test_import.py
│   ├── test_distill.py
│   └── test_server.py
├── .github/
│   ├── workflows/ci.yml
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
└── mcp-server/
    ├── Dockerfile
    ├── requirements.txt     (mcp[cli], psycopg2-binary, pgvector, uvicorn, sentence-transformers, numpy, openai)
    └── server.py
```
