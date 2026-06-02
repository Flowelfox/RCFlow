"""Live-tracking of an agent's working directory across a session.

Managed agents (Claude Code / Codex / OpenCode) often issue ``cd`` or
``git worktree`` commands inside their Bash tool calls.  Those changes
are persistent in the agent's shell session and therefore in the
agent's notion of "where I am", but RCFlow's session metadata is
captured only at spawn time.  Without tracking them, the worktree
badge on the session chip drifts away from reality the moment the
agent moves between worktrees.

This module exposes two small, pure helpers (easy to unit-test):

- :func:`parse_cwd_change` recovers the post-``cd`` working directory
  from a shell command string;
- :func:`resolve_worktree_for_cwd` maps a directory to the Git
  worktree it lives in (cached briefly so a Bash-heavy turn does not
  re-shell-out per call).

Plus a tiny session-helper, :func:`apply_agent_cwd`, that ties them
together and updates the session metadata used by the badge renderer
in :mod:`src.core.badges`.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wtpython import GitOperationError, NotInGitRepository, WorktreeManager

if TYPE_CHECKING:
    from src.core.session import ActiveSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeMatch:
    """A Git worktree the agent is currently inside."""

    repo_path: str
    path: str
    branch: str
    base: str | None


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------


# Match a leading ``cd <path>`` or ``pushd <path>`` token at the start of the
# command (or following an ``&&`` chain).  We deliberately stop at any of
# ``;``, ``&&``, ``||``, ``|``, ``>``, ``<`` so a follow-up command in the
# same line does not pollute the captured path.
_CD_TOKEN = re.compile(
    r"""
    (?:^|&&\s*)                          # start of command or after &&
    \s*
    (?:pushd|cd)                         # cd or pushd
    \s+
    (?:--\s+)?                           # optional `--` end-of-options
    (?P<path>
        "[^"]+"                          # double-quoted
        | '[^']+'                        # single-quoted
        | [^\s;&|<>]+                    # bare token (no shell separator)
    )
    """,
    re.VERBOSE,
)

# Subshell wrappers like ``( cd /x && y )`` do *not* persist the cwd in the
# outer shell.  Reject early so we don't trip on them.
_SUBSHELL = re.compile(r"\(\s*(?:pushd|cd)\b")


def parse_cwd_change(command: str, current_cwd: str | None) -> str | None:
    """Return the post-``cd`` absolute path *command* would produce, or None.

    Recognises the common forms agents emit:

    - ``cd /abs/path`` — absolute target.
    - ``cd path`` — relative, joined onto *current_cwd*.
    - ``cd ~/path`` and ``cd ~`` — HOME-expanded.
    - ``cd -`` — explicitly *unsupported*; returns None (the new cwd
      depends on the shell's OLDPWD which we don't track).
    - ``cd /x && something`` — the ``cd`` persists for follow-ups, so we
      report ``/x``.

    Subshell forms (``( cd /x && y )``) are rejected because the cwd
    change is local to the subshell.

    Returns None when the command does not change the cwd persistently.
    """
    if not command:
        return None
    if _SUBSHELL.search(command):
        return None

    match = _CD_TOKEN.search(command)
    if not match:
        return None
    raw = match.group("path").strip()
    # Strip matching quotes.
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]
    # ``cd -`` semantics depend on OLDPWD — bail.
    if raw == "-":
        return None
    # Shell tokens we don't try to resolve.
    if raw.startswith("$") or "*" in raw or "?" in raw or "$(" in raw or "`" in raw:
        return None

    # Resolve ~ / ~user.
    expanded = os.path.expanduser(raw)

    base = current_cwd or os.getcwd()
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = Path(base) / candidate
    try:
        # Resolve ``..`` segments without requiring the path to exist —
        # ``os.path.normpath`` is safe even when the directory has not been
        # created yet (e.g. the agent is about to ``mkdir`` it).
        return os.path.normpath(str(candidate))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Git worktree resolution (cached)
# ---------------------------------------------------------------------------


# Small in-memory cache so a burst of Bash calls (10x per turn is common)
# doesn't shell out to ``git worktree list`` each time.  Entries expire
# after :data:`_WORKTREE_CACHE_TTL_S` seconds.
_WORKTREE_CACHE_TTL_S: float = 1.0
_WorktreeCacheKey = str  # the resolved repo path
_worktree_cache: dict[_WorktreeCacheKey, tuple[float, list[WorktreeMatch]]] = {}


def _list_worktrees(repo_path: str) -> list[WorktreeMatch]:
    """Return all Git worktrees under *repo_path* (cached for 1 s)."""
    now = time.monotonic()
    entry = _worktree_cache.get(repo_path)
    if entry is not None and now - entry[0] < _WORKTREE_CACHE_TTL_S:
        return entry[1]
    try:
        manager = WorktreeManager(repo_path=repo_path)
        worktrees = manager.list()
    except (NotInGitRepository, GitOperationError, OSError, ValueError):
        # ``GitOperationError`` covers the ``git`` binary being absent or
        # failing — happens in CI sandboxes and on machines without git
        # installed.  Degrade silently so the badge falls back to its
        # last-known state rather than poisoning the whole event loop.
        worktrees = []
    matches = (
        [
            WorktreeMatch(
                repo_path=str(manager.repo_root) if worktrees else repo_path,
                path=str(wt.path),
                branch=wt.branch,
                base=wt.base,
            )
            for wt in worktrees
        ]
        if worktrees
        else []
    )
    _worktree_cache[repo_path] = (now, matches)
    return matches


def resolve_worktree_for_cwd(cwd: str, repo_path: str | None) -> WorktreeMatch | None:
    """Return the Git worktree *cwd* lives in, or None.

    When *repo_path* is provided the lookup is scoped to that
    repository; without it the helper bails (we have no anchor to
    enumerate worktrees from — agents that drift into an unrelated
    repo simply get no badge).

    Multiple candidate worktrees are disambiguated by longest matching
    prefix so a session inside ``.worktrees/foo/sub/`` resolves to
    ``foo`` rather than the repo root.
    """
    if not cwd or not repo_path:
        return None
    try:
        norm_cwd = os.path.normpath(cwd)
    except OSError:
        return None

    best: WorktreeMatch | None = None
    best_len = -1
    for wt in _list_worktrees(repo_path):
        try:
            wt_path = os.path.normpath(wt.path)
        except OSError:
            continue
        # ``Path.is_relative_to`` (Py 3.9+) — used via startswith for
        # tolerance of trailing separators.
        anchor = wt_path if wt_path.endswith(os.sep) else wt_path + os.sep
        if (norm_cwd == wt_path or norm_cwd.startswith(anchor)) and len(wt_path) > best_len:
            best = wt
            best_len = len(wt_path)
    return best


def reset_worktree_cache() -> None:
    """Drop the cached worktree lookups.  Used by tests."""
    _worktree_cache.clear()


# ---------------------------------------------------------------------------
# Session-level helper
# ---------------------------------------------------------------------------


def apply_agent_cwd(session: ActiveSession, new_cwd: str) -> bool:
    """Persist *new_cwd* on the session and refresh worktree metadata.

    Returns True when the cwd actually changed (so the caller can fire
    a single ``session_update`` broadcast) and False on a no-op.
    """
    if not new_cwd:
        return False
    previous = session.metadata.get("agent_cwd")
    if previous == new_cwd:
        return False
    session.metadata["agent_cwd"] = new_cwd
    # Mirror onto the session object for callers that prefer the field
    # over the metadata dict.
    session.agent_cwd = new_cwd

    repo_path = session.main_project_path
    match = resolve_worktree_for_cwd(new_cwd, repo_path)
    if match is not None:
        session.metadata["worktree"] = {
            "repo_path": match.repo_path,
            "branch": match.branch,
            "base": match.base,
            "last_action": "cd",
            "path": match.path,
        }
        # Stay consistent with what the worktree tool's ``attach`` flow
        # writes — downstream code (e.g. permission-mode resolution)
        # reads ``selected_worktree_path``.
        session.metadata["selected_worktree_path"] = match.path
    else:
        # cd into something that isn't a worktree (e.g. /tmp, an
        # unrelated repo).  Leave existing worktree metadata in place —
        # the badge will still reflect the last real worktree, the
        # tooltip / agent_cwd will reflect the truth.
        pass
    return True


# ---------------------------------------------------------------------------
# Bash-side detection of out-of-band worktree creation / removal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Claude Code session jsonl — authoritative cwd source
# ---------------------------------------------------------------------------


def claude_code_jsonl_path(cc_session_id: str, spawn_cwd: str) -> Path | None:
    """Construct the path Claude Code writes its per-session log to.

    Claude Code persists every event of a session — assistant messages,
    tool calls, tool results — as a line of JSON in
    ``~/.claude/projects/<encoded-spawn-cwd>/<session-id>.jsonl``.
    Each event carries the agent's current ``cwd``, so the *last*
    non-empty cwd in the file is an exact, command-agnostic readout of
    "where the agent considers itself right now".

    The encoded directory name uses the spawn cwd (the path Claude Code
    was launched in), not the agent's current cwd; subsequent ``cd`` /
    ``wt attach`` calls do not move the file.  Both ``/`` and ``.`` in
    the spawn path are replaced with ``-``.
    """
    if not cc_session_id or not spawn_cwd:
        return None
    encoded = spawn_cwd.replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / encoded / f"{cc_session_id}.jsonl"


_CWD_FIELD = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def latest_cwd_from_cc_jsonl(jsonl_path: Path) -> str | None:
    """Read *jsonl_path* and return the most recent ``cwd`` field, or None.

    Reads from the end of the file backwards so a multi-MB session log
    is cheap to query.  Falls through to None for missing files,
    permission errors, or empty results.
    """
    try:
        size = jsonl_path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    # Read the trailing portion; 64 KiB is enough for several hundred
    # events on a typical CC session.
    chunk = min(size, 64 * 1024)
    try:
        with jsonl_path.open("rb") as f:
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    last: str | None = None
    for match in _CWD_FIELD.finditer(data):
        candidate = match.group(1)
        if candidate:
            last = candidate
    return last


# ---------------------------------------------------------------------------
# Inference from tool input — works for any agent without command parsing
# ---------------------------------------------------------------------------


# Tool-input keys that commonly carry a filesystem path the agent is
# operating on.  Covers Claude Code's standard tools (Edit, Write, Read,
# Glob, Grep, NotebookEdit, MultiEdit) and the Codex / OpenCode
# equivalents that mirror the same names.
_PATH_FIELDS: tuple[str, ...] = ("file_path", "filePath", "path", "notebook_path", "filename")


def extract_paths_from_tool_input(tool_input: dict[str, object]) -> list[str]:
    """Return every plausible filesystem path mentioned in *tool_input*.

    Lifted to module scope so callers in the agent stream loops can
    invoke it without poking at the cwd-tracking internals.
    """
    if not isinstance(tool_input, dict):
        return []
    found: list[str] = []
    for key in _PATH_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    # ``MultiEdit`` and similar take an ``edits`` list whose items each
    # have a ``file_path`` field; walk one level into list-of-dicts so
    # we don't miss the path.
    for value in tool_input.values():
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                for key in _PATH_FIELDS:
                    sub = item.get(key)  # ty:ignore[invalid-argument-type]
                    if isinstance(sub, str) and sub:
                        found.append(sub)
    return found


def infer_cwd_from_tool_paths(
    paths: list[str],
    spawn_cwd: str | None,
    repo_path: str | None,
) -> str | None:
    """Pick the worktree the agent is most likely operating in.

    Resolves each path to an absolute form (relative to *spawn_cwd*
    when needed), maps it onto the known Git worktrees of *repo_path*
    via :func:`resolve_worktree_for_cwd`, and returns the worktree
    path.  The agent does not have to ``cd`` for this to fire — the
    *file* it edits or reads is enough to deduce where it is working.

    Multiple paths in the same call are voted: the worktree referenced
    by the most paths wins; ties broken by deepest worktree (most
    specific).  Returns None when no path maps to a worktree.
    """
    if not paths or not repo_path:
        return None
    matches: dict[str, int] = {}
    longest_for: dict[str, str] = {}
    for raw in paths:
        # ``..``-normalised, expanded, absolute.
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            base = spawn_cwd or os.getcwd()
            candidate = Path(base) / candidate
        try:
            abs_path = os.path.normpath(str(candidate))
        except OSError:
            continue
        wt = resolve_worktree_for_cwd(abs_path, repo_path)
        if wt is None:
            continue
        matches[wt.path] = matches.get(wt.path, 0) + 1
        longest_for[wt.path] = wt.path
    if not matches:
        return None
    # Sort by (vote count, path length) descending.
    best = max(matches.items(), key=lambda kv: (kv[1], len(kv[0])))
    return best[0]


def infer_cwd_from_output(output: str, repo_path: str | None) -> str | None:
    """Find the worktree path the agent's last command effectively put it in.

    No language / keyword heuristics — we only count *exact* matches
    against a registered Git worktree of *repo_path*.  Tools the agent
    might run that emit the new worktree's absolute path (``wt attach``,
    ``git worktree add``, custom switch scripts, ``pwd`` after a
    successful ``cd``) all win without any tool-specific code; tools
    that merely *mention* a worktree as part of a listing
    (``git worktree list``, ``ls /repo/.worktrees``) tie-break to the
    *last* path in the output — typically the one the user is asking
    to switch into.

    The match is intentionally conservative: an absolute path that is
    *not* a known worktree of *repo_path* is ignored, so unrelated
    paths in the output never trigger a spurious badge flip.
    """
    if not output or not repo_path:
        return None
    worktrees = _list_worktrees(repo_path)
    if not worktrees:
        return None
    best_path: str | None = None
    best_pos = -1
    for wt in worktrees:
        pos = output.rfind(wt.path)
        if pos < 0:
            continue
        # Prefer the right-most match overall; on ties prefer the deeper
        # worktree (``foo/sub`` over ``foo``).
        if pos > best_pos or (pos == best_pos and best_path is not None and len(wt.path) > len(best_path)):
            best_pos = pos
            best_path = wt.path
    return best_path


def looks_like_git_worktree_mutation(command: str) -> bool:
    """Heuristic for whether a Bash command created or destroyed a worktree.

    Used by the agent stream handlers to invalidate the worktree
    cache so a subsequent ``resolve_worktree_for_cwd`` picks up the
    fresh state without waiting for the TTL.
    """
    if not command:
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # Shell-quoting that we can't tokenise; fall back to a coarse
        # substring check so we don't miss the signal.
        return "git worktree add" in command or "git worktree remove" in command or "git worktree prune" in command
    if len(tokens) < 3:
        return False
    # Walk the token list looking for ``git ... worktree (add|remove|prune)``
    # so chained commands like ``cd /x && git worktree add ...`` still match.
    for i in range(len(tokens) - 2):
        if tokens[i] == "git" and tokens[i + 1] == "worktree" and tokens[i + 2] in {"add", "remove", "prune"}:
            return True
    return False
