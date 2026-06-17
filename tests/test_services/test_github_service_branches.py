"""Additional branch/error-path tests for GitHubService.

Complements ``test_github_service.py`` by exercising the transport-layer
helpers (``_rest`` / ``_gql``), the diff/raw endpoints, error translation in
``_raise_for_status``, and the remaining write helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.services.github_service import GitHubService, GitHubServiceError

_REQ = httpx.Request("GET", "https://api.github.com/user")


def _svc() -> GitHubService:
    return GitHubService(token="x")


# ---------------------------------------------------------------------------
# _raise_for_status
# ---------------------------------------------------------------------------


class TestRaiseForStatus:
    def test_403_forbidden_not_rate_limit(self):
        resp = httpx.Response(403, headers={"X-RateLimit-Remaining": "57"}, request=_REQ)
        with pytest.raises(GitHubServiceError, match="forbidden") as exc:
            GitHubService._raise_for_status(resp)
        assert exc.value.status_code == 403

    def test_generic_4xx(self):
        resp = httpx.Response(404, request=_REQ)
        with pytest.raises(GitHubServiceError, match="HTTP 404") as exc:
            GitHubService._raise_for_status(resp)
        assert exc.value.status_code == 404

    def test_2xx_passes(self):
        GitHubService._raise_for_status(httpx.Response(200, request=_REQ))  # no raise


# ---------------------------------------------------------------------------
# _rest transport
# ---------------------------------------------------------------------------


class TestRest:
    @pytest.mark.asyncio
    async def test_relative_path_decodes_json(self):
        svc = _svc()
        svc._client.request = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, json={"login": "alice"}, request=_REQ)
        )
        out = await svc._rest("GET", "/user")
        assert out == {"login": "alice"}
        # Path is joined to the API base URL.
        assert svc._client.request.call_args.args[1] == "https://api.github.com/user"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_absolute_path_used_verbatim(self):
        svc = _svc()
        svc._client.request = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, json={"ok": True}, request=_REQ)
        )
        await svc._rest("GET", "https://example.com/x")
        assert svc._client.request.call_args.args[1] == "https://example.com/x"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_204_returns_none(self):
        svc = _svc()
        svc._client.request = AsyncMock(return_value=httpx.Response(204, request=_REQ))  # type: ignore[method-assign]
        assert await svc._rest("DELETE", "/x") is None
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_empty_body_returns_none(self):
        svc = _svc()
        svc._client.request = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, content=b"", request=_REQ)
        )
        assert await svc._rest("GET", "/x") is None
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        svc = _svc()
        svc._client.request = AsyncMock(side_effect=httpx.TimeoutException("slow"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="timed out"):
            await svc._rest("GET", "/x")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_request_error_raises(self):
        svc = _svc()
        svc._client.request = AsyncMock(side_effect=httpx.ConnectError("down"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="request failed"):
            await svc._rest("GET", "/x")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_http_error_translated(self):
        svc = _svc()
        svc._client.request = AsyncMock(return_value=httpx.Response(401, request=_REQ))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="invalid or expired"):
            await svc._rest("GET", "/x")
        await svc.aclose()


# ---------------------------------------------------------------------------
# _gql transport
# ---------------------------------------------------------------------------


class TestGql:
    @pytest.mark.asyncio
    async def test_returns_data_payload(self):
        svc = _svc()
        svc._client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, json={"data": {"viewer": {"login": "a"}}}, request=_REQ)
        )
        out = await svc._gql("query { viewer { login } }")
        assert out == {"viewer": {"login": "a"}}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_sends_variables(self):
        svc = _svc()
        svc._client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, json={"data": {}}, request=_REQ)
        )
        await svc._gql("q", {"owner": "acme"})
        sent = svc._client.post.call_args.kwargs["json"]
        assert sent["variables"] == {"owner": "acme"}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_graphql_errors_raise(self):
        svc = _svc()
        svc._client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(
                200, json={"errors": [{"message": "bad query"}, {"message": "and worse"}]}, request=_REQ
            )
        )
        with pytest.raises(GitHubServiceError, match="bad query; and worse"):
            await svc._gql("q")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_missing_data_returns_empty(self):
        svc = _svc()
        svc._client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, json={}, request=_REQ)
        )
        assert await svc._gql("q") == {}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        svc = _svc()
        svc._client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="timed out"):
            await svc._gql("q")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_request_error_raises(self):
        svc = _svc()
        svc._client.post = AsyncMock(side_effect=httpx.ConnectError("down"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="request failed"):
            await svc._gql("q")
        await svc.aclose()


# ---------------------------------------------------------------------------
# get_pr_diff / get_file_content transport errors
# ---------------------------------------------------------------------------


class TestRawEndpoints:
    @pytest.mark.asyncio
    async def test_get_pr_diff_returns_text(self):
        svc = _svc()
        svc._client.get = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(200, text="diff --git a b\n@@", request=_REQ)
        )
        out = await svc.get_pr_diff("o", "r", 1)
        assert out.startswith("diff --git")
        # Requested the diff media type.
        assert svc._client.get.call_args.kwargs["headers"]["Accept"] == "application/vnd.github.diff"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_get_pr_diff_timeout(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="timed out"):
            await svc.get_pr_diff("o", "r", 1)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_get_pr_diff_request_error(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.ConnectError("down"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="request failed"):
            await svc.get_pr_diff("o", "r", 1)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_get_file_content_timeout(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="timed out"):
            await svc.get_file_content("o", "r", "a.py", "ref")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_get_file_content_request_error(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.ConnectError("down"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="request failed"):
            await svc.get_file_content("o", "r", "a.py", "ref")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_token_info_timeout(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="timed out"):
            await svc.token_info()
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_token_info_request_error(self):
        svc = _svc()
        svc._client.get = AsyncMock(side_effect=httpx.ConnectError("down"))  # type: ignore[method-assign]
        with pytest.raises(GitHubServiceError, match="request failed"):
            await svc.token_info()
        await svc.aclose()


# ---------------------------------------------------------------------------
# list_pull_requests branch coverage
# ---------------------------------------------------------------------------


class TestListPullRequests:
    @pytest.mark.asyncio
    async def test_unknown_state_raises(self):
        svc = _svc()
        with pytest.raises(GitHubServiceError, match="Unknown PR state"):
            await svc.list_pull_requests("for_me", state="archived")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_all_role_with_repo_scope(self):
        svc = _svc()

        async def fake_rest(method, path, *, params=None, json=None):
            assert path == "/search/issues"
            # 'all' role: no @me qualifier, repo scope present.
            assert "repo:acme/web" in params["q"]
            assert "@me" not in params["q"]
            return {"items": [{"repository_url": "https://api.github.com/repos/acme/web", "number": 5}]}

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        svc.get_pull_request = AsyncMock(return_value={"number": 5})  # type: ignore[method-assign]
        svc.get_pr_status = AsyncMock(return_value={"review_decision": None, "merge_status": None})  # type: ignore[method-assign]
        prs = await svc.list_pull_requests("all", repo="acme/web")
        assert prs[0]["role"] == "all" and prs[0]["number"] == 5
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_skips_items_without_owner(self):
        svc = _svc()
        svc._rest = AsyncMock(  # type: ignore[method-assign]
            return_value={"items": [{"repository_url": "garbage", "number": 1}]}
        )
        prs = await svc.list_pull_requests("created")
        assert prs == []
        await svc.aclose()


# ---------------------------------------------------------------------------
# Remaining write helpers
# ---------------------------------------------------------------------------


class TestWriteHelpers:
    @pytest.mark.asyncio
    async def test_reply_review_comment(self):
        svc = _svc()
        captured: dict = {}

        async def fake_rest(method, path, *, params=None, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return {"id": 1}

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        await svc.reply_review_comment("o", "r", 7, 555, "thanks")
        assert captured["method"] == "POST"
        assert captured["path"].endswith("/pulls/7/comments/555/replies")
        assert captured["json"] == {"body": "thanks"}
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_delete_review_comment(self):
        svc = _svc()
        captured: dict = {}

        async def fake_rest(method, path, *, params=None, json=None):
            captured["method"] = method
            captured["path"] = path
            return

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        out = await svc.delete_review_comment("o", "r", 555)
        assert out is None
        assert captured["method"] == "DELETE"
        assert captured["path"].endswith("/pulls/comments/555")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_create_issue_comment_via_rest(self):
        svc = _svc()
        svc._rest = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": 9, "user": {"login": "me", "avatar_url": "a"}, "body": "hi", "html_url": "u"}
        )
        out = await svc.create_review("o", "r", 1, event="COMMENT", body="b")
        assert out["id"] == 9
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_reviews_empty(self):
        svc = _svc()
        svc._rest = AsyncMock(return_value=None)  # type: ignore[method-assign]
        assert await svc.list_reviews("o", "r", 1) == []
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_pr_files_empty_first_page(self):
        svc = _svc()
        svc._rest = AsyncMock(return_value=[])  # type: ignore[method-assign]
        assert await svc.list_pr_files("o", "r", 1) == []
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_issue_comments_empty(self):
        svc = _svc()
        svc._rest = AsyncMock(return_value=[])  # type: ignore[method-assign]
        assert await svc.list_issue_comments("o", "r", 1) == []
        await svc.aclose()


# ---------------------------------------------------------------------------
# Context-manager protocol
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_async_with_closes_client(self):
        async with GitHubService(token="x") as svc:
            assert isinstance(svc, GitHubService)
        # After exit the client is closed.
        assert svc._client.is_closed
