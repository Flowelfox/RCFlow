"""Tests for the PATCH /api/sessions/{session_id}/reorder endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

from src.core.session import SessionManager, SessionType


def _auth() -> dict[str, str]:
    return {"X-API-Key": "test-api-key"}


def _url(session_id: str) -> str:
    return f"/api/sessions/{session_id}/reorder"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


class TestReorderSession:
    """Tests for the reorder endpoint."""

    def test_move_to_top(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        s2 = sm.create_session(SessionType.CONVERSATIONAL)
        s3 = sm.create_session(SessionType.CONVERSATIONAL)

        # s3 is at top (lowest sort_order). Move s1 to top.
        resp = client.patch(_url(s1.id), json={"after_session_id": None}, headers=_auth())
        assert resp.status_code == 200
        assert s1.sort_order is not None

        # s1 should now have the lowest sort_order
        orders = sorted(
            [(s.id, s.sort_order) for s in [s1, s2, s3]],
            key=lambda x: x[1],
        )
        assert orders[0][0] == s1.id

    def test_move_after_specific_session(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        s2 = sm.create_session(SessionType.CONVERSATIONAL)
        s3 = sm.create_session(SessionType.CONVERSATIONAL)

        # Move s1 to after s2
        resp = client.patch(_url(s1.id), json={"after_session_id": s2.id}, headers=_auth())
        assert resp.status_code == 200

        # Order should be: s3, s2, s1 (sorted by sort_order ascending)
        ordered = sorted([s1, s2, s3], key=lambda s: s.sort_order)
        assert [s.id for s in ordered] == [s3.id, s2.id, s1.id]

    def test_move_to_bottom(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        s2 = sm.create_session(SessionType.CONVERSATIONAL)
        s3 = sm.create_session(SessionType.CONVERSATIONAL)

        # Get the last session ID (highest sort_order)
        ordered = sorted([s1, s2, s3], key=lambda s: s.sort_order)
        last_id = ordered[-1].id

        # Move s3 (currently at top) to after last
        resp = client.patch(_url(s3.id), json={"after_session_id": last_id}, headers=_auth())
        assert resp.status_code == 200

        ordered_after = sorted([s1, s2, s3], key=lambda s: s.sort_order)
        assert ordered_after[-1].id == s3.id

    def test_self_reference_rejected(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)

        resp = client.patch(_url(s1.id), json={"after_session_id": s1.id}, headers=_auth())
        assert resp.status_code == 400

    def test_session_not_found(self, client: TestClient) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = client.patch(_url(fake_id), json={"after_session_id": None}, headers=_auth())
        assert resp.status_code == 404

    def test_anchor_not_found(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        fake_anchor = "00000000-0000-0000-0000-000000000000"

        resp = client.patch(_url(s1.id), json={"after_session_id": fake_anchor}, headers=_auth())
        assert resp.status_code == 404

    def test_invalid_session_id(self, client: TestClient) -> None:
        resp = client.patch(_url("not-a-uuid"), json={"after_session_id": None}, headers=_auth())
        assert resp.status_code == 404

    def test_sort_order_assigned_on_creation(self, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        assert s1.sort_order is not None
        assert s1.sort_order == 0  # First session gets 0

        s2 = sm.create_session(SessionType.CONVERSATIONAL)
        assert s2.sort_order is not None
        assert s2.sort_order < s1.sort_order  # Newer session appears first (lower sort_order)

    def test_multiple_reorders_maintain_consistency(self, client: TestClient, test_app: FastAPI) -> None:
        sm: SessionManager = test_app.state.session_manager
        s1 = sm.create_session(SessionType.CONVERSATIONAL)
        s2 = sm.create_session(SessionType.CONVERSATIONAL)
        s3 = sm.create_session(SessionType.CONVERSATIONAL)
        s4 = sm.create_session(SessionType.CONVERSATIONAL)

        # Move s1 to top
        resp = client.patch(_url(s1.id), json={"after_session_id": None}, headers=_auth())
        assert resp.status_code == 200

        # Move s4 after s1
        resp = client.patch(_url(s4.id), json={"after_session_id": s1.id}, headers=_auth())
        assert resp.status_code == 200

        # Expected order: s1, s4, s3, s2
        ordered = sorted([s1, s2, s3, s4], key=lambda s: s.sort_order)
        ids = [s.id for s in ordered]
        assert ids == [s1.id, s4.id, s3.id, s2.id]
