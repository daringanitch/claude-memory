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


class TestBuildWorkingStyleSection:
    def test_renders_bullet_list(self):
        contents = ["Prefers terse responses.", "Opens feature branches first."]
        result = gup.build_working_style_section(contents)
        assert "## Working Style" in result
        assert "- Prefers terse responses." in result
        assert "- Opens feature branches first." in result

    def test_empty_returns_none(self):
        assert gup.build_working_style_section([]) is None

    def test_uses_first_substantive_line(self):
        content = "Short title\n\nThe user prefers terse responses — skips summaries."
        result = gup.build_working_style_section([content])
        assert "Short title" not in result
        assert "The user prefers terse" in result

    def test_truncates_long_lines(self):
        result = gup.build_working_style_section(["x" * 200])
        bullet = [line for line in result.splitlines() if line.startswith("- ")][0]
        assert len(bullet) <= 163


class TestBuildActiveProjectsSection:
    def test_renders_bullet_list(self):
        contents = ["Enterprise OSINT Platform — Flask backend, React frontend."]
        result = gup.build_active_projects_section(contents)
        assert "## Active Projects" in result
        assert "- Enterprise OSINT Platform" in result

    def test_empty_returns_none(self):
        assert gup.build_active_projects_section([]) is None

    def test_multiple_projects(self):
        contents = ["Project A — description A.", "Project B — description B."]
        result = gup.build_active_projects_section(contents)
        assert "- Project A" in result
        assert "- Project B" in result

    def test_truncates_long_lines(self):
        result = gup.build_active_projects_section(["x" * 300])
        bullet = [line for line in result.splitlines() if line.startswith("- ")][0]
        assert len(bullet) <= 203  # "- " + 200 chars + possible truncation marker


class TestBuildToolingSection:
    def _row(self, tags, content):
        return {"tags": tags, "content": content}

    def test_extracts_commands(self):
        rows = [self._row(["commands", "source:signals"],
                          "Common shell commands in project 'workspace': git, gh, docker.")]
        result = gup.build_tooling_section(rows)
        assert "## Tooling" in result
        assert "git, gh, docker" in result
        assert "Common commands" in result

    def test_extracts_files(self):
        rows = [self._row(["files", "source:signals"],
                          "Frequently accessed files in project 'workspace': server.py, README.md.")]
        result = gup.build_tooling_section(rows)
        assert "server.py" in result
        assert "Frequent files" in result

    def test_extracts_workflow(self):
        rows = [self._row(["workflow", "source:signals"],
                          "Workflow pattern for project 'workspace' (8 sessions): tool categories — execution (54%).")]
        result = gup.build_tooling_section(rows)
        assert "execution (54%)" in result
        assert "Workflow" in result

    def test_empty_returns_none(self):
        assert gup.build_tooling_section([]) is None

    def test_all_three_signals(self):
        rows = [
            self._row(["commands", "source:signals"], "Common shell commands in project 'x': git, gh."),
            self._row(["files",    "source:signals"], "Frequently accessed files in project 'x': server.py."),
            self._row(["workflow", "source:signals"], "Workflow pattern for project 'x' (1 sessions): tool categories — execution (50%)."),
        ]
        result = gup.build_tooling_section(rows)
        assert "git, gh" in result
        assert "server.py" in result
        assert "execution (50%)" in result
