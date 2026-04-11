#!/usr/bin/env python3
"""
Distill raw Claude Code session messages into durable knowledge memories.
Uses a local Ollama LLM — no API key required.

Usage:
  python distill_sessions.py                      # distill all pending sessions
  python distill_sessions.py --project osint      # filter by project
  python distill_sessions.py --dry-run            # preview without writing
  python distill_sessions.py --workers 4          # parallel sessions (default: 4)
  python distill_sessions.py --model llama3.2:3b  # model override
"""

import argparse
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import psycopg2.extras
from openai import OpenAI
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("distill")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
DEFAULT_MODEL = os.environ.get("DISTILL_MODEL", "qwen2.5:7b")
DEFAULT_WORKERS = int(os.environ.get("DISTILL_WORKERS", "4"))
MAX_TRANSCRIPT_CHARS = 80_000  # ~20k tokens
DISTILL_FAILURE_CAP = 3       # sessions that fail this many times are permanently skipped

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

_embed_lock = threading.Lock()


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    register_vector(conn)
    return conn


def embed_batch(texts, embedder):
    """Batch-embed a list of texts. Thread-safe via lock."""
    with _embed_lock:
        return embedder.encode(texts, normalize_embeddings=True, batch_size=64)


def get_pending_sessions(conn, project_filter=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if project_filter:
            cur.execute(
                "SELECT session_id, project, message_count, distill_failures FROM imported_sessions "
                "WHERE distilled = FALSE AND distill_failures < %s AND project ILIKE %s ORDER BY imported_at",
                (DISTILL_FAILURE_CAP, f"%{project_filter}%",)
            )
        else:
            cur.execute(
                "SELECT session_id, project, message_count, distill_failures FROM imported_sessions "
                "WHERE distilled = FALSE AND distill_failures < %s ORDER BY imported_at",
                (DISTILL_FAILURE_CAP,)
            )
        return cur.fetchall()


def get_raw_messages(conn, session_id_prefix):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, content FROM memories WHERE source = %s ORDER BY created_at",
            (f"claude-code/{session_id_prefix}",)
        )
        return cur.fetchall()


def build_transcript(messages):
    parts = [msg["content"].strip() for msg in messages if msg["content"].strip()]
    transcript = "\n\n---\n\n".join(parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[transcript truncated]"
    return transcript


def call_ollama(client, model, project, session_id, transcript):
    prompt = DISTILL_PROMPT.format(project=project, session_id=session_id, transcript=transcript)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    return response.choices[0].message.content


def parse_distilled(response_text):
    text = response_text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(text[start:end + 1])


def _increment_failures(conn, session_id, session_prefix):
    """Increment distill_failures counter. Warns when the cap is reached."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE imported_sessions SET distill_failures = distill_failures + 1 "
                "WHERE session_id = %s RETURNING distill_failures",
                (session_id,)
            )
            row = cur.fetchone()
        conn.commit()
        if row and row[0] >= DISTILL_FAILURE_CAP:
            log.warning(
                "  [%s] Failure cap reached (%d/%d) — session will be permanently skipped",
                session_prefix, row[0], DISTILL_FAILURE_CAP,
            )
    except Exception as e:
        log.error("  [%s] Failed to increment distill_failures: %s", session_prefix, e)
        conn.rollback()


def distill_session(embedder, client, model, session, dry_run=False):
    """Process one session. Opens its own DB connection for thread safety."""
    session_id = session["session_id"]
    project = session["project"] or "unknown"
    session_prefix = session_id[:8]

    conn = get_db()
    try:
        raw_messages = get_raw_messages(conn, session_prefix)
        if not raw_messages:
            log.info("  [%s] No raw messages — marking distilled", session_prefix)
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s",
                        (session_id,)
                    )
                conn.commit()
            return 0

        transcript = build_transcript(raw_messages)
        log.info("  [%s] Calling %s (%d chars, %d messages)...",
                 session_prefix, model, len(transcript), len(raw_messages))

        try:
            response = call_ollama(client, model, project, session_prefix, transcript)
            memories = parse_distilled(response)
        except json.JSONDecodeError as e:
            log.error("  [%s] JSON parse error: %s — keeping raws", session_prefix, e)
            _increment_failures(conn, session_id, session_prefix)
            return 0
        except Exception as e:
            log.error("  [%s] LLM error: %s — keeping raws", session_prefix, e)
            _increment_failures(conn, session_id, session_prefix)
            return 0

        log.info("  [%s] → %d memories extracted", session_prefix, len(memories))

        if dry_run:
            for i, m in enumerate(memories, 1):
                log.info("    [%d] %s | tags: %s", i, m["content"][:100], m.get("tags", []))
            return len(memories)

        if not memories:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memories WHERE source = %s", (f"claude-code/{session_prefix}",))
                cur.execute(
                    "UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s",
                    (session_id,)
                )
            conn.commit()
            return 0

        # Batch embed all contents at once — much faster than one at a time
        valid = [(m["content"].strip(), m.get("tags", [])) for m in memories if m.get("content", "").strip()]
        if not valid:
            return 0

        contents, all_tags = zip(*valid)
        vectors = embed_batch(list(contents), embedder)

        rows = []
        for content, item_tags, vector in zip(contents, all_tags, vectors):
            tags = ["distilled", f"project:{project}"] + [t for t in item_tags if t != "distilled"]
            rows.append((content, tags, f"distilled/{session_prefix}", project, vector))

        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO memories (content, tags, source, project, embedding) VALUES %s "
                    "ON CONFLICT (content_hash) DO NOTHING",
                    rows,
                )
                cur.execute("DELETE FROM memories WHERE source = %s", (f"claude-code/{session_prefix}",))
                cur.execute(
                    "UPDATE imported_sessions SET distilled = TRUE WHERE session_id = %s",
                    (session_id,)
                )
            conn.commit()
            log.info("  [%s] Done: %d memories stored", session_prefix, len(rows))
            return len(rows)
        except psycopg2.Error as e:
            conn.rollback()
            log.error("  [%s] DB error: %s — keeping raws", session_prefix, e)
            return 0
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Distill Claude Code sessions into curated memories via local LLM")
    parser.add_argument("--project", help="Filter to sessions from this project")
    parser.add_argument("--dry-run", action="store_true", help="Preview extractions without writing to DB")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel sessions (default: {DEFAULT_WORKERS})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--ollama-url", default=OLLAMA_URL,
                        help=f"Ollama base URL (default: {OLLAMA_URL})")
    args = parser.parse_args()

    log.info("Loading embedding model...")
    embedder = SentenceTransformer("all-mpnet-base-v2")

    client = OpenAI(base_url=args.ollama_url, api_key="ollama")

    conn = get_db()
    sessions = get_pending_sessions(conn, args.project)
    conn.close()

    if not sessions:
        log.info("No pending sessions to distill.")
        return

    mode = "[DRY RUN] " if args.dry_run else ""
    log.info("%s=== Distilling %d session(s) | workers=%d | model=%s ===",
             mode, len(sessions), args.workers, args.model)

    total = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(distill_session, embedder, client, args.model, s, args.dry_run): s
            for s in sessions
        }
        for future in as_completed(futures):
            s = futures[future]
            try:
                total += future.result()
            except Exception as e:
                log.error("Session %s failed: %s", s["session_id"][:8], e)

    log.info("%sDone. %d distilled memories %sstored.",
             mode, total, "would be " if args.dry_run else "")


if __name__ == "__main__":
    main()
