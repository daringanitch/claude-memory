"""
Real-embedder smoke tests — exercise the production pgvector + psycopg2 +
sentence-transformers code path with the actual all-mpnet-base-v2 model
instead of the deterministic stub used in test_vector_queries.py.

Why this file exists:
  The deterministic numpy-float32 stub in conftest.py reliably exercises the
  *SQL* shape of every CTE-using query, but does NOT reproduce the historical
  "silent wrong similarity" symptom that B1/B2 produced under the real
  pgvector adapter. The real bug appears to be sensitive to something about
  the SentenceTransformer output (stride, dtype, memory layout, byte
  representation) that the stub doesn't replicate.

  These tests load the real model and exercise the worst-case binding paths
  on 2026-05-30 (save_memory dedup, _api_recall, semantic_search). Slower:
  ~30 s for the first model load, then ~ms per embed. Acceptable for a
  smoke-test tier.

Self-test note (2026-05-30):
  Even with the real embedder loaded, the tests below PASS against both the
  fixed code (B1+B2 CTE rewrites) AND a deliberately-reverted-to-buggy
  baseline. We have not yet been able to reproduce the original symptom in
  any synthetic test. See AUDIT-REPORT-2026-05-30.md "What I'm most worried
  about" — T-5 covers structural correctness; the historical symptom would
  need a real production-trace replay to reproduce.
"""
import json

import pytest


@pytest.fixture(scope="module", autouse=True)
def real_embedder(srv):
    """Swap the deterministic stub for a real SentenceTransformer for this file only.

    Module-scoped so we pay the ~30 s model load once per pytest run.
    Restores the stub on teardown so other test modules are unaffected.
    """
    from sentence_transformers import SentenceTransformer
    original = srv.embedder
    srv.embedder = SentenceTransformer("all-mpnet-base-v2")
    yield
    srv.embedder = original


# ── save_memory dedup — B1 surface ────────────────────────────────────────────
def test_save_memory_blocks_semantically_near_duplicate(srv, clean_db):
    """Same meaning, different wording: real embedder should put similarity
    above the 0.85 NOOP threshold and the dedup guard should fire."""
    srv.save_memory(
        "garden tomatoes need watering on Tuesdays",
        tags=["type:test"], project="real-embed-t", force=True,
    )
    out = srv.save_memory(
        "tomato plants in the garden need water on Tuesday",
        tags=["type:test"], project="real-embed-t",
    )
    # If the CTE-bound vector path is correct, this should NOOP.
    # If the historical bug returns (dedup silently misses), this would
    # succeed and we'd see "Memory saved" instead.
    assert "NOOP" in out, f"Expected NOOP for near-duplicate; got: {out[:200]}"


# ── _api_recall — B2 surface ──────────────────────────────────────────────────
def test_api_recall_finds_semantic_match(srv, clean_db):
    """Different wording from the saved memory; real embedder should still
    place it above a moderate similarity threshold."""
    srv.save_memory(
        "PostgreSQL pgvector adapter parameter binding bug",
        tags=["type:test"], project="real-embed-t", force=True,
    )
    results = srv._api_recall(
        "how does psycopg2 bind vector params for pgvector?",
        threshold=0.3,
        limit=5,
    )
    assert len(results) >= 1, (
        "Expected ≥1 result for semantically-similar query; "
        "got empty result set (would indicate the historical silent-no-rows bug)"
    )


# ── semantic_search — B2 surface, also exercises dynamic WHERE ────────────────
def test_semantic_search_picks_the_relevant_memory(srv, clean_db):
    """Two unrelated memories; semantic_search should return the one whose
    meaning matches, not the other."""
    srv.save_memory(
        "docker compose health check fails after machine wakes from sleep",
        tags=["type:test"], project="real-embed-t", force=True,
    )
    srv.save_memory(
        "favorite pasta carbonara recipe with guanciale",
        tags=["type:test"], project="real-embed-t", force=True,
    )
    raw = srv.semantic_search(
        "container healthcheck is broken",
        limit=5,
        min_similarity=0.3,
    )
    assert raw.startswith("["), f"Expected JSON array; got: {raw[:100]}"
    parsed = json.loads(raw)
    assert len(parsed) >= 1, "Expected ≥1 result"
    # The docker memory should outrank the pasta one
    top = parsed[0]
    assert "docker" in top["content"].lower(), (
        f"Expected docker-related memory at rank 1; got: {top['content'][:80]}"
    )
