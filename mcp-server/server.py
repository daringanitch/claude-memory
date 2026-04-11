import os, json, logging
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2, psycopg2.extras, psycopg2.pool
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("claude-memory")

mcp = FastMCP("claude-memory", host="0.0.0.0", port=3333)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
log.info("Loading embedding model...")
embedder = SentenceTransformer("all-mpnet-base-v2")
log.info("Connecting to database...")
_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, DATABASE_URL)
log.info("Ready.")


@contextmanager
def db_conn():
    conn = _pool.getconn()
    try:
        # Rollback any leftover transaction from previous pool use,
        # then register the vector type (runs a SELECT).
        # autocommit=False is the psycopg2 default — don't set it explicitly
        # while a transaction may already be open (causes set_session error).
        conn.rollback()
        register_vector(conn)
        yield conn
    finally:
        conn.rollback()  # ensure clean state before returning to pool
        _pool.putconn(conn)


def embed(text):
    return embedder.encode(text, normalize_embeddings=True)


def _parse_dt(value: str, name: str):
    """Parse an ISO date/datetime string. Returns (datetime, None) or (None, error_str)."""
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value), None
    except ValueError:
        return None, f"❌ Invalid {name} date '{value}'. Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS"


# ── Health check endpoint ──────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Liveness/readiness probe. Returns 200 OK when healthy, 503 when DB is unreachable."""
    db_ok = False
    db_error = None
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        db_ok = True
    except Exception as e:
        db_error = str(e)

    payload = {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "db_error": db_error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def save_memory(content: str, tags: list[str] = [], source: str = "claude-code", project: str = "") -> str:
    """Save a thought, request, note, or piece of information to persistent memory."""
    vector = embed(content)
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Semantic dedup check — only against active (non-deleted) memories
                cur.execute(
                    "SELECT id, content, ROUND((1 - (embedding <=> %s))::numeric, 4) AS sim "
                    "FROM memories WHERE (1 - (embedding <=> %s)) >= 0.92 AND deleted_at IS NULL "
                    "ORDER BY embedding <=> %s LIMIT 1",
                    (vector, vector, vector)
                )
                dup = cur.fetchone()
                if dup:
                    return f"Duplicate of memory ID {dup['id']} (similarity {dup['sim']}): {dup['content'][:80]}..."
                # ON CONFLICT on content_hash handles exact-duplicate races atomically.
                # If the conflicting row was soft-deleted, un-delete it (restore).
                # If it's an active row the DO UPDATE WHERE is false → RETURNING returns nothing.
                cur.execute(
                    "INSERT INTO memories (content, tags, source, project, embedding) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (content_hash) DO UPDATE "
                    "  SET deleted_at = NULL, updated_at = NOW() "
                    "  WHERE memories.deleted_at IS NOT NULL "
                    "RETURNING id, created_at, deleted_at",
                    (content, tags, source, project, vector)
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            return "Duplicate (exact match already stored)."
        log.info("Memory saved id=%s project=%s", row['id'], project or "(none)")
        return f"✅ Memory saved (ID: {row['id']}, created: {row['created_at']})"
    except Exception as e:
        log.error("save_memory failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def semantic_search(query: str, limit: int = 10, min_similarity: float = 0.3,
                    project: str = None, since: str = None, before: str = None) -> str:
    """Search memories by MEANING using vector similarity. Filter by project, since, or before (ISO dates)."""
    since_dt, err = _parse_dt(since, "since")
    if err:
        return err
    before_dt, err = _parse_dt(before, "before")
    if err:
        return err

    vector = embed(query)
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Build WHERE dynamically; first two placeholders belong to the SELECT and WHERE cosine checks
                conditions = ["embedding IS NOT NULL", "deleted_at IS NULL", "(1 - (embedding <=> %s)) >= %s"]
                cond_params = [vector, min_similarity]
                if project:
                    conditions.append("project = %s")
                    cond_params.append(project)
                if since_dt:
                    conditions.append("created_at >= %s")
                    cond_params.append(since_dt)
                if before_dt:
                    conditions.append("created_at < %s")
                    cond_params.append(before_dt)

                sql = (
                    "SELECT id, content, tags, source, project, created_at, "
                    "ROUND((1 - (embedding <=> %s))::numeric, 4) AS similarity "
                    f"FROM memories WHERE {' AND '.join(conditions)} "
                    "ORDER BY embedding <=> %s LIMIT %s"
                )
                params = [vector] + cond_params + [vector, limit]
                cur.execute(sql, params)
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No similar memories found for: '{query}'"
    except Exception as e:
        log.error("semantic_search failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def search_memories(query: str, limit: int = 10, project: str = None,
                    since: str = None, before: str = None) -> str:
    """Search memories by exact keyword or phrase. Filter by project, since, or before (ISO dates)."""
    since_dt, err = _parse_dt(since, "since")
    if err:
        return err
    before_dt, err = _parse_dt(before, "before")
    if err:
        return err

    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = [
                    "deleted_at IS NULL",
                    "(to_tsvector('english', content) @@ plainto_tsquery('english', %s) OR content ILIKE %s)",
                ]
                params = [query, f"%{query}%"]
                if project:
                    conditions.append("project = %s")
                    params.append(project)
                if since_dt:
                    conditions.append("created_at >= %s")
                    params.append(since_dt)
                if before_dt:
                    conditions.append("created_at < %s")
                    params.append(before_dt)
                params.append(limit)
                sql = (
                    "SELECT id, content, tags, source, project, created_at FROM memories "
                    f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT %s"
                )
                cur.execute(sql, params)
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No memories found for: '{query}'"
    except Exception as e:
        log.error("search_memories failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def list_memories(limit: int = 20, tag: str = None, project: str = None,
                  since: str = None, before: str = None) -> str:
    """List recent memories, optionally filtered by tag, project, and/or date range (ISO dates)."""
    since_dt, err = _parse_dt(since, "since")
    if err:
        return err
    before_dt, err = _parse_dt(before, "before")
    if err:
        return err

    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = ["deleted_at IS NULL"]
                params = []
                if tag:
                    conditions.append("%s = ANY(tags)")
                    params.append(tag)
                if project:
                    conditions.append("project = %s")
                    params.append(project)
                if since_dt:
                    conditions.append("created_at >= %s")
                    params.append(since_dt)
                if before_dt:
                    conditions.append("created_at < %s")
                    params.append(before_dt)
                params.append(limit)
                cur.execute(
                    f"SELECT id, content, tags, source, project, created_at FROM memories WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT %s",
                    params
                )
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else "No memories stored yet."
    except Exception as e:
        log.error("list_memories failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def get_memory(memory_id: int) -> str:
    """Fetch a single memory by ID with full content. Returns the memory even if soft-deleted (deleted_at will be set)."""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, content, tags, source, project, created_at, updated_at, deleted_at FROM memories WHERE id = %s",
                    (memory_id,)
                )
                row = cur.fetchone()
        return json.dumps(dict(row), indent=2, default=str) if row else f"❌ No memory with ID {memory_id}"
    except Exception as e:
        log.error("get_memory id=%s failed: %s", memory_id, e)
        return f"❌ Error: {e}"


@mcp.tool()
def recent_context(project: str = None, limit: int = 10) -> str:
    """Return recent distilled memories — ideal for session start context recall. Filter by project for focused recall."""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if project:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at FROM memories "
                        "WHERE 'distilled' = ANY(tags) AND project = %s AND deleted_at IS NULL "
                        "ORDER BY created_at DESC LIMIT %s",
                        (project, limit)
                    )
                else:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at FROM memories "
                        "WHERE 'distilled' = ANY(tags) AND deleted_at IS NULL "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,)
                    )
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else "No distilled memories yet. Run distill_sessions.py to generate them."
    except Exception as e:
        log.error("recent_context failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def update_memory(memory_id: int, content: str = None, tags: list[str] = None, force: bool = False) -> str:
    """Update content and/or tags. Re-embeds if content changes.
    Returns a warning (without saving) if new content is ≥0.92 similar to an existing memory.
    Pass force=True to bypass the duplicate check and save anyway."""
    if not content and tags is None:
        return "❌ Provide at least one of: content, tags"
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if content and not force:
                    new_vector = embed(content)
                    # Check for near-duplicates, excluding the memory being updated and deleted memories
                    cur.execute(
                        "SELECT id, content, ROUND((1 - (embedding <=> %s))::numeric, 4) AS sim "
                        "FROM memories "
                        "WHERE (1 - (embedding <=> %s)) >= 0.92 "
                        "AND id != %s "
                        "AND deleted_at IS NULL "
                        "ORDER BY embedding <=> %s LIMIT 1",
                        (new_vector, new_vector, memory_id, new_vector)
                    )
                    dup = cur.fetchone()
                    if dup:
                        return (
                            f"⚠️ Near-duplicate detected: memory ID {dup['id']} "
                            f"(similarity {dup['sim']}): {dup['content'][:80]}...\n"
                            f"Update not saved. Call update_memory again with force=True to override."
                        )

            with conn.cursor() as cur:
                if content:
                    new_vector = embed(content)
                    if tags is not None:
                        cur.execute(
                            "UPDATE memories SET content=%s, tags=%s, embedding=%s WHERE id=%s AND deleted_at IS NULL",
                            (content, tags, new_vector, memory_id)
                        )
                    else:
                        cur.execute(
                            "UPDATE memories SET content=%s, embedding=%s WHERE id=%s AND deleted_at IS NULL",
                            (content, new_vector, memory_id)
                        )
                else:
                    cur.execute(
                        "UPDATE memories SET tags=%s WHERE id=%s AND deleted_at IS NULL",
                        (tags, memory_id)
                    )
                updated = cur.rowcount
            conn.commit()
        log.info("Memory updated id=%s", memory_id)
        return f"✅ Memory {memory_id} updated." if updated else f"❌ No active memory with ID {memory_id}"
    except Exception as e:
        log.error("update_memory id=%s failed: %s", memory_id, e)
        return f"❌ Error: {e}"


@mcp.tool()
def delete_memory(memory_id: int) -> str:
    """Soft-delete a memory by ID. The memory is hidden but not permanently removed.
    Use restore_memory to undo, or purge_memory to permanently delete."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE memories SET deleted_at = NOW() WHERE id = %s AND deleted_at IS NULL",
                    (memory_id,)
                )
                deleted = cur.rowcount
            conn.commit()
        log.info("Memory soft-deleted id=%s", memory_id)
        return f"✅ Memory {memory_id} deleted." if deleted else f"❌ No active memory with ID {memory_id}"
    except Exception as e:
        log.error("delete_memory id=%s failed: %s", memory_id, e)
        return f"❌ Error: {e}"


@mcp.tool()
def restore_memory(memory_id: int) -> str:
    """Restore a previously soft-deleted memory, making it active again."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE memories SET deleted_at = NULL WHERE id = %s AND deleted_at IS NOT NULL",
                    (memory_id,)
                )
                restored = cur.rowcount
            conn.commit()
        log.info("Memory restored id=%s", memory_id)
        return f"✅ Memory {memory_id} restored." if restored else f"❌ No deleted memory with ID {memory_id}"
    except Exception as e:
        log.error("restore_memory id=%s failed: %s", memory_id, e)
        return f"❌ Error: {e}"


