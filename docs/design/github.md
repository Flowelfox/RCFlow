---
updated: 2026-06-05
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
| **4b** *(backend done)* | Git-mutating assist: **apply-fix** runs a full-perms agent session in the worktree you pick, seeded with a review comment; **open-pr** pushes that worktree's branch (PAT auth) and opens the PR. Client buttons land next. Fork PRs are best-effort (same-repo origin assumed). |

## HTTP Endpoints

All under `/api/integrations/github/`, `X-API-Key` required. See [HTTP API](http-api.md#github-integration).

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/status`          | Token configured + validity preflight for the saved token (`{configured, valid, login, scopes}`) |
| POST | `/status/check`    | Validate an unsaved token `{token}` → same shape as `/status` (settings live preview) |
| GET/PUT | `/repo-defaults` | This worker's default-action repos; PUT `{owner, repo, is_default}` sets/clears (cross-worker action routing) |
| POST | `/sync`            | Re-sync PRs — the 50 most-recently-updated across all states (open/merged/closed) per `?role=for_me\|created` (default both), so the client can filter by state → `{synced, archived_pruned, configured}`. No token → no-op (`synced:0, configured:false`), not an error. Each PR is enriched via GraphQL with `review_decision` + `merge_status` for a per-PR status badge |
| GET  | `/prs`             | List cached PRs (`?role=`, `?state=`, `?q=`) → `{prs, total}` |
| GET  | `/prs/{id}`        | Single cached PR by local UUID |
| GET  | `/prs/{id}/files`  | Live changed files, each with a per-file unified-diff `patch` |
| GET  | `/prs/{id}/diff`   | Live whole-PR unified diff as raw text |
| GET  | `/prs/{id}/file`   | A file's full text at the PR head/base (`?path=&side=`) — for expanding diff context |
| GET  | `/prs/{id}/threads` | Live inline review threads (GraphQL read) |
| GET/POST | `/prs/{id}/conversation` | Global (issue-level) comments + review summaries as a timeline; POST adds a global comment |
| GET/PATCH | `/prs/{id}/draft` | Get / update the local pending review (verdict + body) |
| POST/DELETE | `/prs/{id}/draft/comments[/{index}]` | Queue / remove an inline comment on the draft (POST takes `start_line`/`start_side` for a multi-line range) |
| POST | `/prs/{id}/review` | Submit the review (APPROVE/REQUEST_CHANGES/COMMENT) + queued comments |
| POST | `/prs/{id}/comments/{comment_id}/reply` | Reply to a review-thread comment |
| POST | `/prs/{id}/threads/{thread_id}/resolve` | Resolve / unresolve a thread |
| GET  | `/prs/{id}/conflicts` | Merge-conflict / mergeability status → `{conflicted, files, mergeable, reason, mergeable_state}` |
| POST | `/prs/{id}/merge` | Merge the PR (squash default) |
| POST | `/open-pr` | Push a selected worktree's branch (PAT auth) and open a PR for it |

The `open-pr` push authenticates with `GITHUB_TOKEN` supplied through a
temporary `GIT_ASKPASS` helper, so the token never appears in argv, git config,
or logs (`src/services/git_ops.py`).

Inline review threads (line-anchored) are separate from the **conversation**:
`/prs/{id}/conversation` returns the PR's general issue-level comments merged
with submitted review summaries (approve / request-changes / comment notes) as a
single oldest-first timeline; POST adds a general comment. The client shows this
in a resizable, collapsible panel docked beneath the diff, with a composer to post.

**Cross-worker dedup.** PRs are cached per worker (`backend_id`), so pointing
several workers at one account caches the same PR once per worker. The client
deduplicates by `github_id`: one tile, with a "Worker / Project" badge per
backing worker. Each sync stamps `project_name`/`project_path` (the local
checkout that worker maps the repo to) so the client knows which workers can run
writable actions locally. Writable actions (resolve-conflicts / fix) route to a
worker via per-worker `/repo-defaults` flags: 1 default → use it, 0 → ask, ≥2 →
clear all + ask. `/sync?force=` bypasses the 60s auto-sync recency throttle
(manual refresh forces; auto skips when synced `<60s` ago).

Read = GraphQL (threads); writes = REST (review, reply, merge); thread
resolution = GraphQL mutation. The pending review is held locally in
`github_review_drafts` until submitted, so inline comments can be queued and
edited before they post to GitHub.

`/sync` (and the startup sync) drop PRs whose repository is **archived** —
they are read-only and can't be reviewed or merged. Archived-repo PRs are never
cached, and any previously-cached rows for a repo that has since been archived
are pruned (`_persist_synced_prs`) and a `github_pr_deleted` broadcast is sent so
the client drops them live.

`/prs/{id}/conflicts` reports whether the PR conflicts with its base. GitHub's
API reports only *that* a PR conflicts (`mergeable`/`mergeable_state` on the PR
detail), so the conflicting **file list** is computed from a local 3-way merge
(`git merge-tree --write-tree`, `src/services/git_ops.py`) against the checkout
found in `PROJECTS_DIR`. Nothing is written to the working tree. The response is
`{conflicted, files, mergeable, reason, mergeable_state}`; `reason` is one of
`clean`, `computing` (GitHub still computing — retry), `conflicting`,
`no_local_clone` (conflict confirmed but the file list could not be computed —
no local clone, or git < 2.38), or `blocked` (conflict-free but blocked by
branch protection / repository rules — required reviews or status checks, e.g.
"Review required"). When the PR conflicts the client disables Merge and shows a
banner listing the files plus a **Resolve with agent** button (the
`resolve_conflicts` assist); when blocked it disables Merge and shows a rules
warning. `/prs/{id}/merge` maps GitHub's HTTP 405 (not mergeable) to a `409` with
a clear message instead of a raw `502`.

## WebSocket Messages

Inbound (client → server):

| Type | Effect |
|------|--------|
| `list_github_prs` | Server replies with `github_pr_list` — all cached PRs for this backend, newest first |
| `start_pr_assist` | `{pr_id, kind, file_path?, line?, comment_body?, project_name?, project_path?, selected_worktree_path?}` — `project_path` (the PR's git-remote-resolved checkout) is applied directly so the session opens in the same project as the PR, not a same-named folder found by name. — acks a `session_id` and streams the assist into it. `summary`/`explain` are read-only diff analysis; `fix` and `resolve_conflicts` run a **full-perms** agent session in the local checkout. `fix` addresses `comment_body`; `resolve_conflicts` merges the base into the PR head and resolves conflicts (the optional `comment_body` carries the conflicting file list as a hint), then reports what it fixed / how / why and asks the human for permission before committing & pushing. `fix` edits the tree but never pushes |

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
