"""Tests for distill_sessions.py — pure-logic functions only (no DB, API, or model required)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import distill_sessions as ds


class TestParseDistilled:
    def test_valid_json_array(self):
        response = '[{"content": "Use brew for Python packages.", "tags": ["brew", "python"]}]'
        result = ds.parse_distilled(response)
        assert len(result) == 1
        assert result[0]["content"] == "Use brew for Python packages."
        assert result[0]["tags"] == ["brew", "python"]

    def test_json_with_preamble(self):
        response = 'Here are the memories:\n[{"content": "foo", "tags": ["bar"]}]'
        result = ds.parse_distilled(response)
        assert len(result) == 1
        assert result[0]["content"] == "foo"

    def test_empty_array(self):
        result = ds.parse_distilled("[]")
        assert result == []

    def test_no_array_returns_empty(self):
        result = ds.parse_distilled("Nothing useful was learned.")
        assert result == []

    def test_multiple_items(self):
        response = '[{"content": "a", "tags": []}, {"content": "b", "tags": ["x"]}]'
        result = ds.parse_distilled(response)
        assert len(result) == 2
        assert result[1]["content"] == "b"

    def test_whitespace_only(self):
        result = ds.parse_distilled("   ")
        assert result == []


class TestBuildTranscript:
    def _msg(self, text):
        return {"content": text}

    def test_joins_messages_with_separator(self):
        messages = [self._msg("hello"), self._msg("world")]
        transcript = ds.build_transcript(messages)
        assert "hello" in transcript
        assert "world" in transcript
        assert "---" in transcript

    def test_skips_empty_content(self):
        messages = [self._msg("  "), self._msg("kept")]
        transcript = ds.build_transcript(messages)
        assert transcript == "kept"

    def test_truncates_long_transcript(self):
        long_text = "x" * (ds.MAX_TRANSCRIPT_CHARS + 1000)
        messages = [self._msg(long_text)]
        transcript = ds.build_transcript(messages)
        assert len(transcript) <= ds.MAX_TRANSCRIPT_CHARS + 100
        assert "[transcript truncated]" in transcript

    def test_short_transcript_not_truncated(self):
        messages = [self._msg("short message")]
        transcript = ds.build_transcript(messages)
        assert "[transcript truncated]" not in transcript
