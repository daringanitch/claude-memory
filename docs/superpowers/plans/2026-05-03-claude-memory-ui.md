# Claude Memory UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-file React UI at `mcp-server/ui.html` served from the existing MCP server, backed by 10 new REST endpoints added to `mcp-server/server.py`.

**Architecture:** Extend `server.py` with helper functions (testable) + thin Starlette route wrappers. The UI is one `ui.html` file (React + Babel CDN, no build step) served at `GET /ui`. All API calls go to the same origin (`/api/*`).

**Tech Stack:** Python 3.10+ / Starlette / psycopg2 / pgvector · React 18 CDN · Babel standalone · Lucide CDN · marked.js CDN · Mulish + Cardo (Google Fonts)

**Spec:** `docs/superpowers/specs/2026-05-03-claude-memory-ui-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `mcp-server/server.py` | Modify | Add 9 helper functions + 10 Starlette routes |
| `mcp-server/ui.html` | **Create** | Single-file React app (all CSS, JS, components) |
| `tests/test_api.py` | **Create** | Tests for all new `_api_*` helper functions |

---

## Task 1: REST helpers — projects, tags, stats

**Files:**
- Modify: `mcp-server/server.py` (add after the `cache_invalidate_endpoint` block, before `_write_guard`)
- Create: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api.py`:

```python
"""Tests for REST API helper functions added to server.py."""
import sys, os, json
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-server"))
import server


def _make_cur(rows):
    """Return a mock cursor whose fetchall() returns rows."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    return cur


def _make_conn(cur):
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn


class TestApiProjects:
    def test_returns_list_of_project_dicts(self):
        cur = _make_cur([{"project": "workspace", "count": 42},
                         {"project": "claude-memory", "count": 7}])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_projects()
        assert result == [{"project": "workspace", "count": 42},
                          {"project": "claude-memory", "count": 7}]

    def test_empty_db_returns_empty_list(self):
        cur = _make_cur([])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_projects()
        assert result == []


class TestApiTags:
    def test_returns_tag_counts(self):
        cur = _make_cur([{"tag": "type:decision", "count": 15}])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_tags()
        assert result == [{"tag": "type:decision", "count": 15}]


class TestApiStats:
    def test_returns_stats_dict(self):
        cur = _make_cur([{"active": 247, "deleted": 3, "projects": 12,
                          "avg_content_len": 512}])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_stats()
        assert result["active"] == 247
        assert result["projects"] == 12
        assert "storage_mb" in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py -v 2>&1 | head -30
```

Expected: `AttributeError: module 'server' has no attribute '_api_projects'`

- [ ] **Step 3: Add helper functions to server.py**

In `mcp-server/server.py`, insert after the `cache_invalidate_endpoint` function (after line ~136) and before `# ── Write guard`:

```python
# ── REST API helpers (called by HTTP route handlers below) ────────────────────

def _api_projects() -> list:
    """Distinct projects with memory counts, ordered by count desc."""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT project, COUNT(*) AS count FROM memories "
                "WHERE deleted_at IS NULL GROUP BY project ORDER BY count DESC"
            )
            return [dict(r) for r in cur.fetchall()]


def _api_tags() -> list:
    """All tags with counts across active memories, ordered by count desc."""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT tag, COUNT(*) AS count FROM memories, unnest(tags) AS tag "
                "WHERE deleted_at IS NULL GROUP BY tag ORDER BY count DESC"
            )
            return [dict(r) for r in cur.fetchall()]


def _api_stats() -> dict:
    """Aggregate stats: counts, estimated storage, project count."""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT "
                "  COUNT(*) FILTER (WHERE deleted_at IS NULL) AS active, "
                "  COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted, "
                "  COUNT(DISTINCT project) FILTER (WHERE deleted_at IS NULL) AS projects, "
                "  COALESCE(AVG(LENGTH(content)) FILTER (WHERE deleted_at IS NULL), 0)::int AS avg_content_len "
                "FROM memories"
            )
            row = dict(cur.fetchone())
    # Rough storage estimate: each embedding is 768 floats × 4 bytes = 3072 bytes
    # Plus avg content length. Multiply by active memory count.
    active = row["active"] or 0
    embedding_bytes = active * 3072
    content_bytes = active * (row["avg_content_len"] or 0)
    metadata_bytes = active * 200  # tags, timestamps, id overhead estimate
    total_bytes = embedding_bytes + content_bytes + metadata_bytes
    row["storage_mb"] = round(total_bytes / 1_048_576, 1)
    row["storage_breakdown"] = {
        "embeddings_mb": round(embedding_bytes / 1_048_576, 1),
        "content_mb": round(content_bytes / 1_048_576, 1),
        "metadata_mb": round(metadata_bytes / 1_048_576, 1),
    }
    return row
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py::TestApiProjects tests/test_api.py::TestApiTags tests/test_api.py::TestApiStats -v
```

