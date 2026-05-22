#!/usr/bin/env python3
"""
Generate ~/.claude/user.md from distilled memories in PostgreSQL.

Usage:
  python generate_user_profile.py             # write to ~/.claude/user.md
  python generate_user_profile.py --dry-run   # print to stdout
  python generate_user_profile.py --output /custom/path.md
"""
import argparse
import logging
import os
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("generate_user_profile")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
OUTPUT_PATH  = Path.home() / ".claude" / "user.md"
CLAUDE_MD_PATH = Path.home() / ".claude" / "CLAUDE.md"

TECH_TAGS = {
    "python", "react", "typescript", "javascript", "docker", "flask",
    "fastapi", "k8s", "kubernetes", "terraform", "golang", "go",
    "postgresql", "postgres", "redis", "neo4j", "nodejs", "brew",
    "bash", "rust", "java", "nginx", "aws", "gcp", "azure",
}

CLAUDE_MD_MARKER  = "## User Profile"
CLAUDE_MD_SECTION = (
    "## User Profile\n"
    "See `~/.claude/user.md` for your generated profile — preferences, working style, "
    "active projects, and tooling. Regenerated automatically every 30 minutes by claude-memory.\n\n"
)


def query_identity(conn):
    """Return (top_projects, stack_tags) counted from all active memory tags."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT unnest(tags) AS tag, COUNT(*) AS cnt "
            "FROM memories WHERE deleted_at IS NULL "
            "GROUP BY tag ORDER BY cnt DESC"
        )
        rows = cur.fetchall()
    projects, stack = [], []
    for row in rows:
        tag = row["tag"]
        if tag.startswith("project:"):
            name = tag[len("project:"):]
            if name not in projects:
                projects.append(name)
        elif tag.lower() in TECH_TAGS:
            normalized = tag.capitalize() if tag.islower() else tag
            if normalized not in stack:
                stack.append(normalized)
    return projects[:6], stack[:8]


def build_identity_section(projects, stack):
    """Return markdown ## Identity section, or None if no data."""
    lines = []
    if projects:
        lines.append(f"- **Active projects:** {', '.join(projects)}")
    if stack:
        lines.append(f"- **Stack:** {', '.join(stack)}")
    if not lines:
        return None
    return "## Identity\n" + "\n".join(lines)


def _first_substantive_line(content):
    """Extract the first non-title, non-bold line from a memory (handles auto-memory format).

    Auto-memory files have a short title paragraph followed by the actual preference.
    Skip single-paragraph lines that look like titles (no sentence-ending punctuation,
    no code markers, and shorter than 35 chars).
    """
    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    # If there are multiple paragraphs, the first may be a title — skip it
    # when it looks like a heading (short, no backticks, no sentence punctuation)
    start = 0
    if len(paras) > 1:
        candidate = paras[0].split("\n")[0].strip()
        is_title = (
            not candidate.startswith("**")
            and not candidate.startswith("#")
            and "`" not in candidate
            and not candidate.endswith((".", "!", "?", ";", ":"))
            and len(candidate) < 40
        )
        if is_title:
            start = 1
    for para in paras[start:]:
        if para.startswith("**") or para.startswith("#"):
            continue
        first_line = para.split("\n")[0].strip()
        if len(first_line) > 10:
            return first_line
    return content.split("\n")[0].strip()


def query_preferences(conn):
    """Return content strings for type:preference memories, auto-memory rows first."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT content FROM memories "
            "WHERE 'type:preference' = ANY(tags) AND deleted_at IS NULL "
            "ORDER BY ('source:auto-memory' = ANY(tags)) DESC, created_at DESC "
            "LIMIT 10"
        )
        return [r["content"] for r in cur.fetchall()]


def build_preferences_section(contents):
    """Return markdown ## Preferences section, or None if empty."""
    items = [_first_substantive_line(c) for c in contents if c.strip()]
    items = [i[:160] for i in items if i]
    if not items:
        return None
    return "## Preferences\n" + "\n".join(f"- {item}" for item in items)


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def main():
    parser = argparse.ArgumentParser(description="Generate ~/.claude/user.md from distilled memories")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of writing file")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Output path (default: {OUTPUT_PATH})")
    args = parser.parse_args()
    log.info("generate_user_profile starting (dry_run=%s)", args.dry_run)


if __name__ == "__main__":
    main()
