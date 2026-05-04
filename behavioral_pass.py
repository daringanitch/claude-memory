#!/usr/bin/env python3
"""
Behavioral re-pass: run targeted behavioral extraction over distilled sessions,
reading transcripts from the original JSONL files (raw messages are deleted after distillation).

Usage:
  python behavioral_pass.py               # all distilled sessions
  python behavioral_pass.py --dry-run     # preview without writing
  python behavioral_pass.py --project workspace
  python behavioral_pass.py --force       # re-run even if behavioral memories exist
"""
import argparse
import json
import logging
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from openai import OpenAI
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("behavioral_pass")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
MODEL        = os.environ.get("DISTILL_MODEL", "qwen2.5:7b")
MAX_CHARS    = 20_000
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

BEHAVIORAL_PROMPT = """\
Analyze this Claude Code session. Return behavioral observations about HOW the user works.

Return ONLY a JSON array — no other text. Each element:
{{"content": "The user... [2-3 sentences, cite specific evidence]", "tags": ["type:behavior", "workflow"]}}

Look for: workflow habits, tooling instincts, communication style (terse vs detailed), \
decision-making speed, quality habits (tests, docs, diffs), correction patterns.

If no patterns are observable, return: []

Project: {project}

Transcript:
{transcript}"""


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    register_vector(conn)
    return conn


def extract_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def find_jsonl(session_id):
    """Locate the JSONL file for a given session_id across all project directories."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    for proj_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        candidate = proj_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def build_transcript_from_jsonl(path, min_length=30):
    messages = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") not in ("user", "assistant"):
                continue
            msg = record.get("message", {})
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = extract_text(msg.get("content", ""))
            if len(text) >= min_length:
                messages.append(f"[{role.upper()}]\n{text}")
    transcript = "\n\n---\n\n".join(messages)
    if len(transcript) > MAX_CHARS:
        transcript = transcript[:MAX_CHARS] + "\n\n[truncated]"
    return transcript


def already_has_behavioral(conn, session_prefix):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM memories WHERE source = %s LIMIT 1",
            (f"behavioral/{session_prefix}",)
        )
        return cur.fetchone() is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project", default=None)
    parser.add_argument("--force", action="store_true", help="Re-run even if behavioral memories already exist")
    args = parser.parse_args()

    log.info("Loading embedding model...")
    embedder = SentenceTransformer("all-mpnet-base-v2")
    client   = OpenAI(base_url=OLLAMA_URL, api_key="ollama")

    conn = get_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if args.project:
            cur.execute(
                "SELECT session_id, project FROM imported_sessions WHERE distilled = TRUE AND project ILIKE %s ORDER BY imported_at",
                (f"%{args.project}%",)
            )
        else:
            cur.execute(
                "SELECT session_id, project FROM imported_sessions WHERE distilled = TRUE ORDER BY imported_at"
            )
        sessions = cur.fetchall()

    log.info("Found %d distilled sessions", len(sessions))
    total_written = 0

    for session in sessions:
        session_id = session["session_id"]
        project    = session["project"] or "unknown"
        prefix     = session_id[:8]

        if not args.force and already_has_behavioral(conn, prefix):
            log.info("  [%s] already processed — skip (--force to redo)", prefix)
            continue

        jsonl_path = find_jsonl(session_id)
        if jsonl_path is None:
            log.info("  [%s] JSONL not found on disk — skipping", prefix)
            continue

        transcript = build_transcript_from_jsonl(jsonl_path)
        if not transcript.strip():
            log.info("  [%s] empty transcript — skipping", prefix)
            continue

        log.info("  [%s] %s — %d chars, calling %s...", prefix, project, len(transcript), MODEL)

        prompt = BEHAVIORAL_PROMPT.format(project=project, session_id=prefix, transcript=transcript)
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
            raw_text = response.choices[0].message.content.strip()
            start, end = raw_text.find("["), raw_text.rfind("]")
            if start == -1 or end == -1:
                log.info("  [%s] no JSON array in response — skipping", prefix)
                continue
            memories = json.loads(raw_text[start:end + 1])
        except Exception as e:
            log.error("  [%s] error: %s", prefix, e)
            continue

        log.info("  [%s] → %d behavioral observations", prefix, len(memories))

        if args.dry_run:
            for m in memories:
                log.info("    • [%s] %s", m.get("tags", []), m.get("content", "")[:120])
            continue

        if not memories:
            continue

        valid = [(m["content"].strip(), m.get("tags", [])) for m in memories if m.get("content", "").strip()]
        if not valid:
            continue

        contents, all_tags = zip(*valid)
        vectors = embedder.encode(list(contents), normalize_embeddings=True, batch_size=32)

        with conn.cursor() as cur:
            for content, tags, vector in zip(contents, all_tags, vectors):
                final_tags = ["distilled", "type:behavior", f"project:{project}"]
                for t in tags:
                    if t not in final_tags:
                        final_tags.append(t)
                cur.execute(
                    "INSERT INTO memories (content, tags, source, project, embedding) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (content_hash) DO NOTHING",
                    (content, final_tags, f"behavioral/{prefix}", project, vector)
                )
        conn.commit()
        total_written += len(valid)
        log.info("  [%s] wrote %d behavioral memories", prefix, len(valid))

    conn.close()
    log.info("Done — %d total behavioral memories written", total_written)


if __name__ == "__main__":
    main()