Expected: 5 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/server.py tests/test_api.py
git commit -m "feat: add _api_projects, _api_tags, _api_stats helpers"
```

---

## Task 2: REST helpers — memories list, single memory, related

**Files:**
- Modify: `mcp-server/server.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
class TestApiListMemories:
    def test_returns_memory_list(self):
        row = {"id": 1, "content": "test content", "tags": ["type:decision"],
               "source": "claude-code", "project": "workspace",
               "created_at": "2026-05-01T10:00:00", "updated_at": "2026-05-01T10:00:00"}
        cur = _make_cur([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_list_memories()
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["title"] == "test content"  # no truncation needed here

    def test_truncates_long_content_to_title(self):
        long = "A" * 100
        row = {"id": 2, "content": long, "tags": [], "source": "claude-code",
               "project": "", "created_at": "2026-05-01T10:00:00",
               "updated_at": "2026-05-01T10:00:00"}
        cur = _make_cur([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_list_memories()
        assert result[0]["title"].endswith("…")
        assert len(result[0]["title"]) <= 73  # 72 chars + ellipsis


class TestApiGetMemory:
    def test_returns_memory_dict(self):
        row = {"id": 5, "content": "hello", "tags": ["type:fix"],
               "source": "claude-code", "project": "workspace",
               "created_at": "2026-04-30T09:00:00", "updated_at": "2026-04-30T09:00:00",
               "deleted_at": None}
        cur = _make_cur([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_get_memory(5)
        assert result["id"] == 5
        assert result["content"] == "hello"

    def test_returns_none_when_not_found(self):
        cur = _make_cur([])
        cur.fetchone.return_value = None
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_get_memory(999)
        assert result is None


class TestApiRelatedMemories:
    def test_returns_related_list(self):
        row = {"id": 3, "content": "related memory", "tags": [], "project": "workspace",
               "created_at": "2026-04-28T08:00:00", "sim": 0.88}
        cur = _make_cur([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
        # _api_related_memories needs the embedding of the source memory first.
        # It calls _api_get_memory internally; mock that too.
        source_row = {"id": 1, "content": "source", "tags": [], "project": "workspace",
                      "created_at": "2026-04-30T10:00:00", "updated_at": "2026-04-30T10:00:00",
                      "deleted_at": None}
        with patch("server.db_conn") as mock_db, \
             patch("server.embed", return_value=[0.0] * 768):
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            cur.fetchone.return_value = source_row
            result = server._api_related_memories(1, limit=3)
        # Result shape validated: list (possibly empty in mock scenario)
        assert isinstance(result, list)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py::TestApiListMemories tests/test_api.py::TestApiGetMemory tests/test_api.py::TestApiRelatedMemories -v 2>&1 | head -20
```

Expected: `AttributeError: module 'server' has no attribute '_api_list_memories'`

- [ ] **Step 3: Add helpers to server.py**

Append after `_api_stats()`:

```python
def _api_list_memories(project: str = None, tag: str = None,
                        since: str = None, before: str = None,
                        limit: int = 50, offset: int = 0) -> list:
    """Paginated list of active memories with derived 'title' field."""
    conditions = ["m.deleted_at IS NULL"]
    params = []
    if project:
        conditions.append("m.project = %s")
        params.append(project)
    if tag:
        conditions.append("%s = ANY(m.tags)")
        params.append(tag)
    since_dt, _ = _parse_dt(since, "since")
    before_dt, _ = _parse_dt(before, "before")
    if since_dt:
        conditions.append("m.created_at >= %s")
        params.append(since_dt)
    if before_dt:
        conditions.append("m.created_at < %s")
        params.append(before_dt)
    where = " AND ".join(conditions)
    params.extend([limit, offset])
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT id, content, tags, source, project, created_at, updated_at "
                f"FROM memories m WHERE {where} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        c = r["content"]
        r["title"] = (c[:72] + "…") if len(c) > 72 else c
        r["content_length"] = len(c)
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"]
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat() if hasattr(r["updated_at"], "isoformat") else r["updated_at"]
    return rows


def _api_get_memory(memory_id: int) -> dict | None:
    """Fetch single memory by id. Returns None if not found."""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, content, tags, source, project, created_at, updated_at, deleted_at "
                "FROM memories WHERE id = %s",
                (memory_id,)
            )
            row = cur.fetchone()
    if not row:
        return None
    r = dict(row)
    r["title"] = (r["content"][:72] + "…") if len(r["content"]) > 72 else r["content"]
    r["content_length"] = len(r["content"])
    for f in ("created_at", "updated_at", "deleted_at"):
        if r.get(f) and hasattr(r[f], "isoformat"):
            r[f] = r[f].isoformat()
    return r


def _api_related_memories(memory_id: int, limit: int = 3) -> list:
    """Return up to `limit` nearest-neighbor memories to the given memory id."""
    source = _api_get_memory(memory_id)
    if not source:
        return []
    vec = embed(source["content"])
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, content, tags, project, created_at, "
                "ROUND((1 - (embedding <=> %s))::numeric, 4) AS sim "
                "FROM memories WHERE deleted_at IS NULL AND id != %s "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, memory_id, vec, limit)
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["title"] = (r["content"][:72] + "…") if len(r["content"]) > 72 else r["content"]
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
        r["sim"] = float(r["sim"])
    return rows
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py -v
```

Expected: all tests PASSED (13+)

- [ ] **Step 5: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/server.py tests/test_api.py
git commit -m "feat: add _api_list_memories, _api_get_memory, _api_related_memories helpers"
```

---

## Task 3: REST helpers — recall, preferences, bulk delete

**Files:**
- Modify: `mcp-server/server.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
class TestApiRecall:
    def test_returns_ranked_results(self):
        row = {"id": 7, "content": "auth middleware is driven by compliance",
               "tags": ["type:decision"], "project": "workspace",
               "created_at": "2026-04-30T10:00:00", "sim": 0.91}
        cur = _make_cur([row])
        with patch("server.db_conn") as mock_db, \
             patch("server.embed", return_value=[0.0] * 768):
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_recall("auth compliance", threshold=0.78)
        assert isinstance(result, list)

    def test_empty_query_returns_empty_list(self):
        result = server._api_recall("", threshold=0.78)
        assert result == []


class TestApiPreferences:
    def test_groups_by_category_tag(self):
        rows = [
            {"id": 1, "content": "Prefers terse responses", "tags": ["type:preference", "category:workflow"],
             "project": "workspace", "created_at": "2026-04-30T10:00:00", "updated_at": "2026-04-28T10:00:00"},
            {"id": 2, "content": "Uses brew Python", "tags": ["type:preference", "category:stack"],
             "project": "workspace", "created_at": "2026-04-29T10:00:00", "updated_at": "2026-04-25T10:00:00"},
        ]
        cur = _make_cur(rows)
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_preferences()
        categories = {g["category"] for g in result}
        assert "workflow" in categories
        assert "stack" in categories

    def test_confidence_based_on_recency(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rows = [{"id": 1, "content": "Recent pref", "tags": ["type:preference"],
                 "project": "", "created_at": recent, "updated_at": recent}]
        cur = _make_cur(rows)
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_preferences()
        item = result[0]["items"][0]
        assert item["confidence"] >= 0.9  # updated in last 7 days → high confidence


class TestApiBulkDelete:
    def test_dry_run_returns_count_without_deleting(self):
        cur = _make_cur([])
        cur.rowcount = 5
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=_make_conn(cur))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server._api_bulk_delete(project="workspace", tag=None, dry_run=True)
        assert result["dry_run"] is True
        assert "deleted" in result

    def test_requires_at_least_one_filter(self):
        result = server._api_bulk_delete(project=None, tag=None, dry_run=False)
        assert "error" in result
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py::TestApiRecall tests/test_api.py::TestApiPreferences tests/test_api.py::TestApiBulkDelete -v 2>&1 | head -20
```

Expected: `AttributeError: module 'server' has no attribute '_api_recall'`

- [ ] **Step 3: Add helpers to server.py**

Append after `_api_related_memories()`:

```python
def _api_recall(query: str, threshold: float = 0.78, limit: int = 20) -> list:
    """Semantic search. Returns ranked list with score and snippet."""
    if not query.strip():
        return []
    vec = embed(query)
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, content, tags, project, created_at, "
                "ROUND((1 - (embedding <=> %s))::numeric, 4) AS sim "
                "FROM memories WHERE deleted_at IS NULL "
                "AND (1 - (embedding <=> %s)) >= %s "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, vec, threshold, vec, limit)
            )
            rows = [dict(r) for r in cur.fetchall()]
    results = []
    for r in rows:
        title = (r["content"][:72] + "…") if len(r["content"]) > 72 else r["content"]
        snippet = r["content"][:200] if len(r["content"]) > 72 else r["content"]
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
        results.append({
            "id": r["id"],
            "title": title,
            "snippet": snippet,
            "tags": r["tags"],
            "project": r["project"],
            "created_at": r["created_at"],
            "sim": float(r["sim"]),
        })
    return results


def _api_preferences() -> list:
    """Return type:preference and type:pattern memories grouped by category tag."""
    from datetime import datetime, timezone, timedelta
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, content, tags, project, created_at, updated_at "
                "FROM memories WHERE deleted_at IS NULL "
                "AND ('type:preference' = ANY(tags) OR 'type:pattern' = ANY(tags)) "
                "ORDER BY updated_at DESC"
            )
            rows = [dict(r) for r in cur.fetchall()]
    now = datetime.now(timezone.utc)
    groups: dict[str, list] = {}
    for r in rows:
        # Derive category from tags (first tag starting with "category:")
        cat = next((t.split("category:")[1] for t in r["tags"] if t.startswith("category:")), "general")
        # Confidence from recency of updated_at
        updated = r["updated_at"]
        if hasattr(updated, "tzinfo") and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if hasattr(updated, "isoformat"):
            age_days = (now - updated).days if updated else 999
        else:
            age_days = 999
        confidence = 0.95 if age_days <= 7 else (0.80 if age_days <= 30 else 0.65)
        # Source tag (e.g. "38 sessions", "Decision in s-038")
        source_tag = next((t for t in r["tags"] if t.startswith("source:")), "")
        source = source_tag.replace("source:", "") if source_tag else r["project"] or "unknown"
        item = {"text": r["content"], "confidence": confidence, "source": source}
        groups.setdefault(cat, []).append(item)
    return [{"category": cat, "items": items} for cat, items in groups.items()]


def _api_bulk_delete(project: str = None, tag: str = None, dry_run: bool = True) -> dict:
    """Soft-delete memories matching project and/or tag filter."""
    if not project and not tag:
        return {"error": "At least one filter (project or tag) is required"}
    conditions = ["deleted_at IS NULL"]
    params = []
    if project:
        conditions.append("project = %s")
        params.append(project)
    if tag:
        conditions.append("%s = ANY(tags)")
        params.append(tag)
    where = " AND ".join(conditions)
    with db_conn() as conn:
        with conn.cursor() as cur:
            if dry_run:
                cur.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params)
                count = cur.fetchone()[0]
            else:
                cur.execute(f"UPDATE memories SET deleted_at = NOW() WHERE {where}", params)
                count = cur.rowcount
                conn.commit()
                _cache_invalidate()
    return {"deleted": count, "dry_run": dry_run, "project": project, "tag": tag}
```

- [ ] **Step 4: Run all API tests**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/test_api.py -v
```

Expected: all tests PASSED (20+)

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/ -v 2>&1 | tail -10
```

Expected: all pass (76 original + new API tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/server.py tests/test_api.py
git commit -m "feat: add _api_recall, _api_preferences, _api_bulk_delete helpers"
```

---

## Task 4: HTTP route handlers + serve ui.html

**Files:**
- Modify: `mcp-server/server.py` (add route handlers at the bottom, before `if __name__`)
- Create: `mcp-server/ui.html` (skeleton — filled in Tasks 5–10)

- [ ] **Step 1: Add route handlers to server.py**

Insert before `if __name__ == "__main__":` at the bottom of `mcp-server/server.py`:

```python
# ── REST HTTP route handlers ───────────────────────────────────────────────────

@mcp.custom_route("/ui", methods=["GET"])
async def serve_ui(request: Request) -> Response:
    """Serve the single-file React UI."""
    from starlette.responses import HTMLResponse
    import pathlib
    ui_path = pathlib.Path(__file__).parent / "ui.html"
    if not ui_path.exists():
        return JSONResponse({"error": "ui.html not found"}, status_code=404)
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@mcp.custom_route("/api/projects", methods=["GET"])
async def api_projects(request: Request) -> JSONResponse:
    try:
        return JSONResponse(_api_projects())
    except Exception as e:
        log.error("GET /api/projects failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/tags", methods=["GET"])
async def api_tags(request: Request) -> JSONResponse:
    try:
        return JSONResponse(_api_tags())
    except Exception as e:
        log.error("GET /api/tags failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/stats", methods=["GET"])
async def api_stats(request: Request) -> JSONResponse:
    try:
        return JSONResponse(_api_stats())
    except Exception as e:
        log.error("GET /api/stats failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/memories", methods=["GET"])
async def api_memories_list(request: Request) -> JSONResponse:
    try:
        q = request.query_params
        rows = _api_list_memories(
            project=q.get("project"),
            tag=q.get("tag"),
            since=q.get("since"),
            before=q.get("before"),
            limit=int(q.get("limit", 50)),
            offset=int(q.get("offset", 0)),
        )
        return JSONResponse(rows)
    except Exception as e:
        log.error("GET /api/memories failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/memories/{id}", methods=["GET"])
async def api_memory_get(request: Request) -> JSONResponse:
    try:
        memory_id = int(request.path_params["id"])
        row = _api_get_memory(memory_id)
        if row is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(row)
    except (ValueError, KeyError):
        return JSONResponse({"error": "Invalid id"}, status_code=400)
    except Exception as e:
        log.error("GET /api/memories/%s failed: %s", request.path_params.get("id"), e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/memories/{id}/related", methods=["GET"])
async def api_memory_related(request: Request) -> JSONResponse:
    try:
        memory_id = int(request.path_params["id"])
        limit = int(request.query_params.get("limit", 3))
        return JSONResponse(_api_related_memories(memory_id, limit=limit))
    except (ValueError, KeyError):
        return JSONResponse({"error": "Invalid id"}, status_code=400)
    except Exception as e:
        log.error("GET /api/memories/%s/related failed: %s", request.path_params.get("id"), e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/recall", methods=["POST"])
async def api_recall(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        query = body.get("query", "")
        threshold = float(body.get("threshold", 0.78))
        return JSONResponse(_api_recall(query, threshold=threshold))
    except Exception as e:
        log.error("POST /api/recall failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/preferences", methods=["GET"])
async def api_preferences(request: Request) -> JSONResponse:
    try:
        return JSONResponse(_api_preferences())
    except Exception as e:
        log.error("GET /api/preferences failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/memories", methods=["DELETE"])
async def api_memories_delete(request: Request) -> JSONResponse:
    try:
        q = request.query_params
        project = q.get("project")
        tag = q.get("tag")
        dry_run = q.get("dry_run", "false").lower() != "false"
        result = _api_bulk_delete(project=project, tag=tag, dry_run=dry_run)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    except Exception as e:
        log.error("DELETE /api/memories failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
```

- [ ] **Step 2: Create the ui.html skeleton**

Create `mcp-server/ui.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Claude Memory</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800;900&family=Cardo:ital@1&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/react@18/umd/react.development.js" crossorigin></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js" crossorigin></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
  <script src="https://unpkg.com/marked/marked.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --cs-blue:       #312888;
      --cs-blue-hover: #241b7b;
      --cs-blue-light: #009CFF;
      --cs-blue-dark:  #1a1560;
      --cs-coral:      #FF947B;
      --cs-white:      #ffffff;
      --cs-grey-light: #f8f7fc;
      --cs-grey:       #6b6b78;
      --cs-ink:        #1a1b1c;
      --fg-1:          var(--cs-blue);
      --fg-2:          #3d3d4a;
      --fg-3:          var(--cs-grey);
      --fg-accent:     var(--cs-blue-light);
      --border-subtle: rgba(49,40,136,0.12);
      --border-rule:   var(--cs-blue);
      --shadow-xs:     0 1px 3px rgba(26,27,28,0.06);
      --shadow-sm:     0 2px 8px rgba(26,27,28,0.08);
      --shadow-lg:     0 30px 60px -20px rgba(50,50,93,0.25), 0 18px 36px -18px rgba(0,0,0,0.30);
      --ease:          cubic-bezier(0.25,1,0.5,1);
    }
    body { font-family: 'Mulish', system-ui, sans-serif; background: var(--cs-grey-light); color: var(--fg-2); font-size: 14px; line-height: 1.6; }
    #root { min-height: 100vh; }
    /* Typography */
    .eyebrow { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.14em; color: var(--fg-accent); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; font-variant-numeric: tabular-nums; }
    /* Layout */
    .page-container { max-width: 1440px; margin: 0 auto; }
    /* Buttons */
    .btn-primary { background: var(--cs-blue); color: #fff; border: 2px solid var(--cs-blue); padding: 8px 16px; font-family: 'Mulish', sans-serif; font-size: 13px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: background 150ms var(--ease), color 150ms var(--ease); }
    .btn-primary:hover { background: transparent; color: var(--cs-blue); }
    .btn-outline { background: transparent; color: var(--cs-blue); border: 1.5px solid rgba(49,40,136,0.18); padding: 6px 12px; font-family: 'Mulish', sans-serif; font-size: 12px; font-weight: 600; cursor: pointer; transition: background 150ms var(--ease), color 150ms var(--ease); }
    .btn-outline:hover { background: var(--cs-blue); color: #fff; }
    .btn-danger { background: var(--cs-coral); color: #fff; border: 2px solid var(--cs-coral); padding: 6px 12px; font-family: 'Mulish', sans-serif; font-size: 12px; font-weight: 600; cursor: pointer; }
    /* Tag pill */
    .tag-pill { display: inline-block; border: 1.5px solid var(--cs-blue-light); border-radius: 18px; padding: 2px 10px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--cs-blue-light); margin: 2px; }
    /* Scrollbar */
    ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: transparent; } ::-webkit-scrollbar-thumb { background: rgba(49,40,136,0.15); border-radius: 3px; }
  </style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const { useState, useEffect, useRef, useCallback } = React;

// ── Project color palette (assigned in order to distinct project names) ───────
const PROJECT_COLORS = ['#312888','#009CFF','#7A4DFF','#FF947B','#1FA890','#D9325A','#F5A623','#4A90D9'];
const projectColorCache = {};
let colorIdx = 0;
function projectColor(name) {
  if (!name) return PROJECT_COLORS[0];
  if (!projectColorCache[name]) projectColorCache[name] = PROJECT_COLORS[colorIdx++ % PROJECT_COLORS.length];
  return projectColorCache[name];
}

// ── Kind → color map (inferred from type:* tag) ───────────────────────────────
const KIND_COLORS = {
  'type:decision': { border: '#009CFF', bg: 'rgba(0,156,255,0.07)',   label: 'decision' },
  'type:fix':      { border: '#1FA890', bg: 'rgba(31,168,144,0.10)',  label: 'fix'      },
  'type:bug':      { border: '#1FA890', bg: 'rgba(31,168,144,0.10)',  label: 'bug'      },
  'type:pattern':  { border: '#312888', bg: 'rgba(49,40,136,0.05)',   label: 'pattern'  },
  'type:preference':{ border: '#7A4DFF', bg: 'rgba(122,77,255,0.07)', label: 'pref'     },
  'type:warning':  { border: '#FF947B', bg: 'rgba(255,148,123,0.12)', label: 'note'     },
};
function kindStyle(tags = []) {
  for (const t of tags) if (KIND_COLORS[t]) return KIND_COLORS[t];
  return { border: '#009CFF', bg: 'rgba(0,156,255,0.07)', label: 'memory' };
}

// ── Shared primitives ─────────────────────────────────────────────────────────
function Eyebrow({ children, style }) {
  return <div className="eyebrow" style={style}>{children}</div>;
}
function MeterBar({ value, color = 'var(--cs-blue-light)', width = 64, height = 3 }) {
  return (
    <div style={{ width, height, background: 'rgba(49,40,136,0.12)', borderRadius: 2, overflow: 'hidden', flexShrink: 0 }}>
      <div style={{ width: `${Math.round(value * 100)}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 320ms var(--ease)' }} />
    </div>
  );
}

// ── Placeholder App shell (sections built in Tasks 5–10) ──────────────────────
function App() {
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    fetch('/health').then(r => r.json()).then(d => setStatus(d.status)).catch(() => setStatus('error'));
  }, []);

  return (
    <div className="page-container">
      <div style={{ padding: '40px 32px', textAlign: 'center', color: 'var(--cs-blue)' }}>
        <div style={{ fontSize: 24, fontWeight: 700 }}>Claude Memory UI</div>
        <div style={{ fontFamily: 'monospace', fontSize: 12, marginTop: 8, color: 'var(--cs-grey)' }}>
          Server status: {status} — sections loading in subsequent tasks
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>
</body>
</html>
```

- [ ] **Step 3: Verify server starts without error**

```bash
cd /Users/daringanitch/workspace/claude-memory
docker compose build mcp-server && docker compose up -d mcp-server
sleep 3
curl -s http://localhost:3333/health | python3 -m json.tool
curl -s http://localhost:3333/api/stats | python3 -m json.tool
curl -s http://localhost:3333/ui | head -5
```

Expected: health returns `{"status":"ok"}`, stats returns JSON with `active`/`projects` fields, `/ui` returns `<!DOCTYPE html>`

- [ ] **Step 4: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/server.py mcp-server/ui.html
git commit -m "feat: add REST route handlers + ui.html skeleton served at GET /ui"
```

---

## Task 5: UI — AppHeader + TitleRow + StatStrip + Footer

**Files:**
- Modify: `mcp-server/ui.html` (replace the placeholder `App` with the real one, add components)

- [ ] **Step 1: Replace the `App` function and add AppHeader, TitleRow, StatStrip, Footer**

In `mcp-server/ui.html`, replace everything inside `<script type="text/babel">` from the `// ── Placeholder App shell` comment to the end `ReactDOM.createRoot...` call with:

```jsx
// ── AppHeader ─────────────────────────────────────────────────────────────────
function BrainMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M16 4 C8 4 4 10 4 16 C4 22 8 28 16 28" fill="#312888"/>
      <path d="M16 4 C24 4 28 10 28 16 C28 22 24 28 16 28" fill="#009CFF"/>
      <line x1="16" y1="4" x2="16" y2="28" stroke="white" strokeWidth="1.2"/>
      <path d="M8 11 Q11 9 14 12" stroke="white" strokeWidth="0.9" fill="none" opacity="0.55"/>
      <path d="M7 17 Q10 15 13 18" stroke="white" strokeWidth="0.9" fill="none" opacity="0.55"/>
      <path d="M18 11 Q21 9 24 12" stroke="white" strokeWidth="0.9" fill="none" opacity="0.55"/>
      <path d="M19 17 Q22 15 25 18" stroke="white" strokeWidth="0.9" fill="none" opacity="0.55"/>
    </svg>
  );
}

function AppHeader({ activeSection, onNav, healthy, latency }) {
  const navItems = ['Timeline', 'Search', 'Preferences', 'Settings'];
  return (
    <header style={{ position: 'sticky', top: 0, zIndex: 5, background: '#fff', borderBottom: '1px solid var(--border-subtle)', padding: '12px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <BrainMark />
        <span style={{ fontFamily: 'Mulish', fontSize: 16, fontWeight: 800, letterSpacing: '0.16em', textTransform: 'uppercase' }}>
          <span style={{ color: '#312888' }}>CLAUDE</span>{' '}
          <span style={{ color: '#009CFF' }}>MEMORY</span>
        </span>
      </div>
      <nav style={{ display: 'flex', gap: 28 }}>
        {navItems.map(item => {
          const active = activeSection === item.toLowerCase();
          return (
            <button key={item} onClick={() => onNav(item.toLowerCase())}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'Mulish', fontSize: 13, fontWeight: 600, color: active ? '#312888' : 'rgba(49,40,136,0.5)', borderBottom: active ? '2px solid #312888' : '2px solid transparent', paddingBottom: 2, transition: 'color 150ms' }}>
              {item}
            </button>
          );
        })}
      </nav>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'monospace', fontSize: 11, color: 'var(--cs-grey)' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: healthy ? '#1FA890' : '#D9325A', display: 'inline-block' }} />
        {healthy ? `idx healthy · ${latency}ms` : 'db unreachable'}
        <span style={{ marginLeft: 8, color: 'var(--cs-grey)' }}>local</span>
      </div>
    </header>
  );
}

// ── TitleRow + StatStrip ──────────────────────────────────────────────────────
function StatStrip({ stats }) {
  const cells = [
    { num: stats?.active ?? '—',    label: 'Memories stored' },
    { num: stats?.storage_mb != null ? `${stats.storage_mb}MB` : '—', label: 'Storage used' },
    { num: stats?.projects ?? '—',  label: 'Active projects' },
    { num: '4.1ms',                  label: 'p99 recall' },
    { num: '0.78',                   label: 'Cos threshold' },
    { num: '90d',                    label: 'Retention' },
  ];
  return (
    <div>
      <div style={{ padding: '20px 32px 0' }}>
        <Eyebrow>Persistent Memory</Eyebrow>
        <h1 style={{ fontSize: 32, fontWeight: 700, color: '#312888', letterSpacing: '-0.015em', lineHeight: 1.15, marginTop: 4 }}>
          Recall everything.{' '}
          <em style={{ fontFamily: 'Cardo, Georgia, serif', fontStyle: 'italic', fontWeight: 400, color: 'var(--cs-grey)', fontSize: 26 }}>Across every session.</em>
        </h1>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', borderTop: '2px solid #312888', borderBottom: '1px solid var(--border-subtle)', marginTop: 16 }}>
        {cells.map((c, i) => (
          <div key={i} style={{ padding: '16px 18px', borderRight: i < 5 ? '1px solid var(--border-subtle)' : 'none' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#312888', fontVariantNumeric: 'tabular-nums' }}>{c.num}</div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--cs-grey)', marginTop: 3 }}>{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Footer ────────────────────────────────────────────────────────────────────
function Footer({ stats }) {
  return (
    <footer style={{ borderTop: '2px solid #312888', padding: '16px 32px', display: 'flex', justifyContent: 'space-between' }}>
      <span className="mono" style={{ color: 'var(--cs-grey)' }}>memory.local · running locally</span>
      <span className="mono" style={{ color: 'var(--cs-grey)' }}>{stats?.active ?? '—'} memories · 768-dim vectors · {stats?.storage_mb ?? '—'}MB</span>
      <span className="mono" style={{ color: 'var(--cs-grey)' }}>⌘K to search · feedback</span>
    </footer>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
function App() {
  const [section, setSection]       = useState('timeline');
  const [stats, setStats]           = useState(null);
  const [healthy, setHealthy]       = useState(true);
  const [latency, setLatency]       = useState('—');
  const [error, setError]           = useState(null);

  useEffect(() => {
    const t0 = Date.now();
    fetch('/health').then(r => r.json()).then(d => {
      setHealthy(d.status === 'ok');
      setLatency(Date.now() - t0);
    }).catch(() => setHealthy(false));
    fetch('/api/stats').then(r => r.json()).then(setStats).catch(e => setError(String(e)));
  }, []);

  return (
    <div className="page-container" style={{ background: '#fff', minHeight: '100vh' }}>
      {error && <div style={{ background: '#fff0ed', borderBottom: '2px solid var(--cs-coral)', padding: '10px 32px', fontSize: 13, color: '#c0392b' }}>Database unreachable. Check docker compose is running. ({error})</div>}
      <AppHeader activeSection={section} onNav={setSection} healthy={healthy} latency={latency} />
      <StatStrip stats={stats} />
      <div style={{ padding: '40px 32px', color: 'var(--cs-grey)', fontFamily: 'monospace', fontSize: 12, textAlign: 'center' }}>
        [Sections: Search, Timeline, Preferences, Settings — built in Tasks 6–10]
      </div>
      <Footer stats={stats} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
```

- [ ] **Step 2: Verify in browser**

```bash
docker compose build mcp-server && docker compose up -d mcp-server
sleep 3
open http://localhost:3333/ui
```

Expected: header with brain logo + wordmark, stat strip showing real counts, footer. No console errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html
git commit -m "feat: UI AppHeader, TitleRow, StatStrip, Footer components"
```

---

## Task 6: UI — SearchBar + TimelineRiver

**Files:**
- Modify: `mcp-server/ui.html`

- [ ] **Step 1: Add SearchBar and TimelineRiver components**

In `mcp-server/ui.html`, inside the `<script type="text/babel">` block, insert these two components before the `// ── App` comment:

```jsx
// ── SearchBar ─────────────────────────────────────────────────────────────────
const SUGGESTIONS = ['"JWT auth bug"', '"database migration"', '"preferred test style"', '"deploy workflow"'];

function SearchBar({ query, onSearch, hitCount, latencyMs }) {
  const [draft, setDraft] = useState(query);
  const inputRef = useRef(null);

  // ⌘K / Ctrl+K focuses the search input
  useEffect(() => {
    function handler(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); inputRef.current?.focus(); }
    }
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  function submit(q) { setDraft(q); onSearch(q); }

  return (
    <div style={{ padding: '32px 32px 0' }}>
      <div style={{ border: '1.5px solid #312888', background: '#fff', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#312888" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input ref={inputRef} value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit(draft)}
          placeholder="Search memories semantically…"
          style={{ flex: 1, border: 'none', outline: 'none', fontFamily: 'Mulish', fontSize: 14, fontWeight: 500, color: '#312888', background: 'transparent' }} />
        <span className="mono" style={{ color: '#009CFF', fontSize: 11 }}>↵</span>
        {hitCount != null && (
          <span className="mono" style={{ color: 'var(--cs-grey)', fontSize: 11, marginLeft: 8 }}>
            {hitCount} hits{latencyMs ? ` · ${latencyMs}ms` : ''} · cos≥0.78
          </span>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
        <Eyebrow style={{ fontSize: 10 }}>Try:</Eyebrow>
        {SUGGESTIONS.map(s => (
          <button key={s} onClick={() => submit(s.replace(/"/g, ''))}
            style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--cs-grey)', border: '1px solid var(--border-subtle)', borderRadius: 3, padding: '2px 10px', background: 'var(--cs-grey-light)', cursor: 'pointer' }}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── TimelineRiver ─────────────────────────────────────────────────────────────
function TimelineRiver({ memories, projects, filter, onFilterChange, onSelect, selectedId }) {
  const W = 1100, H = 320;
  const padL = 80, padR = 30, padT = 30, padB = 40;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  // Determine date range (last 21 days)
  const now = Date.now();
  const rangeMs = 21 * 24 * 3600 * 1000;
  const minDate = now - rangeMs;

  // Filter memories to range
  const inRange = memories.filter(m => new Date(m.created_at).getTime() >= minDate);

  // Distinct projects (up to 8)
  const projNames = [...new Set(inRange.map(m => m.project || '(none)'))].slice(0, 8);
  const laneH = projNames.length > 0 ? chartH / projNames.length : chartH;

  function xPos(dateStr) {
    const t = new Date(dateStr).getTime();
    return padL + ((t - minDate) / rangeMs) * chartW;
  }
  function yCenter(projName) {
    const i = projNames.indexOf(projName);
    return padT + i * laneH + laneH / 2;
  }
  function dotRadius(contentLen) {
    return Math.max(6, Math.min(16, 6 + (contentLen / 100)));
  }

  // Build bezier path through points for each project
  function riverPath(pts) {
    if (pts.length < 2) return null;
    const sorted = [...pts].sort((a, b) => a.x - b.x);
    let d = `M ${sorted[0].x} ${sorted[0].y}`;
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1], curr = sorted[i];
      const cpx = (prev.x + curr.x) / 2;
      d += ` Q ${cpx} ${prev.y} ${curr.x} ${curr.y}`;
    }
    return d;
  }

  // X-axis week ticks
  const tickDates = [];
  for (let i = 0; i <= 3; i++) tickDates.push(new Date(minDate + i * 7 * 24 * 3600 * 1000));

  const filtered = filter === 'all' ? inRange : inRange.filter(m => (m.project || '(none)') === filter);

  return (
    <div style={{ background: 'var(--cs-grey-light)', padding: '20px 32px', borderTop: '1px solid var(--border-subtle)', borderBottom: '1px solid var(--border-subtle)' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <Eyebrow>Timeline River</Eyebrow>
          <div className="mono" style={{ color: 'var(--cs-grey)', marginTop: 3, fontSize: 11 }}>
            21 days · {inRange.length} memories · lane = project
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {['all', ...projNames].map(p => {
            const active = filter === p;
            const color = p === 'all' ? '#312888' : projectColor(p);
            return (
              <button key={p} onClick={() => onFilterChange(p)}
                style={{ fontFamily: 'Mulish', fontSize: 10, fontWeight: 700, borderRadius: 12, padding: '3px 12px', border: `1.5px solid ${color}`, background: active ? color : 'transparent', color: active ? '#fff' : color, cursor: 'pointer', transition: 'all 150ms' }}>
                {p === 'all' ? 'All' : p}
              </button>
            );
          })}
        </div>
      </div>
      {/* SVG */}
      <div style={{ background: '#fff', border: '1px solid var(--border-subtle)', borderRadius: 2, padding: '4px 0' }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
          {/* Lane backgrounds + labels */}
          {projNames.map((proj, i) => {
            const y = padT + i * laneH;
            const color = projectColor(proj);
            const pts = filtered.filter(m => (m.project || '(none)') === proj)
              .map(m => ({ x: xPos(m.created_at), y: yCenter(proj) }));
            const path = riverPath(pts);
            return (
              <g key={proj}>
                {i % 2 === 0 && <rect x={padL} y={y} width={chartW} height={laneH} fill={`${color}08`} />}
                <text x={padL - 6} y={y + laneH / 2} textAnchor="end" dominantBaseline="middle"
                  style={{ fontSize: 9, fontWeight: 700, fontFamily: 'monospace', textTransform: 'uppercase', fill: '#312888', letterSpacing: '0.06em' }}>
                  {proj.length > 10 ? proj.slice(0, 10) + '…' : proj}
                </text>
                {/* River curve */}
                {path && <path d={path} stroke={color} strokeWidth={22} fill="none" opacity={0.14} strokeLinecap="round" />}
                {path && <path d={path} stroke={color} strokeWidth={1.5} fill="none" opacity={0.4} strokeLinecap="round" />}
              </g>
            );
          })}
          {/* Dots */}
          {filtered.map(m => {
            const proj = m.project || '(none)';
            const x = xPos(m.created_at);
            const y = yCenter(proj);
            const r = dotRadius(m.content_length || 0);
            const color = projectColor(proj);
            const selected = m.id === selectedId;
            return (
              <g key={m.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(m.id)}
                tabIndex={0} role="button" aria-label={m.title}
                onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && onSelect(m.id)}>
                {selected && <circle cx={x} cy={y} r={r + 10} fill={color} opacity={0.22} />}
                <circle cx={x} cy={y} r={r} fill={color} stroke="#fff" strokeWidth={selected ? 3 : 2} />
              </g>
            );
          })}
          {/* X-axis ticks */}
          {tickDates.map((d, i) => {
            const x = padL + (i / 3) * chartW;
            const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            return (
              <g key={i}>
                <line x1={x} y1={padT} x2={x} y2={H - padB} stroke="rgba(49,40,136,0.08)" strokeWidth={1} />
                <text x={x} y={H - padB + 14} textAnchor="middle" style={{ fontSize: 9, fill: 'var(--cs-grey)', fontFamily: 'monospace' }}>{label}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontFamily: 'monospace', fontSize: 9, color: 'var(--cs-grey)' }}>
        <span>Lanes group by project · x = time · dot size = content length</span>
        <span>Click any dot to load</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire SearchBar and TimelineRiver into App**

In the `App` function, replace the state declarations and body with:

```jsx
function App() {
  const [section, setSection]         = useState('timeline');
  const [stats, setStats]             = useState(null);
  const [healthy, setHealthy]         = useState(true);
  const [latency, setLatency]         = useState('—');
  const [error, setError]             = useState(null);
  const [memories, setMemories]       = useState([]);
  const [projects, setProjects]       = useState([]);
  const [filter, setFilter]           = useState('all');
  const [selectedId, setSelectedId]   = useState(null);
  const [query, setQuery]             = useState('');
  const [searchResults, setResults]   = useState([]);
  const [searchMeta, setSearchMeta]   = useState(null); // {count, ms}
  const detailRef = useRef(null);

  useEffect(() => {
    const t0 = Date.now();
    fetch('/health').then(r => r.json()).then(d => { setHealthy(d.status === 'ok'); setLatency(Date.now() - t0); }).catch(() => setHealthy(false));
    fetch('/api/stats').then(r => r.json()).then(setStats).catch(e => setError(String(e)));
    fetch('/api/memories?limit=500').then(r => r.json()).then(setMemories).catch(() => {});
    fetch('/api/projects').then(r => r.json()).then(setProjects).catch(() => {});
    // Default list
    fetch('/api/memories?limit=20').then(r => r.json()).then(rows => setResults(rows.map(m => ({ ...m, sim: null })))).catch(() => {});
  }, []);

  async function handleSearch(q) {
    setQuery(q);
    if (!q.trim()) {
      const rows = await fetch('/api/memories?limit=20').then(r => r.json()).catch(() => []);
      setResults(rows.map(m => ({ ...m, sim: null })));
      setSearchMeta(null);
      return;
    }
    const t0 = Date.now();
    const rows = await fetch('/api/recall', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: q, threshold: 0.78 }) }).then(r => r.json()).catch(() => []);
    setResults(Array.isArray(rows) ? rows : []);
    setSearchMeta({ count: rows.length, ms: Date.now() - t0 });
  }

  function handleSelect(id) {
    setSelectedId(id);
    setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  }

  return (
    <div className="page-container" style={{ background: '#fff', minHeight: '100vh' }}>
      {error && <div style={{ background: '#fff0ed', borderBottom: '2px solid var(--cs-coral)', padding: '10px 32px', fontSize: 13, color: '#c0392b' }}>Database unreachable. Check docker compose is running. ({error})</div>}
      <AppHeader activeSection={section} onNav={setSection} healthy={healthy} latency={latency} />
      <StatStrip stats={stats} />
      <SearchBar query={query} onSearch={handleSearch} hitCount={searchMeta?.count} latencyMs={searchMeta?.ms} />
      <TimelineRiver memories={memories} projects={projects} filter={filter} onFilterChange={setFilter} onSelect={handleSelect} selectedId={selectedId} />
      <div ref={detailRef} style={{ padding: '40px 32px', color: 'var(--cs-grey)', fontFamily: 'monospace', fontSize: 12, textAlign: 'center' }}>
        [Two-col body, Preferences, Settings — built in Tasks 7–9]
      </div>
      <Footer stats={stats} />
    </div>
  );
}
```

- [ ] **Step 3: Rebuild and verify in browser**

```bash
cd /Users/daringanitch/workspace/claude-memory
docker compose build mcp-server && docker compose up -d mcp-server
sleep 3
open http://localhost:3333/ui
```

Expected: search bar with suggestion chips, timeline river with project lanes and dots, ⌘K focuses search.

- [ ] **Step 4: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html
git commit -m "feat: UI SearchBar and TimelineRiver SVG components"
```

---

## Task 7: UI — SearchResults + MemoryDetail + MemoryReader overlay

**Files:**
- Modify: `mcp-server/ui.html`

- [ ] **Step 1: Add SearchResults, MemoryDetail, MemoryReader components**

In `mcp-server/ui.html`, inside the babel script block, insert before the `// ── App` comment:

```jsx
// ── SearchResults ─────────────────────────────────────────────────────────────
function SearchResults({ results, selectedId, onSelect, query }) {
  if (results.length === 0 && query) {
    return (
      <div>
        <Eyebrow style={{ marginBottom: 10 }}>Search Results</Eyebrow>
        <div style={{ border: '1px dashed var(--border-subtle)', background: 'var(--cs-grey-light)', padding: '24px 16px', textAlign: 'center', color: 'var(--cs-grey)', fontSize: 13 }}>
          No matches above 0.78 similarity. Try a broader query.
        </div>
      </div>
    );
  }
  return (
    <div>
      <Eyebrow style={{ marginBottom: 2 }}>Search Results</Eyebrow>
      {query && <div className="mono" style={{ color: 'var(--cs-grey)', fontSize: 11, marginBottom: 10 }}>"{query}"</div>}
      {results.map((r, i) => {
        const active = r.id === selectedId;
        const color = projectColor(r.project || '(none)');
        return (
          <div key={r.id} onClick={() => onSelect(r.id)} role="button" tabIndex={0}
            onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && onSelect(r.id)}
            style={{ padding: '12px 0', borderTop: i === 0 ? '2px solid #312888' : '1px solid var(--border-subtle)', cursor: 'pointer' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }} />
                <span className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{r.project || '(none)'}</span>
                <span className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)' }}>· {r.id} · {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</span>
              </div>
              {r.sim != null && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <MeterBar value={r.sim} />
                  <span className="mono" style={{ fontSize: 11, color: 'var(--cs-grey)' }}>{r.sim.toFixed(2)}</span>
                </div>
              )}
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: active ? '#009CFF' : '#312888', marginBottom: 3 }}>{r.title}</div>
            {r.snippet && <div style={{ fontSize: 13, fontStyle: 'italic', color: 'var(--cs-grey)', lineHeight: 1.5 }}>{r.snippet.slice(0, 140)}{r.snippet.length > 140 ? '…' : ''}</div>}
          </div>
        );
      })}
    </div>
  );
}

// ── MemoryDetail ──────────────────────────────────────────────────────────────
function RelatedMemories({ items }) {
  if (!items || items.length === 0) return <div style={{ color: 'var(--cs-grey)', fontSize: 13 }}>No related memories found.</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map(r => {
        const ks = kindStyle(r.tags || []);
        return (
          <div key={r.id} style={{ borderLeft: `2px solid ${ks.border}`, background: ks.bg, padding: '6px 10px', borderRadius: '0 2px 2px 0' }}>
            <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: ks.border }}>{ks.label}</span>
            <div style={{ marginTop: 3, fontSize: 12, lineHeight: 1.5, color: 'var(--fg-2)' }}>{r.title}</div>
          </div>
        );
      })}
    </div>
  );
}

function MemoryDetail({ memory, related, onOpenReader }) {
  if (!memory) {
    return (
      <div style={{ background: 'rgba(49,40,136,0.03)', border: '1px solid var(--border-subtle)', height: '100%', minHeight: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
        <div style={{ textAlign: 'center', color: 'var(--cs-grey)' }}>
          <div style={{ fontSize: 13 }}>Select a memory from the timeline or search results.</div>
        </div>
      </div>
    );
  }
  const fileTags = (memory.tags || []).filter(t => t.startsWith('file:'));
  const otherTags = (memory.tags || []).filter(t => !t.startsWith('file:'));
  const dateStr = memory.created_at ? new Date(memory.created_at).toLocaleString() : '—';
  return (
    <div>
      <Eyebrow>{memory.project || '(none)'}</Eyebrow>
      <div className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)', marginBottom: 6 }}>memory://{memory.id}</div>
      <h2 style={{ fontSize: 22, fontWeight: 700, color: '#312888', letterSpacing: '-0.01em', marginBottom: 8 }}>{memory.title}</h2>
      {/* Meta strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderTop: '2px solid #312888', borderBottom: '1px solid var(--border-subtle)', margin: '8px 0 12px' }}>
        {[['Project', memory.project || '—'], ['Date', dateStr], ['Length', `${memory.content_length} ch`], ['Tags', memory.tags?.length ?? 0]].map(([label, val], i) => (
          <div key={label} style={{ padding: '8px 8px', borderRight: i < 3 ? '1px solid var(--border-subtle)' : 'none' }}>
            <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#009CFF' }}>{label}</div>
            <div className="mono" style={{ fontSize: 11, color: '#312888', marginTop: 2 }}>{val}</div>
          </div>
        ))}
      </div>
      {/* Content */}
      <p style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--fg-2)', marginBottom: 16 }}>{memory.content}</p>
      {/* Related */}
      <Eyebrow style={{ marginBottom: 8 }}>Related Memories</Eyebrow>
      <RelatedMemories items={related} />
      {/* File tags */}
      {fileTags.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Eyebrow style={{ marginBottom: 6 }}>Files</Eyebrow>
          {fileTags.map(t => <span key={t} style={{ fontFamily: 'monospace', fontSize: 11, background: 'var(--cs-grey-light)', color: '#312888', padding: '2px 8px', borderRadius: 2, margin: '2px', display: 'inline-block' }}>{t.replace('file:', '')}</span>)}
        </div>
      )}
      {/* Other tags */}
      {otherTags.length > 0 && <div style={{ marginTop: 12 }}>{otherTags.map(t => <span key={t} className="tag-pill">{t}</span>)}</div>}
      {/* Actions */}
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button className="btn-primary" onClick={onOpenReader}>Read full memory →</button>
        <button className="btn-outline" onClick={() => navigator.clipboard.writeText(`memory://${memory.id}`)}>Copy ID</button>
      </div>
    </div>
  );
}

// ── MemoryReader overlay ──────────────────────────────────────────────────────
function MemoryReader({ memory, related, onClose }) {
  const panelRef = useRef(null);

  // Focus trap + ESC
  useEffect(() => {
    panelRef.current?.focus();
    function handler(e) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  if (!memory) return null;
  const dateStr = memory.created_at ? new Date(memory.created_at).toLocaleString() : '—';
  const html = typeof marked !== 'undefined' ? marked.parse(memory.content || '') : memory.content;

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,10,61,0.55)', zIndex: 50, overflowY: 'auto', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 20px' }}>
      <div ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Full memory reader"
        onClick={e => e.stopPropagation()}
        style={{ background: '#fff', maxWidth: 760, width: '100%', boxShadow: 'var(--shadow-lg)', padding: '24px 28px', outline: 'none' }}>
        {/* Sticky header inside panel */}
        <div style={{ borderBottom: '2px solid #312888', paddingBottom: 14, marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Eyebrow>Full Memory · {memory.id}</Eyebrow>
            <h3 style={{ fontSize: 22, fontWeight: 700, color: '#312888', marginBottom: 10, marginTop: 4 }}>{memory.title}</h3>
            <div className="mono" style={{ fontSize: 11, color: 'var(--cs-grey)' }}>{memory.project} · {dateStr} · {memory.content_length} chars · {memory.tags?.length ?? 0} tags</div>
          </div>
          <button className="btn-outline" onClick={onClose} aria-label="Close memory reader" style={{ marginLeft: 16, flexShrink: 0 }}>✕ Close</button>
        </div>
        {/* Content */}
        <div style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--fg-2)' }} dangerouslySetInnerHTML={{ __html: html }} />
        {/* Tags */}
        {(memory.tags || []).length > 0 && <div style={{ marginTop: 20 }}>{memory.tags.map(t => <span key={t} className="tag-pill">{t}</span>)}</div>}
        {/* Related */}
        {related?.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <Eyebrow style={{ marginBottom: 10 }}>Related Memories</Eyebrow>
            <RelatedMemories items={related} />
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire two-col body + reader into App**

In the `App` function, add state and replace the placeholder `[Two-col body...]` div with:

Add to state:
```jsx
const [selectedMemory, setSelectedMemory] = useState(null);
const [related, setRelated]               = useState([]);
const [readerOpen, setReaderOpen]         = useState(false);
```

Replace the `handleSelect` function with:
```jsx
async function handleSelect(id) {
  setSelectedId(id);
  const mem = await fetch(`/api/memories/${id}`).then(r => r.json()).catch(() => null);
  setSelectedMemory(mem);
  const rel = await fetch(`/api/memories/${id}/related`).then(r => r.json()).catch(() => []);
  setRelated(Array.isArray(rel) ? rel : []);
  setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}
```

Replace the placeholder div with:
```jsx
<div ref={detailRef} style={{ padding: '32px 32px', display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: 32, borderBottom: '1px solid var(--border-subtle)' }}>
  <SearchResults results={searchResults} selectedId={selectedId} onSelect={handleSelect} query={query} />
  <div style={{ borderLeft: '1px solid var(--border-subtle)', paddingLeft: 24 }}>
    <MemoryDetail memory={selectedMemory} related={related} onOpenReader={() => setReaderOpen(true)} />
  </div>
</div>
```

Add just before `<Footer>`:
```jsx
{readerOpen && <MemoryReader memory={selectedMemory} related={related} onClose={() => setReaderOpen(false)} />}
```

- [ ] **Step 3: Rebuild and verify**

```bash
cd /Users/daringanitch/workspace/claude-memory
docker compose build mcp-server && docker compose up -d mcp-server
sleep 3
open http://localhost:3333/ui
```

Expected: click a timeline dot → detail pane populates with content, related memories, tags, "Read full memory" button opens overlay. ESC closes overlay.

- [ ] **Step 4: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html
git commit -m "feat: UI SearchResults, MemoryDetail, MemoryReader overlay"
```

---

## Task 8: UI — PreferencesSection

**Files:**
- Modify: `mcp-server/ui.html`

- [ ] **Step 1: Add PreferencesSection component**

Insert before `// ── App`:

```jsx
// ── PreferencesSection ────────────────────────────────────────────────────────
function PrefCard({ category, items }) {
  return (
    <div style={{ background: '#fff', border: '1px solid var(--border-subtle)', padding: '16px 20px' }}>
      <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#009CFF', borderBottom: '2px solid #312888', paddingBottom: 8, marginBottom: 10 }}>
        {category}
      </div>
      {items.map((item, i) => (
        <div key={i} style={{ fontSize: 13, color: 'var(--fg-2)', padding: '6px 0', borderBottom: i < items.length - 1 ? '1px solid rgba(49,40,136,0.07)' : 'none', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <span style={{ lineHeight: 1.45, flex: 1 }}>{item.text}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
            <MeterBar value={item.confidence} color={item.confidence >= 0.9 ? '#009CFF' : 'rgba(0,156,255,0.4)'} width={48} />
            <span className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)', minWidth: 28 }}>{Math.round(item.confidence * 100)}%</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)' }}>{item.source}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function PreferencesSection({ prefs }) {
  const empty = !prefs || prefs.length === 0;
  return (
    <section id="preferences" style={{ background: 'var(--cs-grey-light)', padding: '44px 32px', borderTop: '1px solid var(--border-subtle)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <Eyebrow>Learned Preferences</Eyebrow>
          <h2 style={{ fontSize: 24, fontWeight: 700, color: '#312888', letterSpacing: '-0.01em', marginTop: 6 }}>
            What Claude has noticed about{' '}
            <em style={{ fontFamily: 'Cardo, Georgia, serif', fontStyle: 'italic', fontWeight: 400, color: 'var(--cs-grey)' }}>how you work.</em>
          </h2>
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--cs-grey)', textAlign: 'right', marginTop: 4 }}>
          inferred · auto-updates
        </div>
      </div>
      {empty ? (
        <div style={{ border: '1px dashed var(--border-subtle)', background: '#fff', padding: '32px 24px', textAlign: 'center' }}>
          <div style={{ color: 'var(--cs-grey)', fontSize: 13 }}>No inferred preferences yet.</div>
          <div className="mono" style={{ marginTop: 8, fontSize: 12, color: 'var(--cs-grey)' }}>Run <code style={{ background: 'var(--cs-grey-light)', padding: '1px 6px', borderRadius: 2 }}>python extract_signals.py</code> to populate.</div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {prefs.map(g => <PrefCard key={g.category} category={g.category} items={g.items} />)}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Add prefs state + PreferencesSection to App**

Add to App state:
```jsx
const [prefs, setPrefs] = useState([]);
```

Add to the `useEffect` data-fetch block:
```jsx
fetch('/api/preferences').then(r => r.json()).then(d => setPrefs(Array.isArray(d) ? d : [])).catch(() => {});
```

Add `<PreferencesSection prefs={prefs} />` after the two-col body div and before `{readerOpen && ...}`.

- [ ] **Step 3: Rebuild and verify**

```bash
cd /Users/daringanitch/workspace/claude-memory
docker compose build mcp-server && docker compose up -d mcp-server && sleep 3
open http://localhost:3333/ui
```

Expected: preferences section visible with cards grouped by category, confidence bars, or empty-state hint if no `type:preference` memories exist.

- [ ] **Step 4: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html
git commit -m "feat: UI PreferencesSection with PrefCard and empty state"
```

---

## Task 9: UI — SettingsSection

**Files:**
- Modify: `mcp-server/ui.html`

- [ ] **Step 1: Add SettingsSection component**

Insert before `// ── App`:

```jsx
// ── SettingsSection ───────────────────────────────────────────────────────────
const RETENTION_OPTIONS = ['30d', '90d', '1yr', 'forever'];
const STORAGE_LABELS = [
  { key: 'embeddings_mb', label: 'Embeddings / pgvector', color: '#312888' },
  { key: 'content_mb',    label: 'Memory content',        color: '#009CFF' },
  { key: 'metadata_mb',   label: 'Metadata + index',      color: '#FF947B' },
];

function Toggle({ on, onChange, locked }) {
  return (
    <button onClick={locked ? undefined : () => onChange(!on)} aria-pressed={on} aria-label={locked ? 'Always on' : on ? 'On' : 'Off'}
      style={{ width: 36, height: 18, borderRadius: 9, background: locked ? 'rgba(255,148,123,0.3)' : on ? '#312888' : 'rgba(49,40,136,0.15)', border: 'none', position: 'relative', cursor: locked ? 'not-allowed' : 'pointer', flexShrink: 0, transition: 'background 150ms' }}>
      <span style={{ position: 'absolute', top: 2, left: on ? 20 : 2, width: 14, height: 14, borderRadius: '50%', background: '#fff', transition: 'left 150ms var(--ease)' }} />
    </button>
  );
}

function SettingsSection({ stats, onDeleteProject, onWipeAll }) {
  const [retention, setRetention] = useState(() => localStorage.getItem('cm_retention') || '90d');
  const [toggles, setToggles]     = useState(() => ({ content: true, embeddings: true }));
  const [confirmWipe, setConfirmWipe] = useState(false);

  function saveRetention(val) { setRetention(val); localStorage.setItem('cm_retention', val); }
  function flipToggle(key) { setToggles(t => ({ ...t, [key]: !t[key] })); }

  const breakdown = stats?.storage_breakdown || {};
  const totalMb   = stats?.storage_mb ?? 0;
  const totalGB   = 5;
  const fillPct   = totalMb / (totalGB * 1024) * 100;

  return (
    <section id="settings" style={{ padding: '44px 32px', borderTop: '1px solid var(--border-subtle)' }}>
      <Eyebrow>Data Controls</Eyebrow>
      <h2 style={{ fontSize: 24, fontWeight: 700, color: '#312888', letterSpacing: '-0.01em', marginTop: 6, marginBottom: 20 }}>
        Your memory,{' '}
        <em style={{ fontFamily: 'Cardo, Georgia, serif', fontStyle: 'italic', fontWeight: 400, color: 'var(--cs-grey)' }}>your rules.</em>
      </h2>
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 16 }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Retention */}
          <div style={{ border: '1px solid var(--border-subtle)', padding: '16px 20px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#009CFF', marginBottom: 12 }}>Retention</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {RETENTION_OPTIONS.map(opt => (
                <button key={opt} onClick={() => saveRetention(opt)}
                  style={{ fontFamily: 'Mulish', fontSize: 12, padding: '5px 14px', border: retention === opt ? '2px solid #312888' : '1px solid var(--border-subtle)', background: retention === opt ? 'rgba(49,40,136,0.05)' : 'transparent', color: retention === opt ? '#312888' : 'var(--cs-grey)', fontWeight: retention === opt ? 700 : 500, cursor: 'pointer', borderRadius: 0 }}>
                  {opt}
                </button>
              ))}
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--cs-grey)', marginTop: 8 }}>Stored locally in browser — backend retention TBD</div>
          </div>
          {/* What's stored */}
          <div style={{ border: '1px solid var(--border-subtle)', padding: '16px 20px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#009CFF', marginBottom: 12 }}>What's Stored</div>
            {[
              { key: 'content',    label: 'Memory content',   desc: 'Raw text of each memory' },
              { key: 'embeddings', label: 'Embedding vectors', desc: '768-dim pgvector embeddings' },
            ].map(row => (
              <div key={row.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 0', borderBottom: '1px solid rgba(49,40,136,0.07)' }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-2)' }}>{row.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--cs-grey)' }}>{row.desc}</div>
                </div>
                <Toggle on={toggles[row.key]} onChange={() => flipToggle(row.key)} />
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 0', cursor: 'not-allowed', opacity: 0.8 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: '#FF947B' }}>Secrets &amp; tokens</div>
                <div style={{ fontSize: 11, color: 'var(--cs-grey)' }}>Always redacted</div>
              </div>
              <Toggle on={false} locked />
            </div>
          </div>
        </div>
        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Storage */}
          <div style={{ border: '1px solid var(--border-subtle)', padding: '16px 20px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#009CFF', marginBottom: 10 }}>Storage</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: '#312888', fontVariantNumeric: 'tabular-nums' }}>{totalMb} MB</div>
            <div style={{ fontSize: 11, color: 'var(--cs-grey)', marginBottom: 10 }}>of {totalGB} GB local</div>
            <div style={{ height: 6, background: 'rgba(49,40,136,0.08)', borderRadius: 3, marginBottom: 12, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.min(fillPct, 100)}%`, background: '#312888', borderRadius: 3 }} />
            </div>
            {STORAGE_LABELS.map(sl => (
              <div key={sl.key} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, padding: '3px 0', borderBottom: '1px solid rgba(49,40,136,0.07)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--fg-2)' }}>
                  <span style={{ width: 8, height: 8, borderRadius: 1, background: sl.color, display: 'inline-block' }} />{sl.label}
                </span>
                <span className="mono" style={{ color: 'var(--cs-grey)' }}>{breakdown[sl.key] ?? '—'} MB</span>
              </div>
            ))}
          </div>
          {/* Danger zone */}
          <div style={{ border: '2px solid var(--cs-coral)', padding: '16px 20px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#FF947B', marginBottom: 12 }}>Danger Zone</div>
            {/* Export */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 10, marginBottom: 10, borderBottom: '1px solid rgba(49,40,136,0.08)' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-2)' }}>Export all memories</div>
                <div style={{ fontSize: 11, color: 'var(--cs-grey)' }}>Download as JSON</div>
              </div>
              <button className="btn-outline" onClick={() => window.open('/api/memories?limit=10000', '_blank')}>Export</button>
            </div>
            {/* Delete project */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 10, marginBottom: 10, borderBottom: '1px solid rgba(49,40,136,0.08)' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-2)' }}>Delete project</div>
                <div style={{ fontSize: 11, color: 'var(--cs-grey)' }}>Remove all memories for a project</div>
              </div>
              <button className="btn-outline" onClick={() => { const p = window.prompt('Project name to delete:'); if (p) onDeleteProject(p); }}>Delete</button>
            </div>
            {/* Wipe */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#FF947B' }}>Wipe all memory</div>
                <div style={{ fontSize: 11, color: 'var(--cs-grey)' }}>Permanently deletes everything</div>
              </div>
              {confirmWipe ? (
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn-danger" onClick={() => { onWipeAll(); setConfirmWipe(false); }}>Confirm wipe</button>
                  <button className="btn-outline" onClick={() => setConfirmWipe(false)}>Cancel</button>
                </div>
              ) : (
                <button className="btn-danger" onClick={() => setConfirmWipe(true)}>Wipe</button>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Wire SettingsSection into App**

Add after `<PreferencesSection>`:
```jsx
<SettingsSection
  stats={stats}
  onDeleteProject={async (project) => {
    await fetch(`/api/memories?project=${encodeURIComponent(project)}`, { method: 'DELETE' });
    alert(`Deleted memories for project: ${project}`);
  }}
  onWipeAll={async () => {
    // Wipe requires iterating projects since bulk_delete requires at least one filter
    const projs = await fetch('/api/projects').then(r => r.json()).catch(() => []);
    for (const p of projs) {
      await fetch(`/api/memories?project=${encodeURIComponent(p.project)}`, { method: 'DELETE' });
    }
    alert('All memories wiped.');
    window.location.reload();
  }}
/>
```

- [ ] **Step 3: Rebuild and verify**

```bash
cd /Users/daringanitch/workspace/claude-memory
docker compose build mcp-server && docker compose up -d mcp-server && sleep 3
open http://localhost:3333/ui
```

Expected: settings section visible with retention radio, storage breakdown bar, what's-stored toggles (locked secrets row), danger zone with confirm flow on Wipe.

- [ ] **Step 4: Commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html
git commit -m "feat: UI SettingsSection — retention, storage, toggles, danger zone"
```

---

## Task 10: Final polish — accessibility, error states, full test run

**Files:**
- Modify: `mcp-server/ui.html` (minor a11y additions)
- Run: full test suite

- [ ] **Step 1: Verify all aria attributes are in place**

Open `mcp-server/ui.html` and confirm:
- `MemoryReader` has `role="dialog"`, `aria-modal="true"`, `aria-label="Full memory reader"` — already added in Task 7. ✓
- `Toggle` button has `aria-pressed` and `aria-label` — already added in Task 9. ✓
- River dots have `role="button"`, `tabIndex={0}`, `aria-label={m.title}` — already added in Task 6. ✓
- Search result rows have `role="button"`, `tabIndex={0}` — already added in Task 7. ✓
- AppHeader nav buttons have accessible text — already in place. ✓

If any are missing, add them now following the existing patterns in those components.

- [ ] **Step 2: Add global keyboard shortcut for ⌘K**

Confirm the `useEffect` in `SearchBar` wires `window.addEventListener('keydown', handler)` for `metaKey/ctrlKey + k`. This was added in Task 6. ✓

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/daringanitch/workspace/claude-memory
pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests PASS. Zero failures.

- [ ] **Step 4: Smoke-test the full UI**

```bash
docker compose build mcp-server && docker compose up -d mcp-server
sleep 5
# Check all REST endpoints
curl -s http://localhost:3333/api/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print('stats ok, active:', d['active'])"
curl -s http://localhost:3333/api/projects | python3 -c "import sys,json; d=json.load(sys.stdin); print('projects ok:', len(d))"
curl -s "http://localhost:3333/api/memories?limit=3" | python3 -c "import sys,json; d=json.load(sys.stdin); print('memories ok:', len(d))"
curl -s -X POST http://localhost:3333/api/recall -H 'Content-Type: application/json' -d '{"query":"test","threshold":0.1}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('recall ok:', type(d))"
curl -s http://localhost:3333/api/preferences | python3 -c "import sys,json; d=json.load(sys.stdin); print('prefs ok:', len(d))"
curl -s http://localhost:3333/ui | grep -c "Claude Memory"
```

Expected: each endpoint prints "ok", last command prints `1` (ui.html title found).

- [ ] **Step 5: Add .superpowers to .gitignore**

```bash
cd /Users/daringanitch/workspace/claude-memory
echo '.superpowers/' >> .gitignore
git add .gitignore
```

- [ ] **Step 6: Final commit**

```bash
cd /Users/daringanitch/workspace/claude-memory
git add mcp-server/ui.html .gitignore
git commit -m "feat: Claude Memory UI — full implementation complete

Single-file React UI served at GET /ui with 10 REST endpoints:
- Timeline River SVG with project lanes and dot-size encoding
- Semantic search via POST /api/recall
- Memory detail pane with related memories
- Full memory reader overlay (markdown-rendered, ESC to close)
- Learned preferences grouped by category tag
- Data controls: retention, storage breakdown, danger zone
- Keyboard: ⌘K to focus search, ESC to close overlay
- Accessibility: role=dialog, aria-modal, aria-label, focus management"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| AppHeader (logo, nav, health pill) | Task 5 |
| StatStrip (6 cells from /api/stats) | Task 5 |
| Footer (2px indigo top border, 3 mono spans) | Task 5 |
| SearchBar with ⌘K, suggestion chips | Task 6 |
| TimelineRiver SVG, lanes, dots, bezier, filter pills | Task 6 |
| SearchResults with similarity bars | Task 7 |
| MemoryDetail (meta strip, related memories, tags, actions) | Task 7 |
| MemoryReader overlay (markdown, ESC, focus trap) | Task 7 |
| PreferencesSection (grouped by category, confidence bars, empty state) | Task 8 |
| SettingsSection (retention, storage, toggles, danger zone, confirm flow) | Task 9 |
| `_api_projects`, `_api_tags`, `_api_stats` | Task 1 |
| `_api_list_memories`, `_api_get_memory`, `_api_related_memories` | Task 2 |
| `_api_recall`, `_api_preferences`, `_api_bulk_delete` | Task 3 |
| Route handlers + `GET /ui` | Task 4 |
| Tests for all `_api_*` helpers | Tasks 1–3 |
| .gitignore .superpowers/ | Task 10 |
| Design tokens as CSS custom properties | Task 4 |
| MeterBar, Eyebrow, TagPill shared primitives | Task 4 |
| projectColor palette + kindStyle mapping | Task 4 |

All spec requirements covered. No gaps found.
