"""Download endpoints, timeouts, and compatibility thresholds for managed tools."""

from __future__ import annotations

CLAUDE_GCS_BUCKET = (
    "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
)

CODEX_GITHUB_RELEASES_API = "https://api.github.com/repos/openai/codex/releases/latest"
CODEX_RELEASE_BASE = "https://github.com/openai/codex/releases/download"

OPENCODE_GITHUB_RELEASES_API = "https://api.github.com/repos/sst/opencode/releases/latest"
OPENCODE_RELEASE_BASE = "https://github.com/sst/opencode/releases/download"

# codex-acp — the ACP (Agent Client Protocol) adapter for OpenAI Codex.
#
# Release repo decision (verified 2026-07-07 via the GitHub API):
# github.com/agentclientprotocol/codex-acp publishes releases (latest v1.1.0)
# but with NO downloadable binary assets, while github.com/zed-industries/codex-acp
# publishes per-platform binaries for every release
# (e.g. ``codex-acp-0.16.0-x86_64-unknown-linux-gnu.tar.gz``), so RCFlow
# installs from zed-industries.  Asset naming scheme (verified against the
# v0.16.0 release): ``codex-acp-<version>-<rust target triple>.tar.gz`` on
# Linux/macOS (single root-level ``codex-acp`` file inside) and
# ``codex-acp-<version>-<rust target triple>.zip`` on Windows (single
# root-level ``codex-acp.exe`` inside).  Target triples are identical to the
# Codex ones produced by ``_detect_codex_target``.
CODEX_ACP_GITHUB_RELEASES_API = "https://api.github.com/repos/zed-industries/codex-acp/releases/latest"
CODEX_ACP_RELEASE_BASE = "https://github.com/zed-industries/codex-acp/releases/download"

# Timeout for binary downloads (large files)
_DOWNLOAD_TIMEOUT = 300
# Timeout for version/metadata checks
_CHECK_TIMEOUT = 15

# Minimum glibc version known to work with recent Codex releases.
# When the system glibc is older, we proactively use the musl (static) variant
# to avoid a failed install + retry.  The post-install verification still acts
# as a safety net in case this threshold becomes stale.
_CODEX_MIN_GLIBC = (2, 38)
