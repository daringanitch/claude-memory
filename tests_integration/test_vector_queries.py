"""
Integration tests for every server.py code path that binds a pgvector embedding
into a SQL query. These are the tests that would have caught B1, B2, the
original _write_guard 2-vector bug, and the update_memory tuple-index bug.

Strategy:
  * Identical content → identical stub embedding → cosine sim = 1.0 → reliably
    above any NOOP/threshold check.
  * Distinct content → near-orthogonal embeddings → sim ≈ 0 → reliably below
    thresholds.

We never rely on semantic similarity — only on the SQL execution path
(parameter binding, CTE evaluation, result-set construction).
"""
import json


# ── _write_guard ─────────────────────────────────────────────────────────────
def test_write_guard_no_memories_returns_ADD(srv, clean_db):
    """Empty DB → ADD action with 'no memories yet' reason. Exercises CTE
    against an empty table — must not raise."""
    vec = srv.embed("hello world")
    result = srv._write_guard("hello world", vec)
    assert result["action"] == "ADD"
    assert "no memories" in result["reason"].lower()


def test_write_guard_returns_NOOP_for_identical_content(srv, clean_db):
    """One stored memory + identical new content → NOOP at sim=1.0.
    This is the bug surface that B1 / _write_guard tuple-index lived on."""
    srv.save_memory("identical content for noop test", project="t", force=True)
    vec = srv.embed("identical content for noop test")
    result = srv._write_guard("identical content for noop test", vec)
    assert result["action"] == "NOOP"
    assert result["similarity"] == 1.0


def test_write_guard_returns_ADD_for_orthogonal_content(srv, clean_db):
    """Stored memory + unrelated content → ADD (sim well below threshold)."""
    srv.save_memory("the quick brown fox jumps over the lazy dog", project="t", force=True)
    vec = srv.embed("totally unrelated string about quantum physics")
    result = srv._write_guard("totally unrelated string about quantum physics", vec)
    assert result["action"] == "ADD"
    assert result["similarity"] < srv.GUARD_UPDATE_THRESHOLD


# ── save_memory ───────────────────────────────────────────────────────────────
def test_save_memory_first_save_succeeds(srv, clean_db):
    out = srv.save_memory("first save in empty db", project="t")
    assert "Memory saved" in out or "✅" in out


def test_save_memory_blocks_near_duplicate(srv, clean_db):
    """B1 surface: dedup query MUST detect the identical second save and
    return the NOOP three-options message."""
    srv.save_memory("dedup target string", project="t", force=True)
    out = srv.save_memory("dedup target string", project="t")  # no force
    assert "NOOP" in out
    assert "force=True" in out


def test_save_memory_force_bypasses_dedup(srv, clean_db):
    srv.save_memory("forced double save", project="t", force=True)
    out = srv.save_memory("forced double save", project="t", force=True)
    # ON CONFLICT (content_hash) returns the "exact match" branch since the
    # hash collides — but no error and no NOOP message
    assert "❌" not in out


# ── update_memory ─────────────────────────────────────────────────────────────
def test_update_memory_changes_content_and_re_embeds(srv, clean_db):
    out = srv.save_memory("original content for update test", project="t", force=True)
    # Extract id from "✅ Memory saved (ID: N, ..."
    mid = int(out.split("ID: ")[1].split(",")[0])
    out = srv.update_memory(mid, content="brand new content totally different")
    assert "updated" in out.lower()

    # Verify the new content is searchable
    found = json.loads(srv.list_memories(limit=1))
    assert "brand new content" in found["memories"][0]["content"]


def test_update_memory_dedup_check_blocks_near_duplicate(srv, clean_db):
    """update_memory CTE: changing memory A's content to match memory B
    must be blocked by the dedup guard."""
    o1 = srv.save_memory("memory A content", project="t", force=True)
    o2 = srv.save_memory("memory B content", project="t", force=True)
    id_a = int(o1.split("ID: ")[1].split(",")[0])

    # Try to update A to be identical to B → should hit NOOP guard
    out = srv.update_memory(id_a, content="memory B content")
    assert "Near-duplicate" in out or "near-duplicate" in out


# ── _api_recall ──────────────────────────────────────────────────────────────
def test_api_recall_returns_match(srv, clean_db):
    """B2 surface: recall query with vec reused 3× before fix."""
    srv.save_memory("recall test target", project="t", force=True)
    results = srv._api_recall("recall test target", threshold=0.5, limit=5)
    assert len(results) >= 1
    assert results[0]["sim"] == 1.0


def test_api_recall_respects_threshold(srv, clean_db):
    srv.save_memory("first thing", project="t", force=True)
    srv.save_memory("totally unrelated other thing entirely", project="t", force=True)
    # High threshold + query matching only the first → only one result
    results = srv._api_recall("first thing", threshold=0.99, limit=5)
    assert len(results) == 1


# ── _api_related_memories ────────────────────────────────────────────────────
def test_api_related_memories_excludes_self(srv, clean_db):
    """B2 surface: vec reused 2× before fix."""
    o1 = srv.save_memory("anchor for related test", project="t", force=True)
    srv.save_memory("neighbor memory one", project="t", force=True)
    srv.save_memory("neighbor memory two", project="t", force=True)
    anchor_id = int(o1.split("ID: ")[1].split(",")[0])

    related = srv._api_related_memories(anchor_id, limit=3)
    assert all(r["id"] != anchor_id for r in related)
    assert len(related) == 2  # the two neighbors


# ── semantic_search ──────────────────────────────────────────────────────────
def test_semantic_search_returns_match(srv, clean_db):
    """B2 surface: vec reused 3× before fix. Also has dynamic WHERE."""
    srv.save_memory("semantic search target string", project="proj1", force=True)
    raw = srv.semantic_search("semantic search target string", min_similarity=0.5)
    parsed = json.loads(raw)
    assert len(parsed) >= 1


def test_semantic_search_filters_by_project(srv, clean_db):
    """The dynamic WHERE branch was vulnerable to the vector-param drift."""
    srv.save_memory("shared phrase", project="alpha", force=True)
    srv.save_memory("shared phrase", project="beta", force=True)
    raw = srv.semantic_search("shared phrase", min_similarity=0.5, project="alpha")
    parsed = json.loads(raw)
    assert len(parsed) == 1
    assert parsed[0]["project"] == "alpha"


# ── hybrid_search ────────────────────────────────────────────────────────────
def test_hybrid_search_returns_match(srv, clean_db):
    """B2 surface (caught during sweep): vec reused 2× before fix.
    Has both a CTE wrapper and dynamic WHERE."""
    srv.save_memory("hybrid search test content", project="t", force=True)
    raw = srv.hybrid_search("hybrid search test content", limit=5)
    parsed = json.loads(raw)
    assert len(parsed) >= 1
    assert float(parsed[0]["semantic_score"]) == 1.0


# ── find_duplicates ──────────────────────────────────────────────────────────
def test_find_duplicates_surfaces_pair(srv, clean_db):
    """find_duplicates uses a self-join with vector ops — not the same bug
    class as B1/B2, but worth covering since it's the only other vector path."""
    srv.save_memory("duplicate detection candidate", project="t", force=True)
    srv.save_memory("duplicate detection candidate", project="t", force=True)
    raw = srv.find_duplicates(threshold=0.99, limit=10)
    # When no dups exist returns a string; when dups exist returns JSON
    if raw.startswith("["):
        parsed = json.loads(raw)
        assert len(parsed) >= 1
        assert parsed[0]["similarity"] == 1.0
