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
        elif tag.lower() in TECH_TAGS and tag.capitalize() not in stack:
            stack.append(tag.capitalize() if tag.islower() else tag)
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
