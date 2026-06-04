"""Tests for GitHubService and helpers in src/services/github_service.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.services.github_service import (
    PR_ROLE_QUALIFIERS,
    GitHubService,
    GitHubServiceError,
    _parse_dt,
    _parse_file,
    _parse_pull,
    _parse_thread,
    _repo_ref_from_search_item,
    evaluate_scopes,
)

# ---------------------------------------------------------------------------
# Sample fixture data
# ---------------------------------------------------------------------------

_PULL: dict = {
    "node_id": "PR_kwABC",
    "number": 42,
    "title": "Fix login bug",
    "body": "Users cannot log in with SSO",
    "state": "open",
    "draft": False,
    "html_url": "https://github.com/acme/web/pull/42",
    "merged": False,
    "additions": 12,
    "deletions": 3,
    "changed_files": 2,
    "user": {"login": "alice", "avatar_url": "https://avatars/alice"},
    "base": {"ref": "main", "repo": {"name": "web", "owner": {"login": "acme"}}},
    "head": {"ref": "fix-sso", "sha": "deadbeef"},
    "created_at": "2026-06-01T10:00:00Z",
    "updated_at": "2026-06-02T12:30:00Z",
}


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------


class TestParsers:
    def test_parse_pull_open(self):
        p = _parse_pull(_PULL)
        assert p["github_id"] == "PR_kwABC"
        assert p["repo_owner"] == "acme"
        assert p["repo_name"] == "web"
        assert p["number"] == 42
        assert p["state"] == "open"
        assert p["base_ref"] == "main"
        assert p["head_ref"] == "fix-sso"
        assert p["head_sha"] == "deadbeef"
        assert p["author"] == "alice"
        assert p["additions"] == 12

    def test_parse_pull_merged_overrides_state(self):
        merged = {**_PULL, "state": "closed", "merged": True}
        assert _parse_pull(merged)["state"] == "merged"

    def test_parse_pull_merged_at_implies_merged(self):
        merged = {**_PULL, "state": "closed", "merged": False, "merged_at": "2026-06-03T00:00:00Z"}
        assert _parse_pull(merged)["state"] == "merged"

    def test_parse_file_patch_is_diff(self):
        f = _parse_file(
            {
                "filename": "app/main.py",
                "status": "modified",
                "additions": 3,
                "deletions": 1,
                "changes": 4,
                "patch": "@@ -1,2 +1,3 @@\n-old\n+new",
            }
        )
        assert f["filename"] == "app/main.py"
        assert f["status"] == "modified"
        assert f["patch"].startswith("@@")

    def test_parse_file_binary_has_no_patch(self):
        f = _parse_file({"filename": "logo.png", "status": "added"})
        assert f["patch"] is None

    def test_repo_ref_from_search_item(self):
        owner, name, number = _repo_ref_from_search_item(
            {"repository_url": "https://api.github.com/repos/acme/web", "number": 7}
        )
        assert (owner, name, number) == ("acme", "web", 7)

    def test_repo_ref_malformed_yields_blanks(self):
        owner, name, number = _repo_ref_from_search_item({"repository_url": "garbage", "number": 9})
        assert owner == "" and name == "" and number == 9

    def test_parse_dt_handles_blank(self):
        assert _parse_dt(None) is not None
        assert _parse_dt("2026-06-02T12:30:00Z").year == 2026


# ---------------------------------------------------------------------------
# Service orchestration (REST mocked at the _rest boundary)
# ---------------------------------------------------------------------------


class TestService:
    def test_role_qualifiers(self):
        assert PR_ROLE_QUALIFIERS == {"for_me": "review-requested:@me", "created": "author:@me"}

    @pytest.mark.asyncio
    async def test_test_token(self):
        svc = GitHubService(token="x")
        svc._rest = AsyncMock(return_value={"login": "alice", "id": 1})  # type: ignore[method-assign]
        user = await svc.test_token()
        assert user["login"] == "alice"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_pull_requests_stamps_role_and_fetches_detail(self):
        svc = GitHubService(token="x")

        async def fake_rest(method, path, *, params=None, json=None):
            if path == "/search/issues":
                assert "review-requested:@me" in params["q"]
                return {"items": [{"repository_url": "https://api.github.com/repos/acme/web", "number": 42}]}
            if path == "/repos/acme/web/pulls/42":
                return _PULL
            raise AssertionError(f"unexpected path {path}")

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        prs = await svc.list_pull_requests("for_me")
        assert len(prs) == 1
        assert prs[0]["role"] == "for_me"
        assert prs[0]["number"] == 42
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_pull_requests_unknown_role(self):
        svc = GitHubService(token="x")
        with pytest.raises(GitHubServiceError):
            await svc.list_pull_requests("bogus")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_list_pr_files_paginates(self):
        svc = GitHubService(token="x")
        full_page = [{"filename": f"f{i}.py", "status": "modified", "patch": "@@"} for i in range(100)]
        last_page = [{"filename": "tail.py", "status": "added", "patch": "@@"}]

        async def fake_rest(method, path, *, params=None, json=None):
            return full_page if params["page"] == 1 else last_page

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        files = await svc.list_pr_files("acme", "web", 42)
        assert len(files) == 101
        assert files[-1]["filename"] == "tail.py"
        await svc.aclose()

    def test_raise_for_status_401(self):
        resp = httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/user"))
        with pytest.raises(GitHubServiceError) as exc:
            GitHubService._raise_for_status(resp)
        assert exc.value.status_code == 401

    def test_raise_for_status_rate_limit(self):
        resp = httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            request=httpx.Request("GET", "https://api.github.com/user"),
        )
        with pytest.raises(GitHubServiceError, match="rate limit"):
            GitHubService._raise_for_status(resp)


class TestScopes:
    def test_evaluate_repo_and_org_satisfied(self):
        r = evaluate_scopes(["repo", "read:org", "gist"])
        assert r[0]["scope"] == "repo" and r[0]["satisfied"] is True and r[0]["required"] is True
        assert r[1]["scope"] == "read:org" and r[1]["satisfied"] is True and r[1]["required"] is False

    def test_public_repo_satisfies_repo(self):
        r = evaluate_scopes(["public_repo"])
        assert r[0]["satisfied"] is True  # alt scope counts
        assert r[1]["satisfied"] is False

    def test_empty_grants_unsatisfied(self):
        r = evaluate_scopes([])
        assert all(s["satisfied"] is False for s in r)

    @pytest.mark.asyncio
    async def test_token_info_classic_parses_scopes(self):
        svc = GitHubService(token="x")
        resp = httpx.Response(
            200,
            headers={"X-OAuth-Scopes": "repo, read:org, gist"},
            json={"login": "alice"},
            request=httpx.Request("GET", "https://api.github.com/user"),
        )
        svc._client.get = AsyncMock(return_value=resp)  # type: ignore[method-assign]
        info = await svc.token_info()
        assert info["login"] == "alice"
        assert info["fine_grained"] is False
        assert info["scopes"] == ["repo", "read:org", "gist"]
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_get_file_content_returns_raw_text(self):
        svc = GitHubService(token="x")
        resp = httpx.Response(
            200,
            text="line1\nline2\n",
            request=httpx.Request("GET", "https://api.github.com/repos/o/r/contents/a.py"),
        )
        svc._client.get = AsyncMock(return_value=resp)  # type: ignore[method-assign]
        out = await svc.get_file_content("o", "r", "a.py", "deadbeef")
        assert out == "line1\nline2\n"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_token_info_fine_grained_has_no_scope_header(self):
        svc = GitHubService(token="x")
        resp = httpx.Response(
            200,
            json={"login": "bob"},  # no X-OAuth-Scopes header
            request=httpx.Request("GET", "https://api.github.com/user"),
        )
        svc._client.get = AsyncMock(return_value=resp)  # type: ignore[method-assign]
        info = await svc.token_info()
        assert info["fine_grained"] is True and info["scopes"] == []
        await svc.aclose()


# ---------------------------------------------------------------------------
# Review threads + write actions
# ---------------------------------------------------------------------------


class TestReview:
    def test_parse_thread(self):
        node = {
            "id": "RT_node1",
            "isResolved": True,
            "isOutdated": False,
            "path": "app/main.py",
            "line": 12,
            "diffSide": "RIGHT",
            "comments": {
                "nodes": [
                    {"id": "RC_n1", "databaseId": 555, "author": {"login": "bob"}, "body": "nit", "createdAt": "t"},
                ]
            },
        }
        t = _parse_thread(node)
        assert t["thread_id"] == "RT_node1"
        assert t["is_resolved"] is True
        assert t["path"] == "app/main.py" and t["line"] == 12 and t["side"] == "RIGHT"
        assert t["comments"][0]["database_id"] == 555 and t["comments"][0]["author"] == "bob"

    @pytest.mark.asyncio
    async def test_list_review_threads(self):
        svc = GitHubService(token="x")
        svc._gql = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {"id": "RT1", "isResolved": False, "comments": {"nodes": []}},
                            ]
                        }
                    }
                }
            }
        )
        threads = await svc.list_review_threads("o", "r", 1)
        assert len(threads) == 1 and threads[0]["thread_id"] == "RT1"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_create_review_rejects_bad_event(self):
        svc = GitHubService(token="x")
        with pytest.raises(GitHubServiceError):
            await svc.create_review("o", "r", 1, event="LGTM")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_create_review_builds_payload(self):
        svc = GitHubService(token="x")
        captured: dict = {}

        async def fake_rest(method, path, *, params=None, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return {"id": 1, "state": "APPROVED"}

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        await svc.create_review(
            "o", "r", 7, event="APPROVE", body="lgtm", comments=[{"path": "a", "line": 1, "side": "RIGHT", "body": "x"}]
        )
        assert captured["method"] == "POST" and captured["path"].endswith("/pulls/7/reviews")
        assert captured["json"]["event"] == "APPROVE" and captured["json"]["body"] == "lgtm"
        assert captured["json"]["comments"][0]["path"] == "a"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_resolve_thread_selects_mutation(self):
        svc = GitHubService(token="x")
        seen: dict = {}

        async def fake_gql(query, variables=None):
            seen["query"] = query
            seen["vars"] = variables
            return {}

        svc._gql = AsyncMock(side_effect=fake_gql)  # type: ignore[method-assign]
        await svc.resolve_thread("RT1", resolved=True)
        assert "resolveReviewThread" in seen["query"] and seen["vars"]["threadId"] == "RT1"
        await svc.resolve_thread("RT1", resolved=False)
        assert "unresolveReviewThread" in seen["query"]
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_merge_rejects_bad_method(self):
        svc = GitHubService(token="x")
        with pytest.raises(GitHubServiceError):
            await svc.merge_pull_request("o", "r", 1, method="fast-forward")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_merge_builds_payload(self):
        svc = GitHubService(token="x")
        captured: dict = {}

        async def fake_rest(method, path, *, params=None, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return {"merged": True, "message": "Pull Request successfully merged"}

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        out = await svc.merge_pull_request("o", "r", 9, method="squash", commit_title="T")
        assert captured["method"] == "PUT" and captured["path"].endswith("/pulls/9/merge")
        assert captured["json"]["merge_method"] == "squash" and captured["json"]["commit_title"] == "T"
        assert out["merged"] is True
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_create_pull_request_returns_parsed(self):
        svc = GitHubService(token="x")
        captured: dict = {}

        async def fake_rest(method, path, *, params=None, json=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            return _PULL

        svc._rest = AsyncMock(side_effect=fake_rest)  # type: ignore[method-assign]
        out = await svc.create_pull_request(
            "acme", "web", title="New", head="acme:feat", base="main", body="b", draft=True
        )
        assert captured["method"] == "POST" and captured["path"].endswith("/repos/acme/web/pulls")
        assert captured["json"]["head"] == "acme:feat" and captured["json"]["draft"] is True
        # Returns a normalised PR dict (so it can be cached/upserted).
        assert out["number"] == 42 and out["github_id"] == "PR_kwABC"
        await svc.aclose()
