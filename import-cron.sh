#!/bin/zsh
# Auto-import and distill Claude Code sessions into memory DB
# Run by LaunchAgent every hour

LOG=/tmp/claude-memory-import.log
ERR=/tmp/claude-memory-import-error.log

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure services are running
DOCKER=/usr/local/bin/docker

# Load API keys from ~/.claude/.env
set -a && source "$HOME/.claude/.env" && set +a

$DOCKER compose up -d >> "$ERR" 2>&1
sleep 8

# Step 1: Import new sessions (skips already-distilled sessions)
$DOCKER compose run --rm -T \
  -v "$HOME/.claude/projects:/root/.claude/projects:ro" \
  -v "$SCRIPT_DIR/import_memories.py:/app/import_memories.py:ro" \
  mcp-server \
  python /app/import_memories.py --claude-code >> "$LOG" 2>&1

echo "[$(date)] Import complete" >> "$LOG"

# Step 2: Distill new sessions into curated memories (via local Ollama)
$DOCKER compose run --rm -T \
  -e OLLAMA_URL="http://host.docker.internal:11434/v1" \
  -v "$SCRIPT_DIR/distill_sessions.py:/app/distill_sessions.py:ro" \
  mcp-server \
  python /app/distill_sessions.py >> "$LOG" 2>&1

echo "[$(date)] Distillation complete" >> "$LOG"
