---
updated: 2026-06-04
---

# GitHub Integration — PR Reviews

RCFlow integrates with the [GitHub](https://github.com) API to review pull
requests **inside the app** — list the PRs awaiting your review, read their
diffs, comment in inline threads synced back to GitHub, approve / request
changes, and merge — without leaving RCFlow. The coding agent is **on-demand
assistance, not an autonomous reviewer**: the human drives every decision; the
agent only helps when asked (summarise/explain a PR, suggest and apply fixes
from comments to a worktree, or author a change and open the PR).

**See also:**
- [Configuration](configuration.md) — `GITHUB_TOKEN`, `GITHUB_DEFAULT_REPO`, `GITHUB_SYNC_ON_STARTUP`
- [Database](database.md) — `github_prs` table *(Phase 1)*
- [WebSocket API](websocket-api.md) — PR list / review message types *(Phase 1+)*
- [Linear Integration](linear.md) — the sibling integration this mirrors

---

## Overview

- Authentication is a **Personal Access Token** stored as the masked
  `GITHUB_TOKEN` secret in Worker Settings (no OAuth, no `gh` CLI). The token
  needs `repo` + `read:org` scope. The backend performs every authenticated
  call — the token is never injected into agent prompts or logs.
- Reads of simple resources (PR lists, files, diffs) and **all writes** (review
  comments, review submission, merge) use the **REST v3** API. Review **threads**
  — the only place where resolved-state and line anchoring are coherent — are
  read over the **GraphQL v4** API.
- Synced PR metadata is cached in the `github_prs` table and survives restarts;
  diffs and blobs are fetched live.

## Backend Service — `GitHubService`

`src/services/github_service.py`

An async client wrapping the GitHub REST + GraphQL APIs (`httpx.AsyncClient`,
token auth, pinned `X-GitHub-Api-Version`). Raises `GitHubServiceError`
(carrying the HTTP status) on transport or API errors. Used as an async context
manager.

| Method | Description |
|--------|-------------|
| `test_token()` | Validate the token and return the authenticated user (`GET /user`) — used by the integration `status` preflight |
| `list_pull_requests(role)` / `get_pull_request` / `list_pr_files` / `get_pr_diff` | Read PRs, detail, per-file patches, whole-PR diff (REST) |
| `list_review_threads` | Read inline review threads (GraphQL) |
| `create_review` / `reply_review_comment` / `resolve_thread` | Submit a review, reply to a thread, resolve/unresolve (REST + GraphQL) |
| `merge_pull_request` / `create_pull_request` | Merge / open a PR (REST) |
| `_rest` / `_gql` / `aclose` | REST request, GraphQL request, close client |

The read-only assist prompts (summarise / explain) are built in
`src/services/pr_assist.py` from a cached PR plus its live diff.

## Roadmap

The feature lands in phases (GitHub-backed from day one):

| Phase | Scope |
|-------|-------|
| **0** *(done)* | Config secret + `GitHubService` transport/auth skeleton + this doc |
| **1** | Backend PR read: list (`for_me` / `created`), files (per-file `patch` = unified diff), `github_prs` model + migration, REST routes under `/api/integrations/github`, list broadcast |
| **2** *(MVP ship)* | Client review pane: PR list (For me / Created tabs), file tree + unified/split diff viewer (extracted from the tool-block diff renderer) |
| **3** *(done)* | Inline comment threads (GraphQL read, REST write), approve / request-changes / comment, merge |
| **4a** *(done)* | On-demand **read-only** agent assist: summarise the PR / explain one file (seeded one-shot session, deny-all perms) |
| **4b** | Git-mutating agent assist: apply fixes from comments to a worktree, author a change & open the PR (`GitHubService.create_pull_request` exists; worktree/push orchestration staged) |

## HTTP Endpoints

All under `/api/integrations/github/`, `X-API-Key` required. See [HTTP API](http-api.md#github-integration).

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/status`          | Token configured + validity preflight (`{configured, valid, login}`) |
| POST | `/sync`            | Re-sync open PRs (`?role=for_me\|created`, default both) → `{synced}` |
| GET  | `/prs`             | List cached PRs (`?role=`, `?state=`, `?q=`) → `{prs, total}` |
| GET  | `/prs/{id}`        | Single cached PR by local UUID |
| GET  | `/prs/{id}/files`  | Live changed files, each with a per-file unified-diff `patch` |
| GET  | `/prs/{id}/diff`   | Live whole-PR unified diff as raw text |
| GET  | `/prs/{id}/threads` | Live inline review threads (GraphQL read) |
| GET/PATCH | `/prs/{id}/draft` | Get / update the local pending review (verdict + body) |
| POST/DELETE | `/prs/{id}/draft/comments[/{index}]` | Queue / remove an inline comment on the draft |
| POST | `/prs/{id}/review` | Submit the review (APPROVE/REQUEST_CHANGES/COMMENT) + queued comments |
| POST | `/prs/{id}/comments/{comment_id}/reply` | Reply to a review-thread comment |
| POST | `/prs/{id}/threads/{thread_id}/resolve` | Resolve / unresolve a thread |
| POST | `/prs/{id}/merge` | Merge the PR (squash default) |

Read = GraphQL (threads); writes = REST (review, reply, merge); thread
resolution = GraphQL mutation. The pending review is held locally in
`github_review_drafts` until submitted, so inline comments can be queued and
edited before they post to GitHub.

## WebSocket Messages

Inbound (client → server):

| Type | Effect |
|------|--------|
| `list_github_prs` | Server replies with `github_pr_list` — all cached PRs for this backend, newest first |
| `start_pr_assist` | `{pr_id, kind: "summary"\|"explain", file_path?}` — builds a seeded read-only one-shot session from the PR's diff and acks with its `session_id`; the analysis streams into that session like any agent turn (no file mutation) |

Outbound (server → all connected output clients), mirroring the Linear broadcasts:

| Type | Payload |
|------|---------|
| `github_pr_list` | `{prs: [...]}` — full cached snapshot (reply to `list_github_prs`) |
| `github_pr_update` | a single serialised PR (fields as `GET /prs/{id}`) — emitted on each sync upsert |
| `github_pr_deleted` | `{id}` — a PR removed from the cache |

## Configuration

| Key | Default | Purpose |
|-----|---------|---------|
| `GITHUB_TOKEN` | — | Personal access token (`repo` + `read:org`). Masked secret. |
| `GITHUB_DEFAULT_REPO` | — | Optional `owner/name` to scope PR listing; blank = all accessible repos. |
| `GITHUB_SYNC_ON_STARTUP` | `false` | Sync pull requests on server startup. |
