import os, json
import numpy as np
import psycopg2, psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-memory", host="0.0.0.0", port=3333)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://claude:memory_pass@localhost:5432/memory")
print(":hourglass_flowing_sand: Loading embedding model...")
embedder = SentenceTransformer("all-MiniLM-L6-v2")
print(":white_check_mark: Ready.")

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    register_vector(conn)
    return conn

def embed(text):
    return embedder.encode(text, normalize_embeddings=True)

@mcp.tool()
def save_memory(content: str, tags: list[str] = [], source: str = "claude-code") -> str:
    """Save a thought, request, note, or piece of information to persistent memory."""
    vector = embed(content)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO memories (content, tags, source, embedding) VALUES (%s, %s, %s, %s) RETURNING id, created_at", (content, tags, source, vector))
            row = cur.fetchone()
        conn.commit()
        return f":white_check_mark: Memory saved (ID: {row[0]}, created: {row[1]})"
    except Exception as e:
        conn.rollback(); return f":x: Error: {e}"
    finally:
        conn.close()

@mcp.tool()
def semantic_search(query: str, limit: int = 10, min_similarity: float = 0.3) -> str:
    """Search memories by MEANING using vector similarity."""
    vector = embed(query)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, content, tags, source, created_at, ROUND((1 - (embedding <=> %s))::numeric, 4) AS similarity FROM memories WHERE embedding IS NOT NULL AND (1 - (embedding <=> %s)) >= %s ORDER BY embedding <=> %s LIMIT %s", (vector, vector, min_similarity, vector, limit))
            rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No similar memories found for: '{query}'"
    finally:
        conn.close()

@mcp.tool()
def search_memories(query: str, limit: int = 10) -> str:
    """Search memories by exact keyword or phrase."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, content, tags, source, created_at FROM memories WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s) OR content ILIKE %s ORDER BY created_at DESC LIMIT %s", (query, f"%{query}%", limit))
            rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else f"No memories found for: '{query}'"
    finally:
        conn.close()

@mcp.tool()
def list_memories(limit: int = 20, tag: str = None) -> str:
    """List recent memories, optionally filtered by tag."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tag:
                cur.execute("SELECT id, content, tags, source, created_at FROM memories WHERE %s = ANY(tags) ORDER BY created_at DESC LIMIT %s", (tag, limit))
            else:
                cur.execute("SELECT id, content, tags, source, created_at FROM memories ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
        return json.dumps([dict(r) for r in rows], indent=2, default=str) if rows else "No memories stored yet."
    finally:
        conn.close()

@mcp.tool()
def update_memory(memory_id: int, content: str = None, tags: list[str] = None) -> str:
    """Update content and/or tags. Re-embeds if content changes."""
    if not content and tags is None: return ":x: Provide at least one of: content, tags"
    conn = get_db()
    try:
        with conn.cursor() as cur:
            if content and tags is not None:
                cur.execute("UPDATE memories SET content=%s, tags=%s, embedding=%s WHERE id=%s", (content, tags, embed(content), memory_id))
            elif content:
                cur.execute("UPDATE memories SET content=%s, embedding=%s WHERE id=%s", (content, embed(content), memory_id))
            else:
                cur.execute("UPDATE memories SET tags=%s WHERE id=%s", (tags, memory_id))
            updated = cur.rowcount
        conn.commit()
        return f":white_check_mark: Memory {memory_id} updated." if updated else f":x: No memory with ID {memory_id}"
    except Exception as e:
        conn.rollback(); return f":x: Error: {e}"
    finally:
        conn.close()

@mcp.tool()
def delete_memory(memory_id: int) -> str:
    """Delete a memory by ID."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memories WHERE id=%s", (memory_id,))
            deleted = cur.rowcount
        conn.commit()
        return f":white_check_mark: Memory {memory_id} deleted." if deleted else f":x: No memory with ID {memory_id}"
    except Exception as e:
        conn.rollback(); return f":x: Error: {e}"
    finally:
        conn.close()

@mcp.tool()
def list_tags() -> str:
    """List all unique tags with counts."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tag, COUNT(*) AS count FROM memories, unnest(tags) AS tag GROUP BY tag ORDER BY count DESC")
            rows = cur.fetchall()
        return json.dumps([{"tag": r[0], "count": r[1]} for r in rows], indent=2) if rows else "No tags found."
    finally:
        conn.close()

if __name__ == "__main__":
    mcp.run(transport="sse")
