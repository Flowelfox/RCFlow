# RCFlow — Claude Code Interaction Guide

This document is a practical guide for how RCFlow should structure its interactions with Claude Code sessions. It is based on a detailed technical analysis of Claude Code's internal architecture (revealed via the March 2026 npm sourcemap leak) and is tailored to RCFlow's role as a Linux-based session launcher.

---

## 1. Understanding Claude Code Internals That Matter

### System Prompt Architecture

Claude Code splits every system prompt into two zones at a `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` marker:

- **Static (cacheable)** — content that does not change between requests. Cached across sessions to save tokens.
- **Dynamic (uncached)** — user- and session-specific content that is appended after the boundary and breaks the cache when changed.

**Implication for RCFlow:** Put stable project context (description, conventions, tool list, Design.md summary) first. Put session-specific state (working branch, task description, current date) last. Any content that changes per-session should always come after stable content to maximize cache hits.

### Permission Modes

Claude Code has four permission modes:

| Mode | Behavior |
|------|----------|
| `default` | Prompts user interactively for risky actions |
| `auto` | ML-based transcript classifier auto-approves based on inferred risk |
| `bypass` | Skips all permission checks |
| `yolo` | Confusingly named — **denies all** (max restriction) |

The `--dangerously-skip-permissions` flag enables the `bypass` mode. An ML-based **YOLO classifier** makes real-time decisions in `auto` mode by analyzing what the action does.

**Protected files** that are always guarded regardless of mode: `.gitconfig`, `.bashrc`, `.zshrc`, `.mcp.json`, `.claude.json`, `.claude/settings.json`.

### Task Budgets and Turn Limits

Claude Code has a `task-budgets` beta mechanism that limits how much a session can spend. RCFlow's existing `max_turns` setting is the correct lever on the launch side. The two work together.

### Memory / Dream System

After **5 sessions** and **24 hours**, Claude Code spawns a read-only background subagent called "autoDream" that consolidates memory into `MEMORY.md`. It runs four phases: Orient → Gather Signal → Consolidate → Prune.

RCFlow's memory directory at `~/.local/share/rcflow/tools/claude-code/config/projects/...` is exactly where this lands. Sessions that share a stable working directory will accumulate useful project memory over time.

### Multi-Agent Coordinator Mode

Enabled via `CLAUDE_CODE_COORDINATOR_MODE=1`. The coordinator dispatches parallel workers through four phases:

1. **Research** — workers investigate concurrently
2. **Synthesis** — coordinator reads findings and writes a spec
3. **Implementation** — workers apply targeted changes
4. **Verification** — workers test and confirm

The internal prompt explicitly bans lazy delegation: *"Do NOT say 'based on your findings' — read the actual findings and specify exactly what to do."*

---

## 2. Prompting Structure

### Do

- **Front-load stable context.** Project name, stack, conventions, and any relevant Design.md sections should come first. The task description comes last.
- **State acceptance criteria explicitly.** Tell Claude what "done" looks like: which tests should pass, which file should be modified, what the output should contain.
- **Name the files and functions.** If you know what needs to change, say it. Claude performs better with explicit targets than with inferred ones.
- **Embed a verification step.** End every implementation prompt with: *"After making changes, run `uv run pytest tests/` and confirm all tests pass before stopping."*
- **Use directive language.** *"Modify `src/executor.py` to add retry logic to `_run_command`"* is better than *"Can you look at the executor and maybe add some retry logic?"*

### Do Not

- **Do not ask open-ended questions for implementation tasks.** Claude will explore instead of implement.
- **Do not describe the task in terms of your intent.** Describe it in terms of the required outcome.
- **Do not omit the working directory.** Always pass `--working-dir` as an absolute canonical path, never rely on the prompt text to communicate the project root.
- **Do not include user-specific session context before stable project context.** It breaks the prompt cache on every call.

### Template

```
[Project context — Design.md summary, stack, conventions]
[Specific file targets and their current behavior]
[Required change with acceptance criteria]
[Verification command to run after changes]
```

---

## 3. Task Decomposition

Classify tasks before launching a session. Do not use Claude Code for every request — some are better served by direct API calls or simpler tools.

| Task Type | Recommended Approach |
|-----------|----------------------|
| Single-file read or search | Direct Grep/Glob — no Claude Code session needed |
| Simple code change (1–2 files, clear spec) | Single Claude Code session, `max_turns=10–20` |
| Feature implementation (multi-file, design required) | Single session with structured prompt, `max_turns=30–50` |
| Large refactor or architecture change | Worktree + structured phases (Research → Implement → Verify) |
| Parallel independent tasks | Separate worktrees, separate Claude Code sessions |

