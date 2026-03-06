#!/bin/zsh
# Auto-import Claude Code sessions into memory DB
# Run by LaunchAgent every hour

LOG=/tmp/claude-memory-import.log
ERR=/tmp/claude-memory-import-error.log

cd /Users/daringanitch/workspace/claude-memory

# Ensure services are running
/opt/homebrew/bin/docker compose up -d >> "$ERR" 2>&1
sleep 8

# Run import inside the mcp-server container (has sentence-transformers)
/opt/homebrew/bin/docker compose run --rm -T \
  -v /Users/daringanitch/.claude/projects:/root/.claude/projects:ro \
  -v /Users/daringanitch/workspace/claude-memory/import_memories.py:/app/import_memories.py:ro \
  mcp-server \
  python /app/import_memories.py --claude-code >> "$LOG" 2>&1

echo "[$(date)] Import complete" >> "$LOG"
