#!/usr/bin/env python3
"""
Distill raw Claude Code session messages into durable knowledge memories.

Reads sessions from imported_sessions where distilled=FALSE, sends transcripts
to Claude haiku, extracts key facts/decisions/patterns, stores as clean memories,
then deletes the raw messages and marks the session as distilled.

Usage:
  python distill_sessions.py               # distill all pending sessions
  python distill_sessions.py --project osint  # filter by project
  python distill_sessions.py --dry-run     # preview without writing
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import anthropic
import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("distill")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
MAX_TRANSCRIPT_CHARS = 80_000  # ~20k tokens, safe for haiku context

DISTILL_PROMPT = """\
You are extracting durable knowledge from a Claude Code session transcript.

Your job: identify every reusable fact, decision, preference, bug fix, discovered pattern, \
or architectural insight from this session. Ignore greetings, navigation commands, \
file listings, and ephemeral details (e.g. "let me check X").

Return ONLY a JSON array. Each element must have:
- "content": a self-contained, full-sentence memory (no pronouns without antecedents)
- "tags": list of 2-5 lowercase keyword tags

Example output:
[
  {{"content": "All Python package installation on this Mac uses brew, not pip install directly.", "tags": ["preference", "python", "brew", "macos"]}},
  {{"content": "FastMCP.run() does not accept host/port kwargs — pass them to the FastMCP() constructor instead.", "tags": ["bug", "fastmcp", "pattern"]}}
]

If nothing durable was learned, return an empty array: []

Project: {project}
Session ID: {session_id}

Transcript:
{transcript}"""


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    register_vector(conn)
    return conn


def embed(text, embedder):
    return embedder.encode(text, normalize_embeddings=True)


def get_pending_sessions(conn, project_filter=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if project_filter:
            cur.execute(
                "SELECT session_id, project, message_count FROM imported_sessions "
                "WHERE distilled = FALSE AND project ILIKE %s ORDER BY imported_at",
                (f"%{project_filter}%",)
            )
        else:
            cur.execute(
                "SELECT session_id, project, message_count FROM imported_sessions "
                "WHERE distilled = FALSE ORDER BY imported_at"
            )
        return cur.fetchall()


def get_raw_messages(conn, session_id_prefix):
    """Fetch all raw memories for a session by source prefix."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, content FROM memories WHERE source = %s ORDER BY created_at",
            (f"claude-code/{session_id_prefix}",)
        )
        return cur.fetchall()


def build_transcript(messages):
    parts = []
    for msg in messages:
        content = msg["content"].strip()
        if content:
            parts.append(content)
    transcript = "\n\n---\n\n".join(parts)
    # Truncate if too long
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[transcript truncated]"
    return transcript


def call_claude(client, project, session_id, transcript):
    prompt = DISTILL_PROMPT.format(
        project=project,
        session_id=session_id,
        transcript=transcript
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def parse_distilled(response_text):
    """Extract JSON array from Claude response."""
    text = response_text.strip()
    # Find JSON array bounds
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(text[start:end + 1])


def distill_session(conn, embedder, client, session, dry_run=False):
    session_id = session["session_id"]
    project = session["project"] or "unknown"
    session_prefix = session_id[:8]

    raw_messages = get_raw_messages(conn, session_prefix)
    if not raw_messages:
        log.info("  No raw messages for %s — marking distilled", session_prefix)
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute("UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s", (session_id,))
            conn.commit()
        return 0

    transcript = build_transcript(raw_messages)
    log.info("  Calling Claude haiku (%d chars, %d messages)...", len(transcript), len(raw_messages))

    try:
        response = call_claude(client, project, session_prefix, transcript)
        memories = parse_distilled(response)
    except json.JSONDecodeError as e:
        log.error("  JSON parse error for %s: %s — keeping raws", session_prefix, e)
        return 0
    except anthropic.APIError as e:
        log.error("  Anthropic API error for %s: %s — keeping raws", session_prefix, e)
        return 0
    except Exception as e:
        log.error("  Unexpected error for %s: %s — keeping raws", session_prefix, e)
        return 0

    log.info("  → %d distilled memories extracted", len(memories))

    if dry_run:
        for i, m in enumerate(memories, 1):
            log.info("    [%d] %s", i, m['content'][:100])
            log.info("        tags: %s", m.get('tags', []))
        return len(memories)

    if not memories:
        # Nothing learned — still delete raws and mark done
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memories WHERE source = %s", (f"claude-code/{session_prefix}",))
            cur.execute("UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s", (session_id,))
        conn.commit()
        return 0

    try:
        with conn.cursor() as cur:
            for item in memories:
                content = item.get("content", "").strip()
                if not content:
                    continue
                item_tags = item.get("tags", [])
                tags = ["distilled", f"project:{project}"] + [t for t in item_tags if t not in ("distilled",)]
                vector = embed(content, embedder)
                cur.execute(
                    "INSERT INTO memories (content, tags, source, project, embedding) VALUES (%s, %s, %s, %s, %s)",
                    (content, tags, f"distilled/{session_prefix}", project, vector)
                )
            # Delete raw messages for this session
            cur.execute("DELETE FROM memories WHERE source = %s", (f"claude-code/{session_prefix}",))
            # Mark distilled
            cur.execute("UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s", (session_id,))
        conn.commit()
        log.info("  Session %s distilled: %d memories stored", session_prefix, len(memories))
        return len(memories)
    except psycopg2.Error as e:
        conn.rollback()
        log.error("  DB error for %s: %s — keeping raws", session_prefix, e)
        return 0
    except Exception as e:
        conn.rollback()
        log.error("  Unexpected DB error for %s: %s — keeping raws", session_prefix, e)
        return 0


def main():
    parser = argparse.ArgumentParser(description="Distill Claude Code sessions into curated memories")
    parser.add_argument("--project", help="Filter to sessions from this project")
    parser.add_argument("--dry-run", action="store_true", help="Preview extractions without writing to DB")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set. Export it or add it to ~/.claude/.env")
        sys.exit(1)

    log.info("Loading embedding model...")
    embedder = SentenceTransformer("all-mpnet-base-v2")
    log.info("Ready.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    conn = get_db()

    sessions = get_pending_sessions(conn, args.project)
    if not sessions:
        log.info("No pending sessions to distill.")
        conn.close()
        return

    mode = "[DRY RUN] " if args.dry_run else ""
    log.info("%s=== Distilling %d session(s) ===", mode, len(sessions))

    total_distilled = 0
    for session in sessions:
        project = session["project"] or "unknown"
        session_prefix = session["session_id"][:8]
        log.info("Session %s (project: %s, %d messages)", session_prefix, project, session['message_count'])
        count = distill_session(conn, embedder, client, session, dry_run=args.dry_run)
        total_distilled += count

    conn.close()
    log.info("%sDone. %d distilled memories %sstored.", mode, total_distilled, "would be " if args.dry_run else "")


if __name__ == "__main__":
    main()
