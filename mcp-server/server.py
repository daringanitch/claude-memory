import os, json
from contextlib import contextmanager

import psycopg2, psycopg2.extras, psycopg2.pool
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-memory", host="0.0.0.0", port=3333)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
print("⏳ Loading embedding model...")
embedder = SentenceTransformer("all-mpnet-base-v2")
print("⏳ Connecting to database...")
_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, DATABASE_URL)
print("✅ Ready.")


@contextmanager
def db_conn():
    conn = _pool.getconn()
    register_vector(conn)
    conn.autocommit = False
    try:
        yield conn
    finally:
        _pool.putconn(conn)


def embed(text):
    return embedder.encode(text, normalize_embeddings=True)


@mcp.tool()
def save_memory(content: str, tags: list[str] = [], source: str = "claude-code", project: str = "") -> str:
    """Save a thought, request, note, or piece of information to persistent memory."""
    vector = embed(content)
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Semantic dedup check
                cur.execute(
                    "SELECT id, content, ROUND((1 - (embedding <=> %s))::numeric, 4) AS sim "
                    "FROM memories WHERE (1 - (embedding <=> %s)) >= 0.92 "
                    "ORDER BY embedding <=> %s LIMIT 1",
                    (vector, vector, vector)
                )
                dup = cur.fetchone()
                if dup:
                    return f"Duplicate of memory ID {dup['id']} (similarity {dup['sim']}): {dup['content'][:80]}..."
                cur.execute(
                    "INSERT INTO memories (content, tags, source, project, embedding) VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at",
                    (content, tags, source, project, vector)
                )
                row = cur.fetchone()
            conn.commit()
        return f"✅ Memory saved (ID: {row['id']}, created: {row['created_at']})"
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def semantic_search(query: str, limit: int = 10, min_similarity: float = 0.3, project: str = None) -> str:
    """Search memories by MEANING using vector similarity. Optionally filter by project."""
    vector = embed(query)
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if project:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at, ROUND((1 - (embedding <=> %s))::numeric, 4) AS similarity "
                        "FROM memories WHERE embedding IS NOT NULL AND (1 - (embedding <=> %s)) >= %s AND project = %s "
                        "ORDER BY embedding <=> %s LIMIT %s",
                        (vector, vector, min_similarity, project, vector, limit)
                    )
                else:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at, ROUND((1 - (embedding <=> %s))::numeric, 4) AS similarity "
                        "FROM memories WHERE embedding IS NOT NULL AND (1 - (embedding <=> %s)) >= %s "
                        "ORDER BY embedding <=> %s LIMIT %s",
                        (vector, vector, min_similarity, vector, limit)
                    )
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No similar memories found for: '{query}'"
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def search_memories(query: str, limit: int = 10, project: str = None) -> str:
    """Search memories by exact keyword or phrase. Optionally filter by project."""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if project:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at FROM memories "
                        "WHERE (to_tsvector('english', content) @@ plainto_tsquery('english', %s) OR content ILIKE %s) AND project = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (query, f"%{query}%", project, limit)
                    )
                else:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at FROM memories "
                        "WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s) OR content ILIKE %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (query, f"%{query}%", limit)
                    )
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No memories found for: '{query}'"
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def list_memories(limit: int = 20, tag: str = None, project: str = None) -> str:
    """List recent memories, optionally filtered by tag and/or project."""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = []
                params = []
                if tag:
                    conditions.append("%s = ANY(tags)")
                    params.append(tag)
                if project:
                    conditions.append("project = %s")
                    params.append(project)
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(limit)
                cur.execute(f"SELECT id, content, tags, source, project, created_at FROM memories {where} ORDER BY created_at DESC LIMIT %s", params)
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else "No memories stored yet."
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def get_memory(memory_id: int) -> str:
    """Fetch a single memory by ID with full content."""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, content, tags, source, project, created_at, updated_at FROM memories WHERE id = %s",
                    (memory_id,)
                )
                row = cur.fetchone()
        return json.dumps(dict(row), indent=2, default=str) if row else f"❌ No memory with ID {memory_id}"
    except Exception as e:
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
                        "WHERE 'distilled' = ANY(tags) AND project = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (project, limit)
                    )
                else:
                    cur.execute(
                        "SELECT id, content, tags, source, project, created_at FROM memories "
                        "WHERE 'distilled' = ANY(tags) "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,)
                    )
                rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else "No distilled memories yet. Run distill_sessions.py to generate them."
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def update_memory(memory_id: int, content: str = None, tags: list[str] = None) -> str:
    """Update content and/or tags. Re-embeds if content changes."""
    if not content and tags is None:
        return "❌ Provide at least one of: content, tags"
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                if content and tags is not None:
                    cur.execute("UPDATE memories SET content=%s, tags=%s, embedding=%s WHERE id=%s", (content, tags, embed(content), memory_id))
                elif content:
                    cur.execute("UPDATE memories SET content=%s, embedding=%s WHERE id=%s", (content, embed(content), memory_id))
                else:
                    cur.execute("UPDATE memories SET tags=%s WHERE id=%s", (tags, memory_id))
                updated = cur.rowcount
            conn.commit()
        return f"✅ Memory {memory_id} updated." if updated else f"❌ No memory with ID {memory_id}"
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def delete_memory(memory_id: int) -> str:
    """Delete a memory by ID."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memories WHERE id=%s", (memory_id,))
                deleted = cur.rowcount
            conn.commit()
        return f"✅ Memory {memory_id} deleted." if deleted else f"❌ No memory with ID {memory_id}"
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def list_tags() -> str:
    """List all unique tags with counts."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tag, COUNT(*) AS count FROM memories, unnest(tags) AS tag GROUP BY tag ORDER BY count DESC")
                rows = cur.fetchall()
        return json.dumps([{"tag": r[0], "count": r[1]} for r in rows], indent=2) if rows else "No tags found."
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def get_stats() -> str:
    """Return memory counts broken down by project and source, plus session import status."""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memories")
                total = cur.fetchone()[0]
                cur.execute("SELECT COALESCE(NULLIF(project,''), '(none)'), COUNT(*) FROM memories GROUP BY project ORDER BY COUNT(*) DESC")
                by_project = cur.fetchall()
                cur.execute("SELECT source, COUNT(*) FROM memories GROUP BY source ORDER BY COUNT(*) DESC LIMIT 10")
                by_source = cur.fetchall()
                cur.execute("SELECT COUNT(*) FROM imported_sessions")
                sessions_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM imported_sessions WHERE distilled = TRUE")
                sessions_distilled = cur.fetchone()[0]
        return json.dumps({
            "total_memories": total,
            "by_project": [{"project": r[0], "count": r[1]} for r in by_project],
            "top_sources": [{"source": r[0], "count": r[1]} for r in by_source],
            "sessions": {
                "total": sessions_total,
                "distilled": sessions_distilled,
                "pending_distill": sessions_total - sessions_distilled
            }
        }, indent=2)
    except Exception as e:
        return f"❌ Error: {e}"


if __name__ == "__main__":
    mcp.run(transport="sse")