For complex tasks, break them into checkpointed phases and launch separate sessions for each. Continuity is maintained through CLAUDE.md and memory, not by keeping a single long-running session alive.

---

## 4. Repo Preparation Before Launching Sessions

Claude Code performs best when the repository is in a known-good state before launch.

1. **Ensure `CLAUDE.md` is current.** The CLAUDE.md is the single most important piece of context Claude Code sees. Keep it updated with conventions, rules, and any recent project decisions.

2. **Ensure `Design.md` is accurate.** Per RCFlow's own rules: all architectural decisions must be reflected in Design.md. Claude reads this on every new task.

3. **Start from a clean git working tree.** If there are uncommitted changes, Claude may be confused about what is baseline and what is new. Run `git stash` or commit before launching.

4. **Use absolute canonical paths.** Never pass `~/Projects/RCFlow` — expand to `/home/flowelfox/Projects/RCFlow`. Claude Code's path traversal guards normalize paths, so mismatches can cause silent failures.

5. **Do not pre-create files Claude should create.** Empty placeholder files confuse Claude about whether to overwrite or extend.

6. **For non-trivial changes, always use a worktree.** Use `wt new` (the project-bundled `wtpython` CLI) to isolate the change. This gives Claude a clean branch and prevents interference with main.

---

## 5. Verification Loops

Do not trust a Claude Code session's self-reported success. Build verification into the session and into RCFlow's post-session handling.

### In-Prompt Verification

Always include a final verification step in the prompt:

```
After completing all changes:
1. Run `uv run ruff check src/` — fix any lint errors before finishing.
2. Run `uv run pytest tests/ -x` — stop and report if any test fails.
3. Run `git diff --stat` — confirm only the expected files were modified.
```

### Post-Session Verification in RCFlow

After a Claude Code session exits, RCFlow should:

1. **Check the exit code.** A non-zero exit suggests Claude was interrupted or hit an error.
2. **Check `git status`.** If unexpected files are modified or staged, flag it to the user.
3. **Check for `CLAUDE.md` modifications.** Claude should not modify CLAUDE.md unilaterally — surface this as a warning.
4. **Run the test suite** if the session performed code changes. This can be a post-hook or a follow-up session.

---

## 6. Guardrails and Tool Allowances

### Restrict Tools to Minimum Needed

The full Claude Code tool list has 40+ tools. Grant only what the task requires:

| Task Category | Suggested `--allowedTools` |
|---------------|---------------------------|
| Code search / read-only analysis | `Read,Glob,Grep,Bash` (read-only commands only) |
| Code modification | `Read,Glob,Grep,Edit,Write,Bash` |
| Dependency or package work | Add `Bash` with unrestricted shell |
| Web research | Add `WebFetch,WebSearch` |
| Full agentic session | All tools (use worktree + max_turns limit) |

Use `--disallowedTools` to explicitly block dangerous tools when they are not needed. Even if the session is generally open, blocking `Bash` for read-only tasks prevents accidents.

### max_turns

Always set `max_turns`. Defaults should be:

- Simple queries: 5–10 turns
- Code changes: 15–30 turns
- Agentic feature work: 40–60 turns
- Open-ended exploration: ≤ 80 turns with user confirmation beyond that

Claude Code's internal task budget mechanism also applies limits, but `max_turns` is RCFlow's direct control surface.

### Worktrees for All Non-Trivial Work

This is already a project rule. Claude Code's internal `EnterWorktreeTool` is blocked via `.claude/settings.local.json` — always use `wt new` instead. This ensures:

- Branch naming follows RCFlow conventions
- Environment files are copied
- Dependencies are installed in the new worktree
- Cleanup is handled by `wt rm` / `wt merge`

### Environment Variables

These environment variables must be set on **every** Claude Code session RCFlow launches:

```
CLAUDE_CODE_UNDERCOVER=1                      # strip AI attribution from all commits/PRs
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1    # disable telemetry and update checks
BASH_DEFAULT_TIMEOUT_MS=30000                 # fail fast on hung shell commands
BASH_MAX_TIMEOUT_MS=120000                    # hard ceiling for any Bash call
```

`CLAUDE_CODE_UNDERCOVER=1` is unconditional — RCFlow sessions should never embed AI attribution in git history regardless of whether the repo is public or private.

For complex multi-file tasks, also add:
```
CLAUDE_CODE_COORDINATOR_MODE=1                # enables parallel worker agents
```

### Protect Sensitive Files

