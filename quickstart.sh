#!/bin/zsh
# claude-memory quickstart — complete setup in one script
# Usage: bash quickstart.sh
#
# What this does:
#   1. Starts Docker services (PostgreSQL + MCP server)
#   2. Imports your existing Claude Code session history
#   3. Distills sessions into durable memories via local Ollama (if running)
#   4. Registers the MCP server with Claude Code (user scope — all projects)
#   5. Optionally installs the hourly auto-import LaunchAgent (macOS)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== claude-memory quickstart ==="
echo ""

# ── 1. Start services ──────────────────────────────────────────────────────────
echo "▶ Starting Docker services..."
docker compose up -d

echo "  Waiting for DB to be healthy..."
for i in {1..20}; do
  if docker compose exec -T db pg_isready -U claude -d memory &>/dev/null; then
    echo "  DB ready."
    break
  fi
  sleep 2
done

# ── 2. Import Claude Code session history ─────────────────────────────────────
if [[ -d "$HOME/.claude/projects" ]]; then
  echo ""
  echo "▶ Importing Claude Code session history..."
  docker compose run --rm -T \
    -v "$HOME/.claude/projects:/root/.claude/projects:ro" \
    -v "$SCRIPT_DIR/import_memories.py:/app/import_memories.py:ro" \
    mcp-server \
    python /app/import_memories.py --claude-code
else
  echo ""
  echo "  ~/.claude/projects not found — skipping session import."
fi

# ── 3. Distill sessions ────────────────────────────────────────────────────────
echo ""
if curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null; then
  echo "▶ Distilling sessions into durable memories (via Ollama)..."
  docker compose run --rm -T \
    -e OLLAMA_URL="http://host.docker.internal:11434/v1" \
    -v "$SCRIPT_DIR/distill_sessions.py:/app/distill_sessions.py:ro" \
    mcp-server \
    python /app/distill_sessions.py
else
  echo "  Ollama not running — skipping distillation."
  echo "  Start Ollama ('ollama serve') and run import-cron.sh to distill later."
fi

# ── 4. Register with Claude Code ───────────────────────────────────────────────
echo ""
echo "▶ Registering with Claude Code (user scope)..."
if claude mcp get claude-memory &>/dev/null 2>&1; then
  echo "  Already registered."
else
  claude mcp add --scope user --transport sse claude-memory http://localhost:3333/sse
  echo "  ✅ Registered."
fi

# ── 5. LaunchAgent (optional, macOS only) ─────────────────────────────────────
if [[ "$(uname)" == "Darwin" ]]; then
  echo ""
  printf "▶ Install hourly auto-import LaunchAgent? [y/N]: "
  read -r INSTALL_LA
  if [[ "${INSTALL_LA:-N}" =~ ^[Yy]$ ]]; then
    bash "$SCRIPT_DIR/setup-launchagent.sh"
  else
    echo "  Skipped. Run 'bash setup-launchagent.sh' any time to install it."
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "✅ claude-memory is ready."
echo ""
echo "  Start a new claude session and try:"
echo "    list_memories"
echo "    semantic_search \"your query here\""
echo "    get_stats"
echo ""
echo "  Backup:   bash backup.sh"
echo "  Restore:  bash restore.sh <dump_file>"
echo "  Logs:     docker compose logs -f mcp-server"
