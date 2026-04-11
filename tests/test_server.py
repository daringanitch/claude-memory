"""Tests for mcp-server/server.py — pure-logic and tool functions (DB mocked)."""
import sys
import os
import json
from datetime import datetime
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-server"))

import server


class TestParseDt:
    def test_valid_date(self):
        dt, err = server._parse_dt("2026-01-15", "since")
        assert dt == datetime(2026, 1, 15)
        assert err is None

    def test_valid_datetime(self):
        dt, err = server._parse_dt("2026-01-15T12:30:00", "before")
        assert dt == datetime(2026, 1, 15, 12, 30, 0)
        assert err is None

    def test_empty_string_returns_none(self):
        dt, err = server._parse_dt("", "since")
        assert dt is None
        assert err is None

    def test_none_returns_none(self):
        dt, err = server._parse_dt(None, "since")
        assert dt is None
        assert err is None

    def test_invalid_date_returns_error_string(self):
        dt, err = server._parse_dt("not-a-date", "since")
        assert dt is None
        assert "❌" in err
        assert "since" in err
        assert "not-a-date" in err


class TestSaveMemory:
    def _make_conn(self, dup_row=None, insert_row=None):
        """Build a mock connection that simulates DB interactions."""
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [dup_row, insert_row]
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        return conn, cur

    def test_duplicate_detected(self):
        dup = {"id": 5, "sim": 0.97, "content": "existing memory content here"}
        conn, cur = self._make_conn(dup_row=dup)
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.save_memory("existing memory content here")
        assert "Duplicate" in result
        assert "5" in result

    def test_successful_save(self):
        insert_row = {"id": 42, "created_at": datetime(2026, 1, 1)}
        conn, cur = self._make_conn(dup_row=None, insert_row=insert_row)
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.save_memory("brand new memory")
        assert "✅" in result
        assert "42" in result


class TestSemanticSearch:
    def test_no_results_returns_message(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = []
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.semantic_search("something obscure")
        assert "No similar memories" in result

    def test_invalid_since_date_returns_error(self):
        result = server.semantic_search("query", since="bad-date")
        assert "❌" in result

    def test_invalid_before_date_returns_error(self):
        result = server.semantic_search("query", before="not-a-date")
        assert "❌" in result

    def test_results_returned_as_json(self):
        row = {"id": 1, "content": "test", "tags": [], "source": "x", "project": "", "created_at": datetime(2026, 1, 1), "similarity": 0.85}
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [row]
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.semantic_search("test query")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["content"] == "test"


class TestListMemories:
    def test_empty_returns_placeholder(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = []
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.list_memories()
        assert "No memories" in result

    def test_invalid_since_returns_error(self):
        result = server.list_memories(since="2026-99-99")
        assert "❌" in result


class TestDeleteMemory:
    def test_delete_existing(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.rowcount = 1
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.delete_memory(7)
        assert "✅" in result
        assert "7" in result

    def test_delete_nonexistent(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.rowcount = 0
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.delete_memory(999)
        assert "❌" in result


class TestExportMemories:
    def test_no_results_returns_message(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = []
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.export_memories()
        assert "No memories" in result

    def test_json_export_structure(self):
        row = {"id": 1, "content": "memo", "tags": ["t1"], "source": "s", "project": "p",
               "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1)}
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [row]
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.export_memories(output_format="json")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["memories"][0]["content"] == "memo"

    def test_markdown_export_contains_content(self):
        row = {"id": 2, "content": "markdown memory", "tags": [], "source": "s", "project": "",
               "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1)}
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [row]
        conn.cursor.return_value = cur
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.export_memories(output_format="markdown")
        assert "markdown memory" in result
        assert "# Memory Export" in result

    def test_invalid_format_returns_error(self):
        result = server.export_memories(output_format="csv")
        assert "❌" in result

    def test_invalid_since_returns_error(self):
        result = server.export_memories(since="bad")
        assert "❌" in result


class TestHybridSearch:
    def _make_conn(self, rows):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        return conn

    def test_weights_must_sum_to_one(self):
        result = server.hybrid_search("query", keyword_weight=0.6, semantic_weight=0.6)
        assert "❌" in result
        assert "1.0" in result

    def test_negative_weight_rejected(self):
        result = server.hybrid_search("query", keyword_weight=-0.1, semantic_weight=1.1)
        assert "❌" in result

    def test_invalid_since_returns_error(self):
        result = server.hybrid_search("query", since="not-a-date")
        assert "❌" in result

    def test_invalid_before_returns_error(self):
        result = server.hybrid_search("query", before="not-a-date")
        assert "❌" in result

    def test_no_results_returns_message(self):
        conn = self._make_conn([])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.hybrid_search("obscure query")
        assert "No memories found" in result

    def test_results_returned_as_json(self):
        row = {
            "id": 1, "content": "test memory", "tags": [], "source": "x",
            "project": "", "created_at": datetime(2026, 1, 1),
            "keyword_score": 0.5, "semantic_score": 0.8, "hybrid_score": 0.59,
        }
        conn = self._make_conn([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.hybrid_search("test")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["content"] == "test memory"
        assert "hybrid_score" in data[0]
        assert "keyword_score" in data[0]
        assert "semantic_score" in data[0]

    def test_custom_weights_accepted(self):
        row = {
            "id": 2, "content": "pure keyword match", "tags": [], "source": "y",
            "project": "", "created_at": datetime(2026, 1, 1),
            "keyword_score": 0.9, "semantic_score": 0.1, "hybrid_score": 0.9,
        }
        conn = self._make_conn([row])
        with patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.hybrid_search("pure keyword match", keyword_weight=1.0, semantic_weight=0.0)
        data = json.loads(result)
        assert data[0]["id"] == 2
