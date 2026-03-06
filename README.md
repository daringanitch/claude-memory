# claude-memory

Persistent vector memory for Claude Code. Stores your Claude sessions, notes, and conversations in a local PostgreSQL database with semantic search — so every new session can recall what you've worked on before.

## How it works

Two Docker containers:

- **PostgreSQL 16 + pgvector** — stores memories as text + 384-dimensional embeddings
- **FastMCP server (port 3333)** — exposes 7 tools to Claude via the MCP protocol over SSE

When registered as an MCP server, Claude can search your memory by meaning (`semantic_search`), keyword (`search_memories`), or tag (`list_memories`) — and save new memories automatically during a session.

## Quick start

**Prerequisites:** Docker, Docker Compose, [Claude Code](https://claude.ai/code)

```bash
git clone https://github.com/daringanitch/claude-memory
cd claude-memory
docker compose up -d
```

Register with Claude Code by adding to `~/.claude/settings.json`:

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

Restart Claude Code. The 7 memory tools are now available in every session.

## Importing past sessions

The `import_memories.py` script bulk-imports existing history into the database. Because it depends on `sentence-transformers` (not available via brew), run it inside the container:

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

# All sources at once
  mcp-server python /app/import_memories.py --claude-code --claude-ai conversations.json --text notes.md

# Raise minimum message length (default: 50 chars)
  mcp-server python /app/import_memories.py --claude-code --min-length 100
```

## Auto-import (macOS)

A LaunchAgent runs `import-cron.sh` every hour to keep the DB current with new Claude Code sessions:

```bash
# Install
cp import-cron.sh ~/workspace/claude-memory/  # already there if cloned
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.claude.memory-import.plist

# Check logs
tail -f /tmp/claude-memory-import.log

# Trigger manually
bash import-cron.sh
```

See `import-cron.sh` for the full script. The plist file needs to be created at `~/Library/LaunchAgents/com.claude.memory-import.plist` — see [setup instructions below](#auto-import-launchagent-setup).

## MCP tools

| Tool | Description |
|------|-------------|
| `save_memory` | Save a note or piece of information with optional tags and source |
| `semantic_search` | Search by **meaning** using vector cosine similarity |
| `search_memories` | Search by **keyword** using PostgreSQL full-text search |
| `list_memories` | List recent memories, optionally filtered by tag |
| `update_memory` | Update content or tags (re-embeds automatically if content changes) |
| `delete_memory` | Delete a memory by ID |
| `list_tags` | List all tags with occurrence counts |

## Database schema

```sql
CREATE TABLE memories (
  id         SERIAL PRIMARY KEY,
  content    TEXT         NOT NULL,
  tags       TEXT[]       DEFAULT '{}',
  source     VARCHAR(100) DEFAULT 'claude-code',
  embedding  vector(384),
  created_at TIMESTAMP    DEFAULT NOW(),
  updated_at TIMESTAMP    DEFAULT NOW()
);
```

Indexes: IVFFlat for vector cosine search, GIN for tag arrays, GIN for full-text search, BTREE on `created_at`.

## Configuration

All defaults are set in `docker-compose.yml`. Override via environment variables:

| Variable | Default |
|----------|---------|
| `POSTGRES_DB` | `memory` |
| `POSTGRES_USER` | `claude` |
| `POSTGRES_PASSWORD` | `memory_pass` |
| `DATABASE_URL` | `postgresql://claude:memory_pass@db:5432/memory` |

Data is persisted to `./data/postgres/`. The HuggingFace model cache is stored in a named Docker volume (`model_cache`) so `all-MiniLM-L6-v2` isn't re-downloaded on restart.

## Auto-import LaunchAgent setup

Create `~/Library/LaunchAgents/com.claude.memory-import.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.memory-import</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>/path/to/claude-memory/import-cron.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Then load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.claude.memory-import.plist
```

## Global Claude Code integration

Add `~/.claude/CLAUDE.md` to instruct Claude to recall context automatically at the start of every session:

```markdown
## Session Start — Memory Recall

At the start of every new session, use the claude-memory MCP server:
1. Call semantic_search with the current project name
2. Summarize what was found so prior context is visible
```

## Stack

- [pgvector](https://github.com/pgvector/pgvector) — vector similarity search for PostgreSQL
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [sentence-transformers](https://www.sbert.net/) — `all-MiniLM-L6-v2` for 384-dim embeddings
- [Model Context Protocol](https://modelcontextprotocol.io/) — tool interface for Claude
