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


def _make_insert_conn(insert_row=None):
    """Build a mock connection for the INSERT path only (guard bypassed or ADD decision)."""
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.fetchone.return_value = insert_row
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


class TestSaveMemory:
    def test_duplicate_detected(self):
        noop_decision = {
            "action": "NOOP", "method": "semantic",
            "reason": "Near-identical memory already exists (similarity 0.97).",
            "target_id": 5, "target_preview": "existing memory content here",
            "similarity": 0.97,
        }
        with patch("server._write_guard", return_value=noop_decision):
            result = server.save_memory("existing memory content here")
        data = json.loads(result)
        assert data["blocked"] is True
        assert data["reason"] == "duplicate"
        assert "5" in data["message"]

    def test_successful_save(self):
        add_decision = {
            "action": "ADD", "method": "none", "reason": "No similar memory found.",
            "target_id": None, "target_preview": None, "similarity": None,
        }
        insert_row = {"id": 42, "created_at": datetime(2026, 1, 1), "deleted_at": None}
        conn, cur = _make_insert_conn(insert_row=insert_row)
        with patch("server._write_guard", return_value=add_decision), \
             patch("server.db_conn") as mock_db:
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


# ── Write Guard tests ─────────────────────────────────────────────────────────

def _make_guard_conn(sim_row=None):
    """Mock connection for _write_guard — one fetchone call."""
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.fetchone.return_value = sim_row
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


class TestWriteGuard:
    def _patch_db(self, sim_row=None):
        conn, _ = _make_guard_conn(sim_row)
        mock_db = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        return mock_db

    def test_add_when_no_similar_memory(self):
        with patch("server.db_conn", self._patch_db(sim_row=None)):
            decision = server._write_guard("brand new content", [0.0] * 768)
        assert decision["action"] == "ADD"
        assert decision["target_id"] is None
        assert decision["similarity"] is None

    def test_noop_above_noop_threshold(self):
        row = {"id": 7, "sim": 0.95, "content": "existing content that is very similar"}
        with patch("server.db_conn", self._patch_db(sim_row=row)):
            decision = server._write_guard("nearly identical", [0.0] * 768)
        assert decision["action"] == "NOOP"
        assert decision["target_id"] == 7
        assert decision["similarity"] == 0.95
        assert decision["method"] == "semantic"

    def test_noop_at_exact_threshold_boundary(self):
        row = {"id": 3, "sim": 0.92, "content": "boundary case"}
        with patch("server.db_conn", self._patch_db(sim_row=row)):
            decision = server._write_guard("boundary content", [0.0] * 768)
        assert decision["action"] == "NOOP"

    def test_update_between_thresholds(self):
        row = {"id": 12, "sim": 0.82, "content": "somewhat similar existing memory"}
        with patch("server.db_conn", self._patch_db(sim_row=row)):
            decision = server._write_guard("related but different", [0.0] * 768)
        assert decision["action"] == "UPDATE"
        assert decision["target_id"] == 12
        assert decision["similarity"] == 0.82
        assert decision["method"] == "semantic"

    def test_update_at_lower_threshold_boundary(self):
        row = {"id": 5, "sim": 0.75, "content": "just at update threshold"}
        with patch("server.db_conn", self._patch_db(sim_row=row)):
            decision = server._write_guard("at threshold", [0.0] * 768)
        assert decision["action"] == "UPDATE"

    def test_target_preview_truncated_to_120_chars(self):
        row = {"id": 9, "sim": 0.95, "content": "x" * 200}
        with patch("server.db_conn", self._patch_db(sim_row=row)):
            decision = server._write_guard("similar", [0.0] * 768)
        assert len(decision["target_preview"]) == 120

    def test_db_error_fails_open(self):
        with patch("server.db_conn", side_effect=Exception("connection refused")):
            decision = server._write_guard("some content", [0.0] * 768)
        assert decision["action"] == "ADD"
        assert "Guard DB error" in decision["reason"]

    def test_decision_has_all_required_keys(self):
        with patch("server.db_conn", self._patch_db(sim_row=None)):
            decision = server._write_guard("content", [0.0] * 768)
        required = {"action", "method", "reason", "target_id", "target_preview", "similarity"}
        assert required.issubset(decision.keys())


