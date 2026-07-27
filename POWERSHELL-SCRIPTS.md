# PowerShell Scripts Reference

All scripts target **Windows PowerShell 5.1** (the default shell on Windows 10/11).
They live at the repo root. Path-aware defaults use `$PSScriptRoot`, so scripts
work from whichever directory you cloned the repo into — no `-RepoRoot` override
needed in the common case.

| Script | Purpose | Typical caller |
|---|---|---|
| [`Start-Stack.ps1`](#start-stackps1)             | Bring the entire workstation up in dependency order — Docker, optional Msty, compose stack (DB + ollama + MCP server), Obsidian, Claude Desktop. | Manual / login task |
| [`Stop-Stack.ps1`](#stop-stackps1)               | Graceful shutdown in reverse startup order. | Manual / shutdown task |
| [`Test-StackHealth.ps1`](#test-stackhealthps1)   | One-shot health check across all 4 layers (compose, MCP, Ollama, REST). | `Start-Stack.ps1`, manual |
| [`Invoke-ImportPipeline.ps1`](#invoke-importpipelineps1) | Run the 5-stage import → distill → behavioral pipeline. | Windows Task Scheduler |
| [`Backup-Memory.ps1`](#backup-memoryps1)         | `pg_dump` the memory database to a compressed file. | Manual / scheduled task |
| [`Restore-Memory.ps1`](#restore-memoryps1)       | **Destructive** — drop and recreate the memory DB from a dump. | Manual recovery |

---

## `Start-Stack.ps1`

Five-layer startup with per-layer health checks. Exits with the failing layer
number (1–5) on any failure; remaining layers are skipped. Idempotent — safe to
run when components are already up.

**Layers:**

1. **Docker Desktop** daemon (waits up to 3 min for cold start)
2. **Msty / Local AI** — *optional, non-fatal*. Best-effort launch + netstat port discovery for other local automation. The pipeline no longer needs Msty (the LLM runtime is the in-stack `ollama` service), so a failure here only warns and the stack continues.
3. **Docker Compose stack** (`db` + `ollama` + `mcp-server`) — also ensures the `qwen2.5:7b` model is present in the `ollama` volume (one-time pull on a fresh volume), then verified by `Test-StackHealth.ps1`
4. **Obsidian**
5. **Claude Desktop** (launched via AUMID since Store-install `.exe`s are ACL-blocked)

**Parameters:** `-RepoRoot`, `-DockerExe`, `-MstyExe`, `-ObsidianExe`, `-ClaudeAUMID`, `-MstyLog`, `-NoGpu` (all have sensible defaults).

> **GPU:** Layer 3 auto-detects the NVIDIA docker runtime and, when present, brings the stack up with the `docker-compose.gpu.yml` override (GPU-accelerated ollama). On hosts without it, ollama runs on CPU. Pass `-NoGpu` to force CPU even when a GPU is present.

```powershell
# Bring everything up
.\Start-Stack.ps1

# Verbose — show what each layer is doing
.\Start-Stack.ps1 -Verbose
```

---

## `Stop-Stack.ps1`

Reverse-order graceful shutdown. Sends `WM_CLOSE` to each GUI process, waits
`-GraceSeconds` (default 15) for clean exit, then force-kills any holdouts.
Docker Desktop itself is left running (machine-level service).

**Parameters:**
- `-Down` — run `docker compose down` (removes containers) instead of `stop` (preserves them for fast restart)
- `-GraceSeconds <int>` — seconds to wait before force-kill
- `-RepoRoot <path>`

```powershell
# Stop containers, preserve for fast restart
.\Stop-Stack.ps1

# Remove containers (clean state)
.\Stop-Stack.ps1 -Down

# Give slow processes 30 s to exit before killing
.\Stop-Stack.ps1 -GraceSeconds 30 -Verbose
```

---

## `Test-StackHealth.ps1`

Four-layer health probe. Exits `0` if everything passes, `1` otherwise.
Safe to run repeatedly. Called by `Start-Stack.ps1` (Layer 3) but also useful
standalone for monitoring or post-restart sanity checks.

**Layers:**

1. `docker compose ps` — `claude-memory-db` and `claude-memory-mcp` are `running`/`healthy`
2. `GET /health` on the MCP/REST server (`status=ok` + `db=ok`)
3. `GET /api/tags` on Ollama — required model `qwen2.5:7b` is loaded
4. `GET /api/stats` round-trip — exercises the DB query path end-to-end

**Parameters:** `-ComposeFile`, `-ServerUrl` (default `http://localhost:3333`), `-OllamaUrl`, `-RequiredModel` (default `qwen2.5:7b`).

> `-OllamaUrl` resolution: explicit flag wins. Otherwise defaults to the in-stack ollama service via its published host port, `http://localhost:11737`. Pass a host/Msty URL explicitly to health-check that instead. See [FAQ.md](FAQ.md#which-ollama-endpoint-does-the-pipeline-use).

```powershell
# Quick health check
.\Test-StackHealth.ps1

# Override Ollama URL (e.g. checking against the official Ollama port)
.\Test-StackHealth.ps1 -OllamaUrl "http://localhost:11737"
```

---

## `Invoke-ImportPipeline.ps1`

Runs the full ingestion pipeline. Equivalent to the Linux `import-cron.sh`,
suitable for **Windows Task Scheduler**.

**Stages:**

1. **Import** new Claude Code sessions (`import_memories.py --claude-code`)
2. **Distill** sessions into durable memories (`distill_sessions.py`, LLM-powered)
3. **Extract** behavioral signals (`extract_signals.py`, no LLM)
4. **Behavioral pass** — type:behavior memories (`behavioral_pass.py`, LLM)
5. **User profile** generation (optional — only runs if `venv\Scripts\python.exe` is present)

Uses the `Invoke-Tee` helper to wrap each native call through `cmd /c "... 2>&1"`,
which prevents PowerShell 5.1 from misrendering stderr lines as `NativeCommandError`
records.

**Parameters:**
- `-OllamaUrl` — explicit URL wins. Otherwise defaults to the in-stack ollama service over the compose network, `http://ollama:11434/v1`. Pass a `host.docker.internal:<port>/v1` URL to target a host-side Ollama/Msty instead. See [FAQ.md](FAQ.md#which-ollama-endpoint-does-the-pipeline-use).
- `-Verbosity` — output level for the Python pipeline scripts. One of:
  - `Critical` — ERROR only. Quietest. Long-running steps appear silent between always-on step markers.
  - `Standard` — ERROR + WARNING. Medium. Hides per-session INFO chatter but keeps anomalies visible.
  - `Detailed` (default) — ERROR + WARNING + INFO. Full per-session heartbeat output.

  At all levels, pipeline-step start/end markers print with elapsed time (e.g. `Step 2 — Distill — complete (12m34s)`), so you always know which step is active and how long it has been running. Passed to Python scripts via the `LOGLEVEL` env var.
- `-ClaudeProjects` (default `%USERPROFILE%\.claude\projects`)
- `-LogFile` (default `%TEMP%\claude-memory-import.log` — UTF-8, no BOM)
- `-NoGpu` — force the base CPU-only stack even if an NVIDIA docker runtime is detected. By default the pipeline auto-detects the GPU and includes `docker-compose.gpu.yml`.

```powershell
# Run the pipeline manually (Detailed = full INFO output)
.\Invoke-ImportPipeline.ps1

# Quieter — only show warnings, errors, and step start/end markers
.\Invoke-ImportPipeline.ps1 -Verbosity Standard

# Quietest — only errors and step start/end markers
.\Invoke-ImportPipeline.ps1 -Verbosity Critical

# Explicit override (use a non-default Ollama backend / port for this run only)
.\Invoke-ImportPipeline.ps1 -OllamaUrl "http://host.docker.internal:10000/v1"
```

**Schedule via Task Scheduler** (replace `<path-to-your-clone>` with your actual repo path):

```powershell
schtasks /Create /TN "ClaudeMemory-Import" /TR "powershell.exe -NoProfile -File `"<path-to-your-clone>\Invoke-ImportPipeline.ps1`"" /SC DAILY /ST 03:00
```

---

## `Backup-Memory.ps1`

`pg_dump` (custom format, max compression) of the `memory` database. Writes the
dump inside the container first then copies it out, which avoids PowerShell 5.1
binary-pipe encoding corruption.

**Parameters:**
- `-OutputDir` (default `.\backups`)
- `-DbContainer` (default `claude-memory-db`)

```powershell
# Backup to default ./backups/ folder
.\Backup-Memory.ps1

# Backup to a network share
.\Backup-Memory.ps1 -OutputDir "\\nas\backups\claude-memory"
```

Output file pattern: `claude-memory-YYYY-MM-DDTHH-mm-ss.pgdump`

---

## `Restore-Memory.ps1`

⚠️ **DESTRUCTIVE** — drops and recreates the `memory` database from a dump file.
Prompts for `YES` confirmation before proceeding.

After restore completes, the MCP server needs to be restarted to reconnect to
the rebuilt database:

```powershell
docker compose restart mcp-server
```

**Parameters:**
- `-DumpFile` (**required**) — path to a `.pgdump` file
- `-DbContainer` (default `claude-memory-db`)

```powershell
# Restore from a backup created by Backup-Memory.ps1
.\Restore-Memory.ps1 -DumpFile "backups\claude-memory-2026-05-29T12-00-00.pgdump"
```

---

## Common gotchas

- **All scripts require PowerShell 5.1+**. PowerShell 7 also works but is not tested.
- **Run from any directory** — scripts use `$PSScriptRoot` to resolve relative paths.
- **`Start-Stack.ps1` Layer 5** distinguishes the Claude Desktop app from the Claude Code CLI by `MainWindowHandle -ne 0`. Both processes are named `claude.exe`.
- **`Invoke-ImportPipeline.ps1` Step 5** is silently skipped if no local `venv` exists. To enable, create `venv\` in the repo root with `python -m venv venv` and `pip install -r requirements.txt` (root, not `mcp-server/`).
- **`Restore-Memory.ps1` requires the DB container to be reachable.** It auto-starts `db` via `docker compose up -d db` if not running.
