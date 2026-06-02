"""Unit tests for :mod:`src.core.cwd_tracking`.

Cover the two pure helpers (``parse_cwd_change`` and the worktree
matcher) plus the session-level applier so a regression in either
breaks a focused test instead of surfacing only as a misbehaving
worktree badge.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.cwd_tracking import (
    WorktreeMatch,
    apply_agent_cwd,
    claude_code_jsonl_path,
    extract_paths_from_tool_input,
    infer_cwd_from_output,
    infer_cwd_from_tool_paths,
    latest_cwd_from_cc_jsonl,
    looks_like_git_worktree_mutation,
    parse_cwd_change,
    reset_worktree_cache,
    resolve_worktree_for_cwd,
)
from src.core.session import ActiveSession, SessionType


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_worktree_cache()
    yield
    reset_worktree_cache()


# ---------------------------------------------------------------------------
# parse_cwd_change
# ---------------------------------------------------------------------------


class TestParseCwdChange:
    def test_absolute_path(self):
        assert parse_cwd_change("cd /tmp", "/home/me") == "/tmp"

    def test_relative_path_joins_current(self):
        assert parse_cwd_change("cd src", "/home/me") == "/home/me/src"

    def test_relative_dotdot(self):
        assert parse_cwd_change("cd ..", "/home/me/src") == "/home/me"

    def test_tilde_expands_to_home(self):
        result = parse_cwd_change("cd ~/foo", "/anywhere")
        assert result == os.path.normpath(str(Path.home() / "foo"))

    def test_quoted_path(self):
        assert parse_cwd_change('cd "/a b/c"', "/x") == "/a b/c"

    def test_pushd(self):
        assert parse_cwd_change("pushd /opt", "/home") == "/opt"

    def test_cd_in_and_chain(self):
        # The cd persists for follow-up commands on the same shell line.
        assert parse_cwd_change("cd /repo && git status", "/anywhere") == "/repo"

    def test_cd_after_and_chain(self):
        assert parse_cwd_change("ls && cd /repo", "/anywhere") == "/repo"

    def test_subshell_rejected(self):
        # ``( cd /x ; cmd )`` does not change the outer shell's cwd.
        assert parse_cwd_change("(cd /tmp && ls)", "/home") is None

    def test_cd_dash_unsupported(self):
        assert parse_cwd_change("cd -", "/home") is None

    def test_no_cd_returns_none(self):
        assert parse_cwd_change("ls -la", "/home") is None
        assert parse_cwd_change("", "/home") is None

    def test_variable_path_rejected(self):
        assert parse_cwd_change("cd $HOME", "/anywhere") is None
        assert parse_cwd_change("cd `pwd`", "/anywhere") is None
        assert parse_cwd_change("cd $(echo /tmp)", "/anywhere") is None


# ---------------------------------------------------------------------------
# resolve_worktree_for_cwd
# ---------------------------------------------------------------------------


class TestResolveWorktreeForCwd:
    def test_returns_none_without_anchor(self):
        assert resolve_worktree_for_cwd("/some/path", None) is None
        assert resolve_worktree_for_cwd("", "/repo") is None

    def test_longest_prefix_wins(self, tmp_path):
        repo = tmp_path / "repo"
        foo = repo / ".worktrees" / "foo"
        foo_sub = repo / ".worktrees" / "foo" / "sub"
        repo.mkdir()
        foo.mkdir(parents=True)
        foo_sub.mkdir()
        fake_worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(foo), branch="feature/foo", base="main"),
        ]
        with patch("src.core.cwd_tracking._list_worktrees", return_value=fake_worktrees):
            match = resolve_worktree_for_cwd(str(foo_sub), str(repo))
        assert match is not None
        assert match.branch == "feature/foo"

    def test_no_match_outside_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        fake_worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
        ]
        with patch("src.core.cwd_tracking._list_worktrees", return_value=fake_worktrees):
            assert resolve_worktree_for_cwd("/tmp", str(repo)) is None


# ---------------------------------------------------------------------------
# apply_agent_cwd
# ---------------------------------------------------------------------------


class TestApplyAgentCwd:
    def _session(self):
        return ActiveSession("sid", SessionType.LONG_RUNNING)

    def test_no_change_returns_false(self):
        session = self._session()
        session.metadata["agent_cwd"] = "/a"
        assert apply_agent_cwd(session, "/a") is False

    def test_records_change_and_field_mirror(self):
        session = self._session()
        with patch("src.core.cwd_tracking.resolve_worktree_for_cwd", return_value=None):
            changed = apply_agent_cwd(session, "/new")
        assert changed is True
        assert session.metadata["agent_cwd"] == "/new"
        assert session.agent_cwd == "/new"

    def test_worktree_match_updates_metadata(self):
        session = self._session()
        session.main_project_path = "/repo"
        match = WorktreeMatch(repo_path="/repo", path="/repo/.worktrees/foo", branch="feature/foo", base="main")
        with patch("src.core.cwd_tracking.resolve_worktree_for_cwd", return_value=match):
            apply_agent_cwd(session, "/repo/.worktrees/foo")
        assert session.metadata["worktree"] == {
            "repo_path": "/repo",
            "branch": "feature/foo",
            "base": "main",
            "last_action": "cd",
            "path": "/repo/.worktrees/foo",
        }
        assert session.metadata["selected_worktree_path"] == "/repo/.worktrees/foo"

    def test_no_match_keeps_existing_worktree_metadata(self):
        session = self._session()
        session.metadata["worktree"] = {"branch": "feature/foo"}
        session.main_project_path = "/repo"
        with patch("src.core.cwd_tracking.resolve_worktree_for_cwd", return_value=None):
            apply_agent_cwd(session, "/tmp")
        assert session.metadata["worktree"] == {"branch": "feature/foo"}
        assert session.metadata["agent_cwd"] == "/tmp"


# ---------------------------------------------------------------------------
# looks_like_git_worktree_mutation
# ---------------------------------------------------------------------------


class TestClaudeCodeJsonl:
    def test_encoded_path_matches_known_layout(self):
        p = claude_code_jsonl_path("abc123", "/home/me/Projects/Foo")
        assert p is not None
        assert p.name == "abc123.jsonl"
        assert p.parent.name == "-home-me-Projects-Foo"

    def test_dot_in_path_becomes_dash(self):
        # Claude Code rewrites both ``/`` and ``.`` to ``-`` when
        # naming the project directory (matches the layout under
        # ``~/.claude/projects/``).
        p = claude_code_jsonl_path("s", "/home/me/Projects/spacegame/.worktrees/foo")
        assert p is not None
        assert p.parent.name == "-home-me-Projects-spacegame--worktrees-foo"

    def test_returns_none_on_missing_inputs(self):
        assert claude_code_jsonl_path("", "/x") is None
        assert claude_code_jsonl_path("s", "") is None

    def test_latest_cwd_from_jsonl(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text('{"type":"user","cwd":"/a"}\n{"type":"assistant","cwd":"/a"}\n{"type":"user","cwd":"/b"}\n')
        assert latest_cwd_from_cc_jsonl(p) == "/b"

    def test_latest_cwd_missing_file(self, tmp_path):
        assert latest_cwd_from_cc_jsonl(tmp_path / "nope.jsonl") is None

    def test_latest_cwd_empty_file(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert latest_cwd_from_cc_jsonl(p) is None

    def test_latest_cwd_ignores_lines_without_cwd(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            '{"type":"last-prompt","leafUuid":"x"}\n'
            '{"type":"user","cwd":"/a"}\n'
            '{"type":"permission-mode","permissionMode":"default"}\n'
        )
        assert latest_cwd_from_cc_jsonl(p) == "/a"


class TestInferCwdFromOutput:
    def test_returns_none_without_anchor(self):
        assert infer_cwd_from_output("Now at /tmp", None) is None
        assert infer_cwd_from_output("", "/repo") is None

    def test_recognises_wt_attach_output(self, tmp_path):
        repo = tmp_path / "repo"
        wt_dir = repo / ".worktrees" / "foo"
        wt_dir.mkdir(parents=True)
        worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(wt_dir), branch="feature/foo", base="main"),
        ]
        output = f"Attached. Now at {wt_dir} on feature/foo."
        with patch("src.core.cwd_tracking._list_worktrees", return_value=worktrees):
            assert infer_cwd_from_output(output, str(repo)) == str(wt_dir)

    def test_unrelated_path_ignored(self, tmp_path):
        repo = tmp_path / "repo"
        wt_dir = repo / ".worktrees" / "foo"
        wt_dir.mkdir(parents=True)
        worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(wt_dir), branch="feature/foo", base="main"),
        ]
        # ``/tmp/unrelated`` is absolute but not a known worktree —
        # the helper must NOT flip the badge to it.
        with patch("src.core.cwd_tracking._list_worktrees", return_value=worktrees):
            assert infer_cwd_from_output("error at /tmp/unrelated", str(repo)) is None

    def test_last_match_wins_on_multi_mention(self, tmp_path):
        repo = tmp_path / "repo"
        a = repo / ".worktrees" / "a"
        b = repo / ".worktrees" / "b"
        a.mkdir(parents=True)
        b.mkdir()
        worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(a), branch="A", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(b), branch="B", base=None),
        ]
        # ``git worktree list``-style output — the last-mentioned
        # worktree is taken as the most-recent state.
        output = f"{a}\n{b}\n"
        with patch("src.core.cwd_tracking._list_worktrees", return_value=worktrees):
            assert infer_cwd_from_output(output, str(repo)) == str(b)


class TestInferCwdFromToolPaths:
    def test_no_paths(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert infer_cwd_from_tool_paths([], str(repo), str(repo)) is None

    def test_file_path_under_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        wt_dir = repo / ".worktrees" / "foo"
        wt_dir.mkdir(parents=True)
        worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(wt_dir), branch="foo", base=None),
        ]
        # ``Read(file_path=<wt_dir>/main.py)`` → infer ``wt_dir``.
        with patch("src.core.cwd_tracking._list_worktrees", return_value=worktrees):
            got = infer_cwd_from_tool_paths(
                [str(wt_dir / "main.py")],
                str(repo),
                str(repo),
            )
        assert got == str(wt_dir)

    def test_relative_path_resolved_against_spawn(self, tmp_path):
        repo = tmp_path / "repo"
        wt_dir = repo / ".worktrees" / "foo"
        wt_dir.mkdir(parents=True)
        worktrees = [
            WorktreeMatch(repo_path=str(repo), path=str(repo), branch="main", base=None),
            WorktreeMatch(repo_path=str(repo), path=str(wt_dir), branch="foo", base=None),
        ]
        with patch("src.core.cwd_tracking._list_worktrees", return_value=worktrees):
            got = infer_cwd_from_tool_paths(
                ["main.py"],
                str(wt_dir),
                str(repo),
            )
        assert got == str(wt_dir)


class TestExtractPathsFromToolInput:
    def test_file_path(self):
        assert extract_paths_from_tool_input({"file_path": "/a/b.py"}) == ["/a/b.py"]

    def test_multiedit_edits_list(self):
        out = extract_paths_from_tool_input({"edits": [{"file_path": "/a"}, {"file_path": "/b"}]})
        assert "/a" in out and "/b" in out

    def test_empty(self):
        assert extract_paths_from_tool_input({}) == []
        assert extract_paths_from_tool_input(None) == []  # type: ignore[arg-type]


class TestLooksLikeGitWorktreeMutation:
    def test_add(self):
        assert looks_like_git_worktree_mutation("git worktree add -b foo .worktrees/foo main")

    def test_remove(self):
        assert looks_like_git_worktree_mutation("git worktree remove .worktrees/foo")

    def test_prune(self):
        assert looks_like_git_worktree_mutation("git worktree prune")

    def test_after_cd_chain(self):
        assert looks_like_git_worktree_mutation("cd /repo && git worktree add x main")

    def test_list_is_not_a_mutation(self):
        assert not looks_like_git_worktree_mutation("git worktree list")

    def test_unrelated_command(self):
        assert not looks_like_git_worktree_mutation("git status")
        assert not looks_like_git_worktree_mutation("")
