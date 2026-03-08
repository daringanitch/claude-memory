# Claude Memory — Explainer Video Script

**Format:** Screen recording + voiceover
**Target length:** 3–4 minutes
**Tone:** Direct, developer-focused

---

## SCENE 1 — The Problem (0:00–0:40)

**[VISUAL: Split screen. Left: a developer mid-conversation with Claude, deep in a complex debugging session. Right: a new Claude Code session, blank slate.]**

**VOICEOVER:**

You're deep in a project. You've spent hours with Claude working through architecture decisions, debugging a tricky issue, setting up your environment just right.

Then you close the terminal.

Next session — Claude has no idea any of that happened.

Every session starts from zero. You re-explain your stack. You re-explain your preferences. You re-explain the bug you spent two hours fixing yesterday.

**[VISUAL: User typing "remember when we fixed the auth bug last week?" — Claude responds "I don't have access to previous conversations."]**

This isn't a Claude problem. It's a missing infrastructure problem. And it's completely solvable.

---

## SCENE 2 — Introducing claude-memory (0:40–1:10)

**[VISUAL: Terminal. `git clone`, then `bash quickstart.sh`. Two containers spin up. Import runs. "✅ claude-memory is ready."]**

**VOICEOVER:**

claude-memory is a persistent vector memory system that runs locally alongside Claude Code.

One script sets up everything: a PostgreSQL database with vector search, a lightweight server that exposes your memory as tools Claude can call directly, and your entire Claude Code history imported and indexed — automatically.

**[VISUAL: Diagram — Claude Code ↔ MCP Server ↔ PostgreSQL + pgvector]**

Once it's running, Claude can save memories, search them by meaning, and recall exactly what you worked on — across every project, every session, going back as far as you want. Eleven tools total, all available the moment you open a terminal.

---

## SCENE 3 — How It Works (1:10–2:00)

**[VISUAL: Claude Code session. User opens a new session in a project.]**

**VOICEOVER:**

Here's what a session looks like once claude-memory is set up.

**[VISUAL: Claude automatically calls `semantic_search` with the project name at session start. Results populate.]**

When a new session starts, Claude searches memory for context about the current project and surfaces what's relevant — decisions made, bugs fixed, preferences set — before you've typed a single word.

**[VISUAL: Claude summarizing: "Last time we worked on this project, we fixed a race condition in the auth flow and decided to use JWT with 24-hour expiry..."]**

During the session, Claude saves important things automatically — architectural decisions, root causes of bugs, anything worth remembering next time.

**[VISUAL: `save_memory` tool call with tags: `["project:my-app", "type:decision"]`]**

And it's not just keyword matching. Memories are stored as vector embeddings — so searching for "authentication issues" finds memories about "JWT expiry bugs" and "OAuth redirect problems" even if those exact words don't appear.

**[VISUAL: `semantic_search` returning semantically similar results. `export_memories` outputting a markdown file.]**

You can also filter by date — "show me everything from the last 30 days" — or export your entire memory as JSON or markdown.

---

## SCENE 4 — Importing Your History (2:00–2:30)

**[VISUAL: quickstart.sh running, import output scrolling past.]**

**VOICEOVER:**

You don't start from zero. The quickstart script imports your entire existing Claude Code session history in one pass.

**[VISUAL: Output: "Imported 487 messages from Claude Code sessions."]**

Every conversation you've had with Claude Code gets embedded and indexed. Hundreds of sessions, instantly searchable.

**[VISUAL: `semantic_search` query: "how did we set up the database?" — results show the exact conversation from three weeks ago.]**

Then distillation runs automatically: Claude Haiku reads each raw session, extracts the durable facts — decisions, patterns, bug root causes — and replaces the verbose transcript with clean, concise memories.

**[VISUAL: "195 distilled memories stored."]**

You can also import Claude.ai conversations, or any markdown and text files — notes, docs, architecture writeups — anything you want Claude to be able to recall.

---

## SCENE 5 — Auto-Import, Zero Maintenance (2:30–3:00)

**[VISUAL: macOS LaunchAgent installed. Log tail showing clean hourly run.]**

**VOICEOVER:**

Once set up, the whole thing runs itself.

A background agent checks for new Claude Code sessions every hour and imports them automatically. You never think about it. Every session you have today will be searchable tomorrow.

**[VISUAL: `tail -f /tmp/claude-memory-import.log` — timestamps, import counts, distillation complete.]**

All data stays local. Nothing leaves your machine. Back it up any time with a single command:

**[VISUAL: `bash backup.sh` — "Backup complete: backups/claude-memory-2026-03-08T12-00-00.pgdump (2.1MB)"]**

---

## SCENE 6 — Setup (3:00–3:30)

**[VISUAL: Terminal, clean and fast.]**

**VOICEOVER:**

Setup takes about two minutes.

**[VISUAL: Commands appearing one by one:]**

```
git clone https://github.com/daringanitch/claude-memory
cd claude-memory
bash quickstart.sh
```

That's it. The script handles Docker, imports your history, registers the MCP server with Claude Code at user scope — so it works in every project directory — and optionally installs the hourly background agent.

**[VISUAL: New Claude Code session in any directory. Claude immediately recalls prior context.]**

Every future session starts with Claude knowing exactly where you left off.

---

## SCENE 7 — Close (3:30–3:50)

**[VISUAL: Side-by-side. Before: user re-explaining context to Claude. After: Claude opening with a summary of prior work.]**

**VOICEOVER:**

The gap between a powerful AI assistant and a truly useful one is memory. Context that persists. History that's searchable. A system that learns your projects over time.

claude-memory fills that gap.

**[VISUAL: GitHub repo — github.com/daringanitch/claude-memory]**

It's open source, runs entirely locally, and takes two minutes to set up.

Link in the description.

---

## Production Notes

- **Screen resolution:** Record at 1920×1080, terminal font size 18+
- **Terminal theme:** Use a high-contrast theme (dark background) for readability
- **Pacing:** Pause 1–2 seconds after each command runs before continuing voiceover
- **Captions:** Add subtitles — most viewers watch without audio
- **B-roll suggestions:** pgvector docs page, sentence-transformers model card, the GitHub repo, `claude mcp get claude-memory` output showing "✓ Connected"
