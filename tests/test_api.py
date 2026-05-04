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