class TestCheckMemory:
    def test_returns_valid_json_add(self):
        with patch("server._write_guard", return_value={
            "action": "ADD", "method": "none", "reason": "No similar.",
            "target_id": None, "target_preview": None, "similarity": None,
        }):
            result = server.check_memory("some content")
        data = json.loads(result)
        assert data["action"] == "ADD"

    def test_noop_decision_in_json(self):
        with patch("server._write_guard", return_value={
            "action": "NOOP", "method": "semantic", "reason": "Duplicate.",
            "target_id": 5, "target_preview": "existing...", "similarity": 0.95,
        }):
            result = server.check_memory("duplicate content")
        data = json.loads(result)
        assert data["action"] == "NOOP"
        assert data["target_id"] == 5

    def test_update_decision_in_json(self):
        with patch("server._write_guard", return_value={
            "action": "UPDATE", "method": "semantic", "reason": "Similar exists.",
            "target_id": 3, "target_preview": "similar memory", "similarity": 0.80,
        }):
            result = server.check_memory("related content")
        data = json.loads(result)
        assert data["action"] == "UPDATE"
        assert data["target_id"] == 3


class TestSaveMemoryGuard:
    def test_guard_blocks_noop(self):
        with patch("server._write_guard", return_value={
            "action": "NOOP", "method": "semantic", "reason": "Duplicate.",
            "target_id": 5, "target_preview": "preview text", "similarity": 0.95,
        }):
            result = server.save_memory("duplicate content", guard=True)
        data = json.loads(result)
        assert data["blocked"] is True
        assert data["reason"] == "duplicate"
        assert "5" in data["message"]

    def test_guard_blocks_update(self):
        with patch("server._write_guard", return_value={
            "action": "UPDATE", "method": "semantic", "reason": "Similar exists.",
            "target_id": 12, "target_preview": "similar preview", "similarity": 0.82,
        }):
            result = server.save_memory("similar content", guard=True)
        data = json.loads(result)
        assert data["blocked"] is True
        assert data["reason"] == "similar_exists"
        assert "update_memory" in data["message"]
        assert "12" in data["message"]

    def test_guard_allows_add(self):
        insert_row = {"id": 99, "created_at": datetime(2026, 4, 11), "deleted_at": None}
        conn, _ = _make_insert_conn(insert_row=insert_row)
        with patch("server._write_guard", return_value={
            "action": "ADD", "method": "none", "reason": "No similar.",
            "target_id": None, "target_preview": None, "similarity": None,
        }), patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.save_memory("brand new content", guard=True)
        assert "✅" in result
        assert "99" in result

    def test_guard_false_bypasses_check(self):
        insert_row = {"id": 77, "created_at": datetime(2026, 4, 11), "deleted_at": None}
        conn, _ = _make_insert_conn(insert_row=insert_row)
        with patch("server._write_guard") as mock_guard, \
             patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.save_memory("any content", guard=False)
        mock_guard.assert_not_called()
        assert "✅" in result

    def test_guard_true_is_default(self):
        insert_row = {"id": 55, "created_at": datetime(2026, 4, 11), "deleted_at": None}
        conn, _ = _make_insert_conn(insert_row=insert_row)
        with patch("server._write_guard", return_value={
            "action": "ADD", "method": "none", "reason": "No similar.",
            "target_id": None, "target_preview": None, "similarity": None,
        }) as mock_guard, patch("server.db_conn") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            result = server.save_memory("new content")
        mock_guard.assert_called_once()
        assert "✅" in result
