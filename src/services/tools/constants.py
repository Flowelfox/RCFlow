"""Download endpoints, timeouts, and compatibility thresholds for managed tools."""

from __future__ import annotations

CLAUDE_GCS_BUCKET = (
    "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
)

CODEX_GITHUB_RELEASES_API = "https://api.github.com/repos/openai/codex/releases/latest"
CODEX_RELEASE_BASE = "https://github.com/openai/codex/releases/download"

OPENCODE_GITHUB_RELEASES_API = "https://api.github.com/repos/sst/opencode/releases/latest"
OPENCODE_RELEASE_BASE = "https://github.com/sst/opencode/releases/download"

# Timeout for binary downloads (large files)
_DOWNLOAD_TIMEOUT = 300
# Timeout for version/metadata checks
_CHECK_TIMEOUT = 15

# Minimum glibc version known to work with recent Codex releases.
# When the system glibc is older, we proactively use the musl (static) variant
# to avoid a failed install + retry.  The post-install verification still acts
# as a safety net in case this threshold becomes stale.
_CODEX_MIN_GLIBC = (2, 38)
