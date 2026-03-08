#!/usr/bin/env python3
"""
Import past conversations and notes into claude-memory.

Usage:
  # Claude Code session history (all projects)
  python import_memories.py --claude-code

  # Specific project only
  python import_memories.py --claude-code --project workspace

  # Claude.ai export JSON
  python import_memories.py --claude-ai conversations.json

  # Plain text / markdown files
  python import_memories.py --text notes.md thoughts.txt

  # All sources at once
  python import_memories.py --claude-code --claude-ai conversations.json --text notes.md

  # Custom minimum message length (default: 50 chars)
  python import_memories.py --claude-code --min-length 100
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

print("Loading embedding model...")
embedder = SentenceTransformer("all-mpnet-base-v2")
print("Ready.\n")


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    register_vector(conn)
    return conn


def embed(text):
    return embedder.encode(text, normalize_embeddings=True)


def insert_memory(cur, content, tags, source, project="", created_at=None):
    vector = embed(content)
    if created_at:
        cur.execute(
            "INSERT INTO memories (content, tags, source, project, embedding, created_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (content, tags, source, project, vector, created_at),
        )
    else:
        cur.execute(
            "INSERT INTO memories (content, tags, source, project, embedding) VALUES (%s, %s, %s, %s, %s)",
            (content, tags, source, project, vector),
        )


# ── Claude Code sessions ──────────────────────────────────────────────────────

def extract_text(content):
    """Extract plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def is_session_already_processed(conn, session_id):
    """Return True if session is in imported_sessions with distilled=TRUE (skip entirely) or imported."""
    with conn.cursor() as cur:
        cur.execute("SELECT distilled FROM imported_sessions WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
    if row is None:
        return False  # never imported
    return row[0]  # True if distilled, False if imported but not yet distilled


def record_session(conn, session_id, project, message_count):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO imported_sessions (session_id, project, message_count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (session_id, project, message_count)
        )
    conn.commit()


def import_claude_code(project_filter=None, min_length=50):
    if not CLAUDE_PROJECTS_DIR.exists():
        print("~/.claude/projects not found, skipping.")
        return

    projects = sorted(CLAUDE_PROJECTS_DIR.iterdir())
    if project_filter:
        projects = [p for p in projects if project_filter.lower() in p.name.lower()]

    conn = get_db()
    total = 0

    for project_dir in projects:
        jsonl_files = list(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        home_encoded = str(Path.home()).replace("/", "-")  # e.g. "-Users-yourname"
        project_name = project_dir.name.replace(home_encoded, "").lstrip("-").replace("-", "/")
        project_short = project_name.split("/")[-1]
        print(f"  Project: {project_name} ({len(jsonl_files)} session(s))")

        for jsonl_path in jsonl_files:
            session_id = jsonl_path.stem

            if is_session_already_processed(conn, session_id):
                print(f"    Skipping {session_id[:8]} (already distilled)")
                continue

            messages = []

            with open(jsonl_path, encoding="utf-8", errors="ignore") as f:
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
                    if len(text) < min_length:
                        continue

                    timestamp_str = record.get("timestamp")
                    created_at = None
                    if timestamp_str:
                        try:
                            created_at = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        except ValueError:
                            pass

                    messages.append((role, text, created_at))

            if not messages:
                continue

            try:
                with conn.cursor() as cur:
                    for role, text, created_at in messages:
                        tags = ["claude-code-session", f"role:{role}", f"project:{project_short}"]
                        source = f"claude-code/{session_id[:8]}"
                        insert_memory(cur, text, tags, source, project_short, created_at)
                        total += 1
                conn.commit()
                record_session(conn, session_id, project_short, len(messages))
            except Exception as e:
                conn.rollback()
                print(f"    Error in {jsonl_path.name}: {e}")

    conn.close()
    print(f"\n  Imported {total} messages from Claude Code sessions.\n")


# ── Claude.ai export ──────────────────────────────────────────────────────────

def import_claude_ai(export_path, min_length=50):
    """
    Import from Claude.ai data export (Settings → Privacy → Export data).
    The export contains a conversations.json file.
    """
    path = Path(export_path)
    if not path.exists():
        print(f"File not found: {export_path}")
        return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        conversations = data.get("conversations", data.get("chats", [data]))
    else:
        conversations = data

    conn = get_db()
    total = 0

    for convo in conversations:
        convo_name = convo.get("name", convo.get("title", "untitled"))
        messages = convo.get("chat_messages", convo.get("messages", []))

        try:
            with conn.cursor() as cur:
                for msg in messages:
                    role = msg.get("sender", msg.get("role", ""))
                    content_raw = msg.get("content", msg.get("text", ""))
                    if isinstance(content_raw, list):
                        text = "\n".join(
                            b.get("text", "") for b in content_raw
                            if isinstance(b, dict) and b.get("type") == "text"
                        ).strip()
                    else:
                        text = str(content_raw).strip()

                    if len(text) < min_length:
                        continue

                    created_at = None
                    ts = msg.get("created_at", msg.get("timestamp"))
                    if ts:
                        try:
                            created_at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        except ValueError:
                            pass

                    tags = ["claude-ai", f"role:{role}", f"convo:{convo_name[:40]}"]
                    insert_memory(cur, text, tags, f"claude.ai/{convo_name[:30]}", "", created_at)
                    total += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  Error in conversation '{convo_name}': {e}")

    conn.close()
    print(f"Imported {total} messages from Claude.ai export.\n")


# ── Plain text / markdown ─────────────────────────────────────────────────────

def import_text_files(paths, chunk_size=1500, overlap=200):
    """Split text files into overlapping chunks and store each as a memory."""
    conn = get_db()
    total = 0

    for file_path in paths:
        path = Path(file_path)
        if not path.exists():
            print(f"  File not found: {file_path}")
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        ext = path.suffix.lstrip(".")
        tags = ["text-import", f"file:{path.name}", f"type:{ext or 'txt'}"]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end].strip())
            start += chunk_size - overlap

        try:
            with conn.cursor() as cur:
                for i, chunk in enumerate(chunks):
                    if len(chunk) < 50:
                        continue
                    insert_memory(cur, chunk, tags, f"file:{path.name}", "", None)
                    total += 1
            conn.commit()
            print(f"  {path.name}: {len(chunks)} chunk(s)")
        except Exception as e:
            conn.rollback()
            print(f"  Error in {file_path}: {e}")

    conn.close()
    print(f"\nImported {total} chunks from text files.\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import memories into claude-memory DB")
    parser.add_argument("--claude-code", action="store_true", help="Import ~/.claude/projects session history")
    parser.add_argument("--project", help="Filter to a specific project name (with --claude-code)")
    parser.add_argument("--claude-ai", metavar="FILE", help="Path to Claude.ai conversations.json export")
    parser.add_argument("--text", metavar="FILE", nargs="+", help="Plain text or markdown files to import")
    parser.add_argument("--min-length", type=int, default=50, help="Minimum message length to import (default: 50)")
    args = parser.parse_args()

    if not any([args.claude_code, args.claude_ai, args.text]):
        parser.print_help()
        sys.exit(1)

    if args.claude_code:
        print("=== Importing Claude Code sessions ===")
        import_claude_code(project_filter=args.project, min_length=args.min_length)

    if args.claude_ai:
        print("=== Importing Claude.ai export ===")
        import_claude_ai(args.claude_ai, min_length=args.min_length)

    if args.text:
        print("=== Importing text files ===")
        import_text_files(args.text)

    print("Done.")


if __name__ == "__main__":
    main()
