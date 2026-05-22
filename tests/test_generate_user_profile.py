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
