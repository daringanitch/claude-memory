"""Tests for generate_user_profile.py — pure functions only, no DB or filesystem."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import generate_user_profile as gup


class TestBuildIdentitySection:
    def test_both_projects_and_stack(self):
        result = gup.build_identity_section(["workspace", "osint"], ["Python", "Docker"])
        assert "## Identity" in result
        assert "workspace, osint" in result
        assert "Python, Docker" in result

    def test_projects_only(self):
        result = gup.build_identity_section(["workspace"], [])
        assert "## Identity" in result
        assert "Active projects" in result
        assert "Stack" not in result

    def test_stack_only(self):
        result = gup.build_identity_section([], ["Python"])
        assert "## Identity" in result
        assert "Stack" in result
        assert "Active projects" not in result

    def test_empty_returns_none(self):
        assert gup.build_identity_section([], []) is None


class TestBuildPreferencesSection:
    def test_renders_bullet_list(self):
        result = gup.build_preferences_section(["Use brew for Python.", "Open a feature branch."])
        assert "## Preferences" in result
        assert "- Use brew for Python." in result
        assert "- Open a feature branch." in result

    def test_empty_returns_none(self):
        assert gup.build_preferences_section([]) is None

    def test_auto_memory_format_extracts_preference_not_title(self):
        # auto-memory content has a short title line, then the actual preference
        content = "Python packaging preference\n\nAlways use `brew` for Python.\n\n**Why:** ...\n"
        result = gup.build_preferences_section([content])
        assert "Python packaging preference" not in result
        assert "Always use `brew`" in result

    def test_truncates_long_lines(self):
        result = gup.build_preferences_section(["x" * 200])
        bullet = [line for line in result.splitlines() if line.startswith("- ")][0]
        assert len(bullet) <= 163  # "- " + 160 chars + possible truncation marker
