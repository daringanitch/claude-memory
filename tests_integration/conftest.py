"""
Integration test fixtures — run real SQL against real pgvector.

These tests escape the heavy mocking in tests/conftest.py by living in a
separate directory outside tests/. They require:
  * A reachable Postgres with pgvector (hostname `db` by default — works
    from inside the mcp-server container).
  * Permission on the admin DB to CREATE / DROP DATABASE.

Designed to be invoked from inside the mcp-server container (see
Run-IntegrationTests.ps1) so the existing service network resolves `db`
and all runtime deps are already installed.

Per-session: a temp DB `memory_test_<pid>` is created from init.sql and
torn down at the end.
Per-test: `clean_db` fixture truncates tables for isolation.
"""
import os
import sys
import hashlib
import pathlib

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector
import pytest


# ── Config ────────────────────────────────────────────────────────────────────
ADMIN_URL = os.environ.get(
    "INTEGRATION_ADMIN_URL",
    "postgresql://claude:memory_pass@db:5432/postgres",
)
TEST_DB_NAME = f"memory_test_{os.getpid()}"
TEST_DB_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"
INIT_SQL_PATH = pathlib.Path(os.environ.get("INIT_SQL_PATH", "/app/init.sql"))


# ── Test DB lifecycle ─────────────────────────────────────────────────────────
def _create_test_db():
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    admin.close()

    conn = psycopg2.connect(TEST_DB_URL)
    with conn.cursor() as cur:
        cur.execute(INIT_SQL_PATH.read_text())
    conn.commit()
    conn.close()


def _drop_test_db():
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        # Terminate any leftover connections to the test DB before dropping
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
    admin.close()


# Create the DB BEFORE importing server.py — server creates its pool at
# import time using DATABASE_URL.
_create_test_db()
os.environ["DATABASE_URL"] = TEST_DB_URL

# Make server importable
sys.path.insert(0, "/app")
import server  # noqa: E402


# ── Deterministic embedder stub ───────────────────────────────────────────────
# The bug class we guard against (pgvector + psycopg2 param-reuse) only cares
# that the vector is a valid 768-float vector. Semantic quality is irrelevant
# for SQL-shape tests. Identical inputs → identical vectors → sim=1.0 (great
# for dedup tests); different inputs → near-orthogonal vectors → sim≈0.
class _StubEmbedder:
    def encode(self, text, normalize_embeddings=True):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(h[:4], "big")
        rng = np.random.RandomState(seed)
        v = rng.randn(768).astype(np.float32)
        if normalize_embeddings:
            v = v / np.linalg.norm(v)
        return v


server.embedder = _StubEmbedder()


# ── pytest hooks ──────────────────────────────────────────────────────────────
def pytest_sessionfinish(session, exitstatus):
    # Close pool connections before dropping the DB
    try:
        server._pool.closeall()
    except Exception:
        pass
    _drop_test_db()


# ── Per-test fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def clean_db():
    """Wipe all data between tests for isolation. Also clears server cache."""
    with server.db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE memories, imported_sessions RESTART IDENTITY CASCADE")
        conn.commit()
    server._cache_invalidate()
    yield


@pytest.fixture(scope="session")
def srv():
    """Convenience: hand the server module to tests so they can call tools directly.

    Session-scoped because (a) it returns a singleton module and (b) module-scoped
    fixtures in test files (e.g. real_embedder in test_real_embedder.py) need to
    consume it.
    """
    return server


@pytest.fixture(scope="session")
def stub_embed():
    """Convenience: hand the stub embedder to tests that need to pre-compute a vector."""
    return _StubEmbedder()
