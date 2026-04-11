"""Tests for the ArtifactScanner service."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.services.artifact_scanner import _FILE_PATH_RE, ArtifactScanner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-key",
        DATABASE_URL="sqlite+aiosqlite:///test.db",
        ANTHROPIC_API_KEY="test",
        TOOLS_DIR=tmp_path / "tools",
        ARTIFACT_INCLUDE_PATTERN="*",
        ARTIFACT_EXCLUDE_PATTERN="node_modules/**,__pycache__/**,.git/**,.venv/**,*.pyc",
        ARTIFACT_AUTO_SCAN=True,
    )


@pytest.fixture
def md_only_settings(tmp_path: Path) -> Settings:
    """Settings with the default *.md include pattern."""
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-key",
        DATABASE_URL="sqlite+aiosqlite:///test.db",
        ANTHROPIC_API_KEY="test",
        TOOLS_DIR=tmp_path / "tools",
        ARTIFACT_INCLUDE_PATTERN="*.md",
        ARTIFACT_EXCLUDE_PATTERN="node_modules/**,__pycache__/**,.git/**",
    )


def _make_scanner(settings: Settings) -> ArtifactScanner:
    """Create an ArtifactScanner without a real DB session factory (for unit tests)."""
    # Pass a dummy factory — unit tests below don't hit the DB.
    return ArtifactScanner(settings, None)  # type: ignore[arg-type]


# ===========================================================================
# _FILE_PATH_RE — regex extraction
# ===========================================================================


class TestFilePathRegex:
    """Tests for the _FILE_PATH_RE regex that extracts file paths from text."""

    def test_absolute_path_in_sentence(self):
        text = "I edited /home/user/Projects/RCFlow/Design.md today"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/Projects/RCFlow/Design.md"]

    def test_absolute_path_standalone(self):
        text = "/home/user/file.py"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.py"]

    def test_absolute_path_at_end_of_line(self):
        text = "Check /home/user/file.md"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.md"]

    def test_tilde_path(self):
        text = "Look at ~/Documents/notes.md for details"
        assert _FILE_PATH_RE.findall(text) == ["~/Documents/notes.md"]

    def test_relative_dot_path(self):
        text = "Created ./src/main.py"
        assert _FILE_PATH_RE.findall(text) == ["./src/main.py"]

    def test_relative_dotdot_path(self):
        text = "See ../test.md for docs"
        assert _FILE_PATH_RE.findall(text) == ["../test.md"]

    def test_multiple_paths_in_one_line(self):
        text = "Edited ./src/main.py and /home/user/test.md"
        matches = _FILE_PATH_RE.findall(text)
        assert "./src/main.py" in matches
        assert "/home/user/test.md" in matches

    def test_path_in_quotes(self):
        text = 'Read "/home/user/file.md"'
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.md"]

    def test_path_in_backticks(self):
        text = "Check `/home/user/file.md`"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.md"]

    def test_path_after_colon(self):
        text = "file: /home/user/file.md"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.md"]

    def test_path_after_equals(self):
        text = "path=/home/user/file.md"
        assert _FILE_PATH_RE.findall(text) == ["/home/user/file.md"]

    def test_bare_relative_path_not_matched(self):
        """Relative paths without ./ or ../ prefix are NOT matched by the regex."""
        text = "src/services/artifact_scanner.py"
        assert _FILE_PATH_RE.findall(text) == []

    def test_no_paths_in_plain_text(self):
        text = "Hello world, nothing to see here"
        assert _FILE_PATH_RE.findall(text) == []

    def test_path_with_hyphens_and_dots(self):
        text = "Installed /usr/local/bin/my-tool.v2"
        assert _FILE_PATH_RE.findall(text) == ["/usr/local/bin/my-tool.v2"]

    def test_path_with_plus(self):
        text = "File at /home/user/c++/main.cpp"
        matches = _FILE_PATH_RE.findall(text)
        assert "/home/user/c++/main.cpp" in matches

    def test_multiline_extraction(self):
        text = "First file: /home/user/a.md\nSecond file: /home/user/b.py\nThird: ./local/c.txt"
        matches = _FILE_PATH_RE.findall(text)
        assert "/home/user/a.md" in matches
        assert "/home/user/b.py" in matches
        assert "./local/c.txt" in matches


# ===========================================================================
# _should_include_file — include / exclude pattern matching
# ===========================================================================


class TestShouldIncludeFile:
    """Tests for ArtifactScanner._should_include_file."""

    def test_include_wildcard_matches_any_extension(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert scanner._should_include_file(Path("/home/user/project/readme.md"))
        assert scanner._should_include_file(Path("/home/user/project/main.py"))
        assert scanner._should_include_file(Path("/home/user/project/style.css"))

    def test_include_md_only(self, md_only_settings: Settings):
        scanner = _make_scanner(md_only_settings)
        assert scanner._should_include_file(Path("/home/user/project/Design.md"))
        assert scanner._should_include_file(Path("/home/user/project/README.MD"))
        assert not scanner._should_include_file(Path("/home/user/project/main.py"))
        assert not scanner._should_include_file(Path("/home/user/project/style.css"))

    def test_exclude_node_modules(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert not scanner._should_include_file(Path("/home/user/project/node_modules/pkg/index.js"))

    def test_exclude_pycache(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert not scanner._should_include_file(Path("/home/user/project/__pycache__/module.cpython-312.pyc"))

    def test_exclude_git_directory(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert not scanner._should_include_file(Path("/home/user/project/.git/config"))

    def test_exclude_venv(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert not scanner._should_include_file(Path("/home/user/project/.venv/lib/python3.12/site.py"))

    def test_exclude_pyc_extension(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert not scanner._should_include_file(Path("/home/user/project/src/module.pyc"))

    def test_normal_file_not_excluded(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert scanner._should_include_file(Path("/home/user/project/src/main.py"))
        assert scanner._should_include_file(Path("/home/user/project/Design.md"))

    def test_exclude_pattern_strips_glob_suffixes(self, settings: Settings):
        """Verify that /**  and /* suffixes are stripped during init."""
        scanner = _make_scanner(settings)
        # The patterns should be bare directory names after stripping
        assert "node_modules" in scanner.exclude_patterns
        assert "__pycache__" in scanner.exclude_patterns
        assert ".git" in scanner.exclude_patterns
        # No patterns should still have /** or /*
        for p in scanner.exclude_patterns:
            assert not p.endswith("/**"), f"Pattern still has /** suffix: {p}"
            assert not p.endswith("/*"), f"Pattern still has /* suffix: {p}"

    def test_empty_exclude_pattern(self, tmp_path: Path):
        s = Settings(
            RCFLOW_HOST="127.0.0.1",
            RCFLOW_PORT=8765,
            RCFLOW_API_KEY="test-key",
            DATABASE_URL="sqlite+aiosqlite:///test.db",
            ANTHROPIC_API_KEY="test",
            TOOLS_DIR=tmp_path / "tools",
            ARTIFACT_INCLUDE_PATTERN="*",
            ARTIFACT_EXCLUDE_PATTERN="",
        )
        scanner = _make_scanner(s)
        assert scanner.exclude_patterns == []
        assert scanner._should_include_file(Path("/home/user/node_modules/pkg/file.js"))


# ===========================================================================
# _extract_paths_from_text
# ===========================================================================


class TestExtractPathsFromText:
    """Tests for ArtifactScanner._extract_paths_from_text."""

    def test_extracts_absolute_paths(self, settings: Settings):
        scanner = _make_scanner(settings)
        paths = scanner._extract_paths_from_text("Edited /home/user/file.md and /tmp/test.py")
        assert "/home/user/file.md" in paths
        assert "/tmp/test.py" in paths

    def test_extracts_relative_paths(self, settings: Settings):
        scanner = _make_scanner(settings)
        paths = scanner._extract_paths_from_text("See ./src/main.py and ../docs/README.md")
        assert "./src/main.py" in paths
        assert "../docs/README.md" in paths

    def test_no_paths_returns_empty(self, settings: Settings):
        scanner = _make_scanner(settings)
        paths = scanner._extract_paths_from_text("No file paths here at all.")
        assert paths == set()

    def test_deduplicates_paths(self, settings: Settings):
        scanner = _make_scanner(settings)
        paths = scanner._extract_paths_from_text("File /home/user/a.md and again /home/user/a.md")
        assert paths == {"/home/user/a.md"}


# ===========================================================================
# _extract_paths_from_conversation
# ===========================================================================


class TestExtractPathsFromConversation:
    """Tests for ArtifactScanner._extract_paths_from_conversation."""

    def test_string_content(self, settings: Settings):
        scanner = _make_scanner(settings)
        history = [
            {"role": "user", "content": "Edit /home/user/file.md"},
            {"role": "assistant", "content": "Done with /home/user/file.md"},
        ]
        paths = scanner._extract_paths_from_conversation(history)
        assert "/home/user/file.md" in paths

    def test_block_content_with_text(self, settings: Settings):
        scanner = _make_scanner(settings)
        history = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading /home/user/code.py"},
                ],
            },
        ]
        paths = scanner._extract_paths_from_conversation(history)
        assert "/home/user/code.py" in paths

    def test_block_content_with_content_field(self, settings: Settings):
        scanner = _make_scanner(settings)
        history = [
            {
                "role": "tool",
                "content": [
                    {"type": "tool_result", "content": "File at /tmp/output.md created"},
                ],
            },
        ]
        paths = scanner._extract_paths_from_conversation(history)
        assert "/tmp/output.md" in paths

    def test_empty_conversation(self, settings: Settings):
        scanner = _make_scanner(settings)
        assert scanner._extract_paths_from_conversation([]) == set()

    def test_no_content_key(self, settings: Settings):
        scanner = _make_scanner(settings)
        paths = scanner._extract_paths_from_conversation([{"role": "system"}])
        assert paths == set()

    def test_mixed_content_types(self, settings: Settings):
        scanner = _make_scanner(settings)
        history = [
            {"role": "user", "content": "Check /home/user/a.md"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Editing /home/user/b.py"},
                    {"type": "image", "url": "http://example.com"},  # non-text block
                ],
            },
        ]
        paths = scanner._extract_paths_from_conversation(history)
        assert "/home/user/a.md" in paths
        assert "/home/user/b.py" in paths


# ===========================================================================
# _resolve_path
# ===========================================================================


class TestResolvePath:
    """Tests for ArtifactScanner._resolve_path."""

    def test_existing_file(self, settings: Settings, tmp_path: Path):
        scanner = _make_scanner(settings)
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = scanner._resolve_path(str(f))
        assert result is not None
        assert result == f.resolve()

    def test_nonexistent_file(self, settings: Settings):
        scanner = _make_scanner(settings)
        result = scanner._resolve_path("/nonexistent/path/file.txt")
        assert result is None

    def test_directory_not_matched(self, settings: Settings, tmp_path: Path):
        scanner = _make_scanner(settings)
        d = tmp_path / "subdir"
        d.mkdir()
        result = scanner._resolve_path(str(d))
        assert result is None

    def test_dot_relative_resolves_against_project_path(self, settings: Settings, tmp_path: Path):
        """./file.md that doesn't exist relative to CWD is resolved against project_path."""
        # Use a name that won't accidentally exist in the server's CWD
        f = tmp_path / "UNIQUE_TEST_DOC_XYZ.md"
        f.write_text("# doc")
        scanner = _make_scanner(settings)
        result = scanner._resolve_path("./UNIQUE_TEST_DOC_XYZ.md", project_path=tmp_path)
        assert result is not None
        assert result == f.resolve()

    def test_bare_relative_resolves_against_project_path(self, settings: Settings, tmp_path: Path):
        """A bare filename resolves against project_path when CWD resolution fails."""
        sub = tmp_path / "src"
        sub.mkdir()
        f = sub / "session.py"
        f.write_text("# session")
        scanner = _make_scanner(settings)
        result = scanner._resolve_path("src/session.py", project_path=tmp_path)
        assert result is not None
        assert result == f.resolve()

    def test_no_project_path_returns_none_for_relative(self, settings: Settings):
        """Without project_path, a relative path that doesn't exist at CWD returns None."""
        scanner = _make_scanner(settings)
        result = scanner._resolve_path("./nonexistent.md")
        assert result is None


# ===========================================================================
# Integration: include + exclude with real file paths
# ===========================================================================


class TestIncludeExcludeIntegration:
    """End-to-end tests combining extraction, resolution, and filtering."""

    def test_md_file_in_project_root_included(self, md_only_settings: Settings, tmp_path: Path):
        scanner = _make_scanner(md_only_settings)
        f = tmp_path / "Design.md"
        f.write_text("# Design")
        assert scanner._should_include_file(f)

    def test_md_file_in_node_modules_excluded(self, md_only_settings: Settings, tmp_path: Path):
        scanner = _make_scanner(md_only_settings)
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        f = nm / "README.md"
        f.write_text("# Readme")
        assert not scanner._should_include_file(f)

    def test_py_file_excluded_by_md_only_pattern(self, md_only_settings: Settings, tmp_path: Path):
        scanner = _make_scanner(md_only_settings)
        f = tmp_path / "main.py"
        f.write_text("print('hello')")
        assert not scanner._should_include_file(f)

    def test_md_file_in_git_excluded(self, md_only_settings: Settings, tmp_path: Path):
        scanner = _make_scanner(md_only_settings)
        git_dir = tmp_path / ".git" / "info"
        git_dir.mkdir(parents=True)
        f = git_dir / "exclude.md"
        f.write_text("# exclude")
        assert not scanner._should_include_file(f)

    def test_full_pipeline_extract_and_filter(self, settings: Settings, tmp_path: Path):
        """Simulate the full pipeline: extract paths from text, resolve, filter."""
        scanner = _make_scanner(settings)

        # Create real files
        good_file = tmp_path / "readme.md"
        good_file.write_text("# Hello")
        excluded_file = tmp_path / "node_modules" / "pkg" / "file.js"
        excluded_file.parent.mkdir(parents=True)
        excluded_file.write_text("module.exports = {}")

        text = f"See {good_file} and {excluded_file}"
        paths = scanner._extract_paths_from_text(text)

        included = []
        for raw in paths:
            resolved = scanner._resolve_path(raw)
            if resolved and scanner._should_include_file(resolved):
                included.append(resolved)

        assert good_file.resolve() in included
        assert excluded_file.resolve() not in included
