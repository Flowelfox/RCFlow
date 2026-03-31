"""Tests for src/core/permissions.py.

Covers:
- ``classify_risk`` — risk levels for each tool type including escalation rules
- ``describe_tool_action`` — human-readable action descriptions
- ``get_scope_options`` — available scopes per tool type
- ``PermissionManager`` — rule storage, scope semantics, approval workflow,
  timeout, snapshot/restore, cancel_all_pending
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.permissions import (
    PermissionDecision,
    PermissionManager,
    PermissionScope,
    classify_risk,
    describe_tool_action,
    get_scope_options,
)

# ---------------------------------------------------------------------------
# classify_risk
# ---------------------------------------------------------------------------


class TestClassifyRisk:
    # Read-only tools

    def test_read_is_low(self) -> None:
        assert classify_risk("Read", {}) == "low"

    def test_glob_is_low(self) -> None:
        assert classify_risk("Glob", {"pattern": "**/*.py"}) == "low"

    def test_grep_is_low(self) -> None:
        assert classify_risk("Grep", {"pattern": "TODO"}) == "low"

    def test_webfetch_is_low(self) -> None:
        assert classify_risk("WebFetch", {"url": "https://example.com"}) == "low"

    # Write tools

    def test_write_normal_path_is_medium(self) -> None:
        assert classify_risk("Write", {"file_path": "/home/user/project/file.py"}) == "medium"

    def test_edit_normal_path_is_medium(self) -> None:
        assert classify_risk("Edit", {"file_path": "/home/user/file.txt"}) == "medium"

    # Bash — base high, escalates to critical for destructive patterns

    def test_bash_safe_command_is_high(self) -> None:
        assert classify_risk("Bash", {"command": "ls -la"}) == "high"

    def test_bash_rm_is_critical(self) -> None:
        assert classify_risk("Bash", {"command": "rm /tmp/foo"}) == "critical"

    def test_bash_rmdir_is_critical(self) -> None:
        assert classify_risk("Bash", {"command": "rmdir old_dir"}) == "critical"

    def test_bash_git_reset_hard_is_critical(self) -> None:
        assert classify_risk("Bash", {"command": "git reset --hard HEAD"}) == "critical"

    def test_bash_git_push_force_is_critical(self) -> None:
        assert classify_risk("Bash", {"command": "git push --force origin main"}) == "critical"

    def test_bash_kill_is_critical(self) -> None:
        assert classify_risk("Bash", {"command": "kill 1234"}) == "critical"

    # Write/Edit to sensitive paths

    def test_write_to_etc_is_high(self) -> None:
        assert classify_risk("Write", {"file_path": "/etc/hosts"}) == "high"

    def test_edit_to_usr_is_high(self) -> None:
        assert classify_risk("Edit", {"file_path": "/usr/lib/libfoo.so"}) == "high"

    def test_write_to_bin_is_high(self) -> None:
        assert classify_risk("Write", {"file_path": "/bin/sh"}) == "high"

    # Worktree action-based risk

    def test_worktree_list_is_low(self) -> None:
        assert classify_risk("worktree", {"action": "list"}) == "low"

    def test_worktree_merge_is_high(self) -> None:
        assert classify_risk("worktree", {"action": "merge"}) == "high"

    def test_worktree_rm_is_high(self) -> None:
        assert classify_risk("worktree", {"action": "rm"}) == "high"

    def test_worktree_new_is_medium(self) -> None:
        assert classify_risk("worktree", {"action": "new"}) == "medium"

    # Unknown tool

    def test_unknown_tool_is_medium(self) -> None:
        assert classify_risk("SomeFancyTool", {}) == "medium"


# ---------------------------------------------------------------------------
# describe_tool_action
# ---------------------------------------------------------------------------


class TestDescribeToolAction:
    def test_bash_includes_command(self) -> None:
        desc = describe_tool_action("Bash", {"command": "echo hello"})
        assert "echo hello" in desc

    def test_read_includes_file_path(self) -> None:
        desc = describe_tool_action("Read", {"file_path": "/tmp/foo.py"})
        assert "/tmp/foo.py" in desc

    def test_write_includes_file_path(self) -> None:
        desc = describe_tool_action("Write", {"file_path": "/src/main.py"})
        assert "/src/main.py" in desc

    def test_edit_includes_file_path(self) -> None:
        desc = describe_tool_action("Edit", {"file_path": "/src/utils.py"})
        assert "/src/utils.py" in desc

    def test_glob_includes_pattern(self) -> None:
        desc = describe_tool_action("Glob", {"pattern": "**/*.md"})
        assert "**/*.md" in desc

    def test_grep_includes_pattern(self) -> None:
        desc = describe_tool_action("Grep", {"pattern": "TODO"})
        assert "TODO" in desc

    def test_agent_includes_description(self) -> None:
        desc = describe_tool_action("Agent", {"description": "run tests"})
        assert "run tests" in desc

    def test_webfetch_includes_url(self) -> None:
        desc = describe_tool_action("WebFetch", {"url": "https://example.com"})
        assert "https://example.com" in desc

    def test_worktree_new_includes_branch(self) -> None:
        desc = describe_tool_action(
            "worktree",
            {"action": "new", "branch": "feature/foo", "base": "main", "repo_path": "/repo"},
        )
        assert "feature/foo" in desc

    def test_worktree_merge_includes_name(self) -> None:
        desc = describe_tool_action(
            "worktree",
            {"action": "merge", "name": "feature/foo", "message": "done", "repo_path": "/repo"},
        )
        assert "feature/foo" in desc

    def test_worktree_rm_includes_name(self) -> None:
        desc = describe_tool_action("worktree", {"action": "rm", "name": "feature/old", "repo_path": "/repo"})
        assert "feature/old" in desc

    def test_worktree_list_includes_repo(self) -> None:
        desc = describe_tool_action("worktree", {"action": "list", "repo_path": "/my/repo"})
        assert "/my/repo" in desc

    def test_unknown_tool_includes_name(self) -> None:
        desc = describe_tool_action("MyCustomTool", {})
        assert "MyCustomTool" in desc


# ---------------------------------------------------------------------------
# get_scope_options
# ---------------------------------------------------------------------------


class TestGetScopeOptions:
    def test_bash_has_three_options(self) -> None:
        opts = get_scope_options("Bash")
        assert len(opts) == 3
        assert "tool_path" not in opts

    def test_read_includes_tool_path(self) -> None:
        opts = get_scope_options("Read")
        assert "tool_path" in opts

    def test_write_includes_tool_path(self) -> None:
        opts = get_scope_options("Write")
        assert "tool_path" in opts

    def test_edit_includes_tool_path(self) -> None:
        opts = get_scope_options("Edit")
        assert "tool_path" in opts

    def test_glob_includes_tool_path(self) -> None:
        opts = get_scope_options("Glob")
        assert "tool_path" in opts

    def test_all_options_include_base_scopes(self) -> None:
        for tool in ("Bash", "Agent", "Read", "Write"):
            opts = get_scope_options(tool)
            assert "once" in opts
            assert "tool_session" in opts
            assert "all_session" in opts


# ---------------------------------------------------------------------------
# PermissionManager
# ---------------------------------------------------------------------------


class TestPermissionManagerBasics:
    def test_initially_no_pending(self) -> None:
        pm = PermissionManager()
        assert pm.has_pending is False

    def test_create_request_adds_pending(self) -> None:
        pm = PermissionManager()
        pm.create_request("Bash", {"command": "ls"})
        assert pm.has_pending is True

    def test_check_cached_returns_none_by_default(self) -> None:
        pm = PermissionManager()
        assert pm.check_cached("Bash", {}) is None

    def test_resolve_unknown_request_returns_false(self) -> None:
        pm = PermissionManager()
        result = pm.resolve_request("nonexistent-id", PermissionDecision.ALLOW, PermissionScope.ONCE)
        assert result is False

    def test_resolve_known_request_returns_true(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        result = pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.ONCE)
        assert result is True


class TestPermissionManagerScopes:
    def test_once_scope_not_cached_after_resolution(self) -> None:
        """ONCE scope must NOT be stored in rules — it applies only to the single request."""
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.ONCE)
        assert pm.check_cached("Bash", {}) is None

    def test_tool_session_scope_allows_same_tool(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {"command": "ls"})
        pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.TOOL_SESSION)
        assert pm.check_cached("Bash", {"command": "different command"}) == PermissionDecision.ALLOW

    def test_tool_session_scope_does_not_affect_other_tools(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.TOOL_SESSION)
        assert pm.check_cached("Write", {"file_path": "/tmp/x"}) is None

    def test_tool_session_deny_cached(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Write", {})
        pm.resolve_request(p.request_id, PermissionDecision.DENY, PermissionScope.TOOL_SESSION)
        assert pm.check_cached("Write", {}) == PermissionDecision.DENY

    def test_all_session_allow_sets_blanket_flag(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.ALL_SESSION)
        assert pm._blanket_allow is True
        assert pm.check_cached("AnyTool", {}) == PermissionDecision.ALLOW

    def test_all_session_deny_sets_blanket_deny_flag(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.resolve_request(p.request_id, PermissionDecision.DENY, PermissionScope.ALL_SESSION)
        assert pm._blanket_deny is True
        assert pm.check_cached("AnyTool", {}) == PermissionDecision.DENY

    def test_tool_path_scope_matches_path_prefix(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Read", {"path": "/home/user/project/file.py"})
        pm.resolve_request(
            p.request_id,
            PermissionDecision.ALLOW,
            PermissionScope.TOOL_PATH,
            path_prefix="/home/user/project/",
        )
        # Same tool, path under prefix → matches
        result = pm.check_cached("Read", {"path": "/home/user/project/other.py"})
        assert result == PermissionDecision.ALLOW

    def test_tool_path_scope_does_not_match_different_prefix(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Read", {"path": "/home/user/project/file.py"})
        pm.resolve_request(
            p.request_id,
            PermissionDecision.ALLOW,
            PermissionScope.TOOL_PATH,
            path_prefix="/home/user/project/",
        )
        # Different path outside prefix → no match
        result = pm.check_cached("Read", {"path": "/etc/hosts"})
        assert result is None


class TestPermissionManagerCancelAll:
    def test_cancel_all_denies_pending_requests(self) -> None:
        pm = PermissionManager()
        p1 = pm.create_request("Bash", {})
        p2 = pm.create_request("Write", {})

        pm.cancel_all_pending()

        assert p1.decision == PermissionDecision.DENY
        assert p2.decision == PermissionDecision.DENY

    def test_cancel_all_marks_timed_out(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.cancel_all_pending()
        assert p.timed_out is True

    def test_cancel_all_signals_events(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.cancel_all_pending()
        assert p.event.is_set()

    def test_cancel_all_clears_pending_dict(self) -> None:
        pm = PermissionManager()
        pm.create_request("Bash", {})
        pm.cancel_all_pending()
        assert not pm.has_pending


class TestPermissionManagerWaitForResponse:
    async def test_resolves_when_event_set(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})

        async def _resolve_later() -> None:
            await asyncio.sleep(0)
            pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.ONCE)

        task = asyncio.create_task(_resolve_later())
        result = await pm.wait_for_response(p.request_id)
        await task
        assert result.decision == PermissionDecision.ALLOW

    async def test_times_out_and_auto_denies(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        result = await pm.wait_for_response(p.request_id, timeout=0.01)
        assert result.timed_out is True
        assert result.decision == PermissionDecision.DENY

    async def test_times_out_removes_from_pending(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        await pm.wait_for_response(p.request_id, timeout=0.01)
        assert not pm.has_pending

    async def test_raises_for_unknown_request(self) -> None:
        pm = PermissionManager()
        with pytest.raises(ValueError, match="Unknown request"):
            await pm.wait_for_response("nonexistent-id")


class TestPermissionManagerSnapshotRestore:
    def test_snapshot_empty_manager(self) -> None:
        pm = PermissionManager()
        assert pm.get_rules_snapshot() == []

    def test_snapshot_includes_tool_session_rules(self) -> None:
        pm = PermissionManager()
        p = pm.create_request("Bash", {})
        pm.resolve_request(p.request_id, PermissionDecision.ALLOW, PermissionScope.TOOL_SESSION)
        snap = pm.get_rules_snapshot()
        assert any(r["tool_name"] == "Bash" and r["decision"] == "allow" for r in snap)

    def test_snapshot_includes_blanket_allow(self) -> None:
        pm = PermissionManager()
        pm._blanket_allow = True
        snap = pm.get_rules_snapshot()
        assert any(r["tool_name"] == "*" and r["decision"] == "allow" for r in snap)

    def test_snapshot_includes_blanket_deny(self) -> None:
        pm = PermissionManager()
        pm._blanket_deny = True
        snap = pm.get_rules_snapshot()
        assert any(r["tool_name"] == "*" and r["decision"] == "deny" for r in snap)

    def test_restore_blanket_allow(self) -> None:
        pm = PermissionManager()
        pm.restore_rules([{"tool_name": "*", "decision": "allow", "scope": "all_session", "path_prefix": None}])
        assert pm._blanket_allow is True
        assert pm.check_cached("AnyTool", {}) == PermissionDecision.ALLOW

    def test_restore_blanket_deny(self) -> None:
        pm = PermissionManager()
        pm.restore_rules([{"tool_name": "*", "decision": "deny", "scope": "all_session", "path_prefix": None}])
        assert pm._blanket_deny is True

    def test_restore_tool_session_rule(self) -> None:
        pm = PermissionManager()
        pm.restore_rules([{"tool_name": "Bash", "decision": "allow", "scope": "tool_session", "path_prefix": None}])
        assert pm.check_cached("Bash", {}) == PermissionDecision.ALLOW

    def test_snapshot_restore_roundtrip(self) -> None:
        """Rules saved via get_rules_snapshot() must survive restore_rules()."""
        pm1 = PermissionManager()
        p = pm1.create_request("Write", {"file_path": "/home/user/file.py"})
        pm1.resolve_request(p.request_id, PermissionDecision.DENY, PermissionScope.TOOL_SESSION)

        snap = pm1.get_rules_snapshot()

        pm2 = PermissionManager()
        pm2.restore_rules(snap)
        assert pm2.check_cached("Write", {}) == PermissionDecision.DENY