Even though Claude Code guards `.gitconfig`, `.bashrc`, etc. internally, RCFlow should explicitly add these to `--disallowedTools` or the deny list in `settings.json` for defense in depth. Never launch a session with `bypass` mode against a user's home directory.

---

## 7. Session Management

### Session Identity

Each Claude Code session should have a clear identity: project root + task description + branch. RCFlow should record this identity in the session database so post-session verification can reference it.

### Context Handoff Between Sessions

Claude Code does not carry conversation history between separate process invocations unless memory files are present. RCFlow should ensure:

1. The working directory is consistent across related sessions (so memory accumulates).
2. For multi-session tasks, each subsequent session's prompt references what the previous session completed: *"Session 1 completed X. This session should do Y."*
3. Design.md and CLAUDE.md are committed before the next session starts — they are Claude's primary cross-session memory.

### Handling Long-Running Sessions

If a session runs past `max_turns` without completing the task:

1. Capture the final output as context.
2. Launch a new session with: *"The previous session made the following progress: [summary]. Continue from this point: [next step]."*
3. Do not extend `max_turns` indefinitely — decompose further instead.

### Session Logging

RCFlow should persist:
- The full prompt sent to each session
- The session's final stdout/stderr
- `git diff` at session end
- Exit code and elapsed turns

This log is the ground truth for debugging failed sessions and for building better prompts over time.

---

## 8. When to Use Claude Code vs. Direct API

Claude Code is a full agentic system with tool use overhead. Not every task warrants it.

| Use Claude Code when... | Use direct Anthropic API when... |
|------------------------|----------------------------------|
| Task requires reading/modifying files | Task is a classification or single-step reasoning |
| Task requires shell commands | Output is a structured JSON response |
| Task benefits from iterative tool use | Task is stateless and under 1 tool call |
| Verification requires running tests | Task is a quick answer or search |
| Task is multi-step with branching | Task is deterministic and short |

For short queries from the mobile client that don't require code changes, RCFlow should route to the direct Anthropic Messages API, not a full Claude Code session. This saves cost and latency.

---

## 9. Reducing Ambiguity

Ambiguity is the primary cause of Claude Code doing the wrong thing. Eliminate it at the prompt level.

**Ambiguous:** `"Fix the session handling bug"`
**Better:** `"In src/core/session.py, the _cleanup method does not await pending tasks before closing. Add await asyncio.gather(*self._tasks, return_exceptions=True) before self._tasks.clear() on line ~85."`

**Ambiguous:** `"Add tests for the executor"`
**Better:** `"Add pytest tests to tests/test_executors/test_http.py covering: (1) successful GET request, (2) timeout (mock with a 5s delay), (3) non-2xx status code. Use the existing test fixtures in conftest.py."`

**Ambiguous:** `"Refactor the code"`
**Better:** `"Extract the retry logic from src/executors/http.py:_run into a standalone _retry_request helper with the same signature. Do not change behavior."`

---

## 10. UX Improvements for RCFlow Users

When RCFlow surfaces Claude Code sessions to the user:

1. **Show the working directory and branch before starting.** Users should confirm the session is targeting the right context.

2. **Show `max_turns` remaining** as a progress indicator. Users get anxious when sessions run long with no feedback.

3. **Surface git diff at session end.** Show a compact `git diff --stat` summary so users immediately see what changed.

4. **Warn on unclean git state before launch.** If the working tree has uncommitted changes, prompt the user to stash or commit first.

5. **Distinguish read-only from write sessions** in the UI. A session with only `Read,Glob,Grep` tools is safe to run unattended; one with `Edit,Write,Bash` warrants attention.

6. **For failed sessions**, show the last 20 lines of output and the exit code — not just "session failed." This is enough for the user to understand what went wrong and whether to retry.

---

## Summary of Key Rules

1. Always set `CLAUDE_CODE_UNDERCOVER=1` — no AI attribution in commits or PRs, ever.
2. Always set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and Bash timeout vars on every session.
3. Front-load stable project context, put task-specific content last.
4. Always specify acceptance criteria and a verification command in the prompt.
5. Always pass `--working-dir` as an absolute path.
6. Start sessions from a clean git working tree.
7. Use `wt new` for all non-trivial code changes.
8. Set `max_turns` appropriate to the task complexity.
9. Grant minimum necessary tools via `--allowedTools`.
10. Verify the session result via git diff and test suite — don't trust self-reported success.
11. Decompose complex tasks into checkpointed phases across multiple sessions.
12. Route simple queries to the direct API, not Claude Code.
