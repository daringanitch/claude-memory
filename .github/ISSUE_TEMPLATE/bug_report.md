---
name: Bug report
about: Something isn't working
labels: bug
---

**Describe the bug**
A clear description of what's wrong and what you expected to happen.

**Steps to reproduce**
1. ...
2. ...

**Environment**
- OS: (e.g. macOS 14, Ubuntu 24.04)
- Docker version: (`docker --version`)
- Claude Code version: (`claude --version`)
- Ollama version if relevant: (`ollama --version`)

**Logs**
```
# MCP server logs
docker compose logs mcp-server

# Import/distill cron logs (if relevant)
cat /tmp/claude-memory-import.log
```

**Additional context**
Any other details that might help.
