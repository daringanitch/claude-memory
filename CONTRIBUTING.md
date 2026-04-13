# Contributing to claude-memory

Thanks for your interest in contributing. This document covers how to report issues, suggest features, and submit pull requests.

## Reporting issues

Before opening an issue, check if it already exists. When filing a new one, please include:

- Your OS and Docker version (`docker --version`)
- Your Claude Code version (`claude --version`)
- The relevant section of `/tmp/claude-memory-import.log` or `docker compose logs mcp-server`
- Steps to reproduce

## Suggesting features

Open a GitHub issue with the `enhancement` label. Describe what problem you're trying to solve, not just the solution — this helps discussion.

## Pull requests

### Setup

```bash
git clone https://github.com/daringanitch/claude-memory
cd claude-memory
docker compose up -d
```

### Workflow

1. Fork the repo and create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```
2. Make your changes
3. Run the test suite — all tests must pass:
   ```bash
   brew install pytest   # one-time
   pytest tests/ -v
   ```
4. Update `README.md` and `CLAUDE.md` if you're adding or changing behaviour
5. Add a row to the **What's new** table in `README.md` with today's date
6. Push and open a PR against `main`

### PR checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Docs updated (README + CLAUDE.md)
- [ ] What's new table updated with date
- [ ] New MCP tools documented in the tools table
- [ ] New scripts have usage examples in README

### Adding a new MCP tool

1. Add the tool function to `mcp-server/server.py` with a `@mcp.tool()` decorator
2. Add a row to the MCP tools table in both `README.md` and `CLAUDE.md`
3. Update the tool count in the "How it works" section
4. Add a test in `tests/test_server.py`

### Code style

- Python: follow the existing style — no type annotation noise, straightforward functions
- Keep error handling at the boundaries; don't add defensive checks inside already-trusted paths
- No unnecessary abstractions — if a helper is only used once, inline it

## Running tests

```bash
# All tests (no Docker or GPU required — heavy deps are mocked)
pytest tests/ -v

# Single file
pytest tests/test_server.py -v

# With coverage
pytest tests/ -v --cov --cov-report=term-missing
```

## Questions

Open a GitHub issue or discussion — happy to help.