@mcp.tool()
def purge_memory(memory_id: int) -> str:
    """Permanently delete a memory. The memory must already be soft-deleted (call delete_memory first).
    This is irreversible — the row is removed from the database."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM memories WHERE id = %s AND deleted_at IS NOT NULL",
                    (memory_id,)
                )
                purged = cur.rowcount
            conn.commit()
        log.info("Memory purged id=%s", memory_id)
        return (
            f"✅ Memory {memory_id} permanently purged."
            if purged else
            f"❌ Memory {memory_id} not found or not soft-deleted (call delete_memory first)"
        )
    except Exception as e:
        log.error("purge_memory id=%s failed: %s", memory_id, e)
        return f"❌ Error: {e}"


@mcp.tool()
def list_tags() -> str:
    """List all unique tags with counts."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tag, COUNT(*) AS count FROM memories, unnest(tags) AS tag "
                    "WHERE deleted_at IS NULL GROUP BY tag ORDER BY count DESC"
                )
                rows = cur.fetchall()
        return json.dumps([{"tag": r[0], "count": r[1]} for r in rows], indent=2) if rows else "No tags found."
    except Exception as e:
        log.error("list_tags failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def get_stats() -> str:
    """Return memory counts broken down by project and source, plus session import status."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NOT NULL")
                total_deleted = cur.fetchone()[0]
                cur.execute("SELECT COALESCE(NULLIF(project,''), '(none)'), COUNT(*) FROM memories WHERE deleted_at IS NULL GROUP BY project ORDER BY COUNT(*) DESC")
                by_project = cur.fetchall()
                cur.execute("SELECT source, COUNT(*) FROM memories WHERE deleted_at IS NULL GROUP BY source ORDER BY COUNT(*) DESC LIMIT 10")
                by_source = cur.fetchall()
                cur.execute("SELECT COUNT(*) FROM imported_sessions")
                sessions_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM imported_sessions WHERE distilled = TRUE")
                sessions_distilled = cur.fetchone()[0]
        return json.dumps({
            "total_memories": total,
            "deleted_memories": total_deleted,
            "by_project": [{"project": r[0], "count": r[1]} for r in by_project],
            "top_sources": [{"source": r[0], "count": r[1]} for r in by_source],
            "sessions": {
                "total": sessions_total,
                "distilled": sessions_distilled,
                "pending_distill": sessions_total - sessions_distilled
            }
        }, indent=2)
    except Exception as e:
        log.error("get_stats failed: %s", e)
        return f"❌ Error: {e}"


@mcp.tool()
def export_memories(project: str = None, tag: str = None, since: str = None,
                    before: str = None, output_format: str = "json") -> str:
    """Export memories as JSON or markdown. Filter by project, tag, and/or date range (ISO dates).
    output_format: 'json' (default) or 'markdown'."""
    since_dt, err = _parse_dt(since, "since")
    if err:
        return err
    before_dt, err = _parse_dt(before, "before")
    if err:
        return err
    if output_format not in ("json", "markdown"):
        return "❌ output_format must be 'json' or 'markdown'"

    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = ["deleted_at IS NULL"]
                params = []
                if tag:
                    conditions.append("%s = ANY(tags)")
                    params.append(tag)
                if project:
                    conditions.append("project = %s")
                    params.append(project)
                if since_dt:
                    conditions.append("created_at >= %s")
                    params.append(since_dt)
                if before_dt:
                    conditions.append("created_at < %s")
                    params.append(before_dt)
                cur.execute(
                    f"SELECT id, content, tags, source, project, created_at, updated_at FROM memories WHERE {' AND '.join(conditions)} ORDER BY created_at ASC",
                    params
                )
                rows = cur.fetchall()

        if not rows:
            return "No memories found matching the given filters."

        records = [dict(r) for r in rows]
        log.info("Exporting %d memories format=%s", len(records), output_format)

        now = datetime.now(timezone.utc)
        if output_format == "json":
            return json.dumps({"exported_at": now.isoformat(), "count": len(records), "memories": records}, indent=2, default=str)

        # Markdown format
        lines = [f"# Memory Export", f"*Exported: {now.strftime('%Y-%m-%d %H:%M UTC')} — {len(records)} memories*", ""]
        for r in records:
            lines.append(f"## [{r['id']}] {r['created_at']}")
            if r.get("project"):
                lines.append(f"**Project:** {r['project']}  **Source:** {r['source']}")
            lines.append(f"**Tags:** {', '.join(r['tags']) if r['tags'] else '(none)'}")
            lines.append("")
            lines.append(r["content"])
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    except Exception as e:
        log.error("export_memories failed: %s", e)
        return f"❌ Error: {e}"


if __name__ == "__main__":
    mcp.run(transport="sse")
