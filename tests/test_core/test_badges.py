"""Unit tests for src.core.badges — BadgeSpec and BadgeState."""

import json
from unittest.mock import MagicMock

import pytest

from src.core.badges import BadgePriority, BadgeSpec, BadgeState
from src.core.session import ActivityState, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    *,
    status: SessionStatus = SessionStatus.ACTIVE,
    activity_state: ActivityState = ActivityState.IDLE,
    agent_type: str | None = None,
    main_project_path: str | None = None,
    project_name_error: str | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a minimal ActiveSession mock for BadgeState tests."""
    s = MagicMock()
    s.id = "test-session-id"
    s.status = status
    s.activity_state = activity_state
    # agent_type is a property — configure via property mock
    type(s).agent_type = property(lambda self: agent_type)
    s.main_project_path = main_project_path
    s.project_name_error = project_name_error
    s.metadata = metadata or {}
    return s


# ---------------------------------------------------------------------------
# BadgeSpec
# ---------------------------------------------------------------------------


class TestBadgeSpec:
    def test_to_dict_roundtrip(self) -> None:
        spec = BadgeSpec(
            type="status",
            label="active",
            priority=0,
            visible=True,
            interactive=False,
            payload={"activity_state": "idle"},
        )
        d = spec.to_dict()
        assert d["type"] == "status"
        assert d["label"] == "active"
        assert d["priority"] == 0
        assert d["visible"] is True
        assert d["interactive"] is False
        assert d["payload"] == {"activity_state": "idle"}

    def test_to_dict_is_json_serialisable(self) -> None:
        spec = BadgeSpec(
            type="caveman",
            label="Caveman",
            priority=BadgePriority.CAVEMAN,
            visible=True,
            interactive=False,
        )
        # Should not raise
        json.dumps(spec.to_dict())

    def test_default_payload_is_empty_dict(self) -> None:
        spec = BadgeSpec(type="worker", label="Home", priority=10, visible=True, interactive=False)
        assert spec.payload == {}

    def test_payload_not_shared_between_instances(self) -> None:
        a = BadgeSpec(type="x", label="x", priority=0, visible=True, interactive=False)
        b = BadgeSpec(type="y", label="y", priority=0, visible=True, interactive=False)
        a.payload["key"] = "value"
        assert "key" not in b.payload


# ---------------------------------------------------------------------------
# BadgePriority constants
# ---------------------------------------------------------------------------


class TestBadgePriority:
    def test_ordering(self) -> None:
        priorities = [
            BadgePriority.STATUS,
            BadgePriority.WORKER,
            BadgePriority.AGENT,
            BadgePriority.PROJECT,
            BadgePriority.WORKTREE,
            BadgePriority.CAVEMAN,
        ]
        assert priorities == sorted(priorities), "Priority constants must be strictly ascending"

    def test_values(self) -> None:
        assert BadgePriority.STATUS == 0
        assert BadgePriority.WORKER == 10
        assert BadgePriority.AGENT == 20
        assert BadgePriority.PROJECT == 30
        assert BadgePriority.WORKTREE == 40
        assert BadgePriority.CAVEMAN == 50


# ---------------------------------------------------------------------------
# BadgeState — individual badge builders
# ---------------------------------------------------------------------------


class TestStatusBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    @pytest.mark.parametrize(
        "status",
        [
            SessionStatus.ACTIVE,
            SessionStatus.EXECUTING,
            SessionStatus.PAUSED,
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ],
    )
    def test_status_badge_visible_for_all_statuses(self, status: SessionStatus) -> None:
        session = _make_session(status=status)
        badges = self._bs().compute(session)
        status_badges = [b for b in badges if b.type == "status"]
        assert len(status_badges) == 1
        assert status_badges[0].visible is True

    def test_status_badge_label_matches_status_value(self) -> None:
        session = _make_session(status=SessionStatus.PAUSED)
        badges = self._bs().compute(session)
        sb = next(b for b in badges if b.type == "status")
        assert sb.label == "paused"

    def test_status_badge_payload_contains_activity_state(self) -> None:
        session = _make_session(activity_state=ActivityState.PROCESSING_LLM)
        badges = self._bs().compute(session)
        sb = next(b for b in badges if b.type == "status")
        assert sb.payload["activity_state"] == "processing_llm"

    def test_status_badge_priority(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session)
        sb = next(b for b in badges if b.type == "status")
        assert sb.priority == BadgePriority.STATUS


class TestWorkerBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_worker_badge_always_present(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session, worker_id="wkr-123")
        wb = [b for b in badges if b.type == "worker"]
        assert len(wb) == 1

    def test_worker_badge_label_uses_worker_name(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session, worker_id="wkr-123", worker_name="HomeServer")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.label == "HomeServer"

    def test_worker_badge_label_falls_back_to_worker_id(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session, worker_id="wkr-abc")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.label == "wkr-abc"

    def test_worker_badge_label_falls_back_to_unknown(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session)
        wb = next(b for b in badges if b.type == "worker")
        assert wb.label == "unknown"

    def test_worker_badge_payload_contains_worker_id(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session, worker_id="wkr-xyz")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.payload["worker_id"] == "wkr-xyz"

    def test_worker_badge_not_interactive(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session, worker_id="w")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.interactive is False


class TestAgentBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_agent_badge_absent_for_pure_llm(self) -> None:
        session = _make_session(agent_type=None)
        badges = self._bs().compute(session)
        assert not any(b.type == "agent" for b in badges)

    @pytest.mark.parametrize(
        ("agent", "expected_label"),
        [("claude_code", "ClaudeCode"), ("codex", "Codex"), ("opencode", "OpenCode")],
    )
    def test_agent_badge_present_for_known_agents(self, agent: str, expected_label: str) -> None:
        session = _make_session(agent_type=agent)
        badges = self._bs().compute(session)
        ab = [b for b in badges if b.type == "agent"]
        assert len(ab) == 1
        assert ab[0].label == expected_label

    def test_agent_badge_payload(self) -> None:
        session = _make_session(agent_type="claude_code")
        badges = self._bs().compute(session)
        ab = next(b for b in badges if b.type == "agent")
        assert ab.payload["agent_type"] == "claude_code"

    def test_agent_badge_priority(self) -> None:
        session = _make_session(agent_type="codex")
        badges = self._bs().compute(session)
        ab = next(b for b in badges if b.type == "agent")
        assert ab.priority == BadgePriority.AGENT


class TestProjectBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_project_badge_absent_when_no_path_and_no_error(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session)
        assert not any(b.type == "project" for b in badges)

    def test_project_badge_present_with_path(self) -> None:
        session = _make_session(main_project_path="/home/user/Projects/RCFlow")
        badges = self._bs().compute(session)
        pb = [b for b in badges if b.type == "project"]
        assert len(pb) == 1

    def test_project_badge_label_is_folder_name(self) -> None:
        session = _make_session(main_project_path="/home/user/Projects/RCFlow")
        badges = self._bs().compute(session)
        pb = next(b for b in badges if b.type == "project")
        assert pb.label == "RCFlow"

    def test_project_badge_label_strips_trailing_slash(self) -> None:
        session = _make_session(main_project_path="/home/user/MyProject/")
        badges = self._bs().compute(session)
        pb = next(b for b in badges if b.type == "project")
        assert pb.label == "MyProject"

    def test_project_badge_payload_contains_path(self) -> None:
        path = "/home/user/Projects/RCFlow"
        session = _make_session(main_project_path=path)
        badges = self._bs().compute(session)
        pb = next(b for b in badges if b.type == "project")
        assert pb.payload["path"] == path
        assert pb.payload["error"] is None

    def test_project_badge_present_with_error_only(self) -> None:
        session = _make_session(project_name_error="Project 'Foo' not found")
        badges = self._bs().compute(session)
        pb = [b for b in badges if b.type == "project"]
        assert len(pb) == 1

    def test_project_badge_payload_contains_error(self) -> None:
        session = _make_session(
            main_project_path="/some/path",
            project_name_error="resolution failed",
        )
        badges = self._bs().compute(session)
        pb = next(b for b in badges if b.type == "project")
        assert pb.payload["error"] == "resolution failed"


class TestWorktreeBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_worktree_badge_absent_without_metadata(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session)
        assert not any(b.type == "worktree" for b in badges)

    def test_worktree_badge_absent_when_metadata_is_none(self) -> None:
        session = _make_session(metadata={"worktree": None})
        badges = self._bs().compute(session)
        assert not any(b.type == "worktree" for b in badges)

    def test_worktree_badge_present_with_branch(self) -> None:
        session = _make_session(
            metadata={
                "worktree": {
                    "repo_path": "/home/user/Projects/RCFlow",
                    "branch": "feature/badges",
                    "base": "main",
                    "last_action": "new",
                }
            }
        )
        badges = self._bs().compute(session)
        wb = [b for b in badges if b.type == "worktree"]
        assert len(wb) == 1
        assert wb[0].label == "feature/badges"

    def test_worktree_badge_label_falls_back_to_repo_path(self) -> None:
        session = _make_session(
            metadata={
                "worktree": {
                    "repo_path": "/some/path",
                    "last_action": "new",
                }
            }
        )
        badges = self._bs().compute(session)
        wb = next(b for b in badges if b.type == "worktree")
        assert wb.label == "/some/path"

    def test_worktree_badge_payload_roundtrip(self) -> None:
        wt = {
            "repo_path": "/home/user/Projects/RCFlow",
            "branch": "main",
            "base": None,
            "last_action": "merge",
        }
        session = _make_session(metadata={"worktree": wt})
        badges = self._bs().compute(session)
        wb = next(b for b in badges if b.type == "worktree")
        assert wb.payload["branch"] == "main"
        assert wb.payload["repo_path"] == "/home/user/Projects/RCFlow"


class TestCavemanBadge:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_caveman_badge_absent_when_mode_is_false(self) -> None:
        session = _make_session(metadata={"caveman_mode": False})
        badges = self._bs().compute(session)
        assert not any(b.type == "caveman" for b in badges)

    def test_caveman_badge_absent_when_key_missing(self) -> None:
        session = _make_session()
        badges = self._bs().compute(session)
        assert not any(b.type == "caveman" for b in badges)

    def test_caveman_badge_present_when_mode_is_true(self) -> None:
        session = _make_session(metadata={"caveman_mode": True})
        badges = self._bs().compute(session)
        cb = [b for b in badges if b.type == "caveman"]
        assert len(cb) == 1

    def test_caveman_badge_label(self) -> None:
        session = _make_session(metadata={"caveman_mode": True})
        badges = self._bs().compute(session)
        cb = next(b for b in badges if b.type == "caveman")
        assert cb.label == "Caveman"

    def test_caveman_badge_payload_level_default(self) -> None:
        session = _make_session(metadata={"caveman_mode": True})
        badges = self._bs().compute(session)
        cb = next(b for b in badges if b.type == "caveman")
        assert cb.payload["level"] == "full"

    def test_caveman_badge_payload_level_custom(self) -> None:
        session = _make_session(metadata={"caveman_mode": True, "caveman_level": "lite"})
        badges = self._bs().compute(session)
        cb = next(b for b in badges if b.type == "caveman")
        assert cb.payload["level"] == "lite"

    def test_caveman_badge_priority(self) -> None:
        session = _make_session(metadata={"caveman_mode": True})
        badges = self._bs().compute(session)
        cb = next(b for b in badges if b.type == "caveman")
        assert cb.priority == BadgePriority.CAVEMAN


# ---------------------------------------------------------------------------
# BadgeState.compute — ordering and serialisability
# ---------------------------------------------------------------------------


class TestBadgeStateCompute:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_all_badges_priority_ordering(self) -> None:
        """Full badge set must be emitted in ascending priority order."""
        session = _make_session(
            status=SessionStatus.ACTIVE,
            agent_type="claude_code",
            main_project_path="/home/user/RCFlow",
            metadata={
                "caveman_mode": True,
                "worktree": {
                    "repo_path": "/home/user/RCFlow",
                    "branch": "feat",
                    "last_action": "new",
                },
            },
        )
        badges = self._bs().compute(session, worker_id="wkr-1", worker_name="Home")
        priorities = [b.priority for b in badges]
        assert priorities == sorted(priorities)

    def test_compute_all_serialisable(self) -> None:
        """Every BadgeSpec returned by compute must be JSON-serialisable."""
        session = _make_session(
            status=SessionStatus.EXECUTING,
            activity_state=ActivityState.EXECUTING_TOOL,
            agent_type="codex",
            main_project_path="/tmp/proj",
            metadata={
                "caveman_mode": True,
                "worktree": {"repo_path": "/tmp", "branch": "fix", "last_action": "new"},
            },
        )
        badges = self._bs().compute(session, worker_id="wkr-2")
        for badge in badges:
            json.dumps(badge.to_dict())  # must not raise

    def test_minimal_session_has_status_and_worker(self) -> None:
        """A bare session always yields at least status + worker badges."""
        session = _make_session()
        badges = self._bs().compute(session, worker_id="wkr-x")
        types = {b.type for b in badges}
        assert "status" in types
        assert "worker" in types

    def test_graceful_on_error_in_one_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failure in one badge builder must not prevent other badges being returned."""
        bs = self._bs()
        # Make _agent_badge raise unconditionally
        monkeypatch.setattr(bs, "_agent_badge", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        session = _make_session(agent_type="claude_code")
        badges = bs.compute(session, worker_id="w")
        # status + worker should still be present
        types = {b.type for b in badges}
        assert "status" in types
        assert "worker" in types
        assert "agent" not in types


# ---------------------------------------------------------------------------
# BadgeState.compute_archived — badges for DB-archived sessions
# ---------------------------------------------------------------------------


class TestComputeArchived:
    def _bs(self) -> BadgeState:
        return BadgeState()

    def test_always_returns_status_and_worker(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="backend-xyz")
        types = {b.type for b in badges}
        assert "status" in types
        assert "worker" in types

    def test_status_badge_label_matches_arg(self) -> None:
        badges = self._bs().compute_archived("cancelled", worker_id="w1")
        sb = next(b for b in badges if b.type == "status")
        assert sb.label == "cancelled"

    def test_status_badge_activity_state_is_idle(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="w1")
        sb = next(b for b in badges if b.type == "status")
        assert sb.payload["activity_state"] == "idle"

    def test_worker_badge_label_is_backend_id(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="my-backend")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.label == "my-backend"

    def test_worker_badge_label_falls_back_to_unknown(self) -> None:
        badges = self._bs().compute_archived("completed")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.label == "unknown"

    def test_worker_badge_payload_contains_worker_id(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="bk-1")
        wb = next(b for b in badges if b.type == "worker")
        assert wb.payload["worker_id"] == "bk-1"

    def test_no_caveman_badge_by_default(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="w")
        assert not any(b.type == "caveman" for b in badges)

    def test_caveman_badge_present_when_mode_is_true(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="w", caveman_mode=True)
        cb = [b for b in badges if b.type == "caveman"]
        assert len(cb) == 1
        assert cb[0].label == "Caveman"

    def test_caveman_badge_level_propagated(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="w", caveman_mode=True, caveman_level="moderate")
        cb = next(b for b in badges if b.type == "caveman")
        assert cb.payload["level"] == "moderate"

    def test_all_badges_serialisable(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="bk", caveman_mode=True)
        for badge in badges:
            json.dumps(badge.to_dict())  # must not raise

    def test_priorities_are_ascending(self) -> None:
        badges = self._bs().compute_archived("completed", worker_id="w", caveman_mode=True)
        priorities = [b.priority for b in badges]
        assert priorities == sorted(priorities)
