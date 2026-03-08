#!/bin/zsh
# Auto-import and distill Claude Code sessions into memory DB
# Run by LaunchAgent every hour

LOG=/tmp/claude-memory-import.log
ERR=/tmp/claude-memory-import-error.log

cd /Users/daringanitch/workspace/claude-memory

# Ensure services are running
/opt/homebrew/bin/docker compose up -d >> "$ERR" 2>&1
sleep 8

# Step 1: Import new sessions (skips already-distilled sessions)
/opt/homebrew/bin/docker compose run --rm -T \
  -v /Users/daringanitch/.claude/projects:/root/.claude/projects:ro \
  -v /Users/daringanitch/workspace/claude-memory/import_memories.py:/app/import_memories.py:ro \
  mcp-server \
  python /app/import_memories.py --claude-code >> "$LOG" 2>&1

echo "[$(date)] Import complete" >> "$LOG"

# Step 2: Distill new sessions into curated memories
/opt/homebrew/bin/docker compose run --rm -T \
  --env-file /Users/daringanitch/.claude/.env \
  -v /Users/daringanitch/workspace/claude-memory/distill_sessions.py:/app/distill_sessions.py:ro \
  mcp-server \
  python /app/distill_sessions.py >> "$LOG" 2>&1

echo "[$(date)] Distillation complete" >> "$LOG"
