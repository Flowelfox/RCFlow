"""Artifact scanner service for extracting file artifacts from session messages."""

import fnmatch
import logging
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.database.models import Artifact, SessionMessage
from src.database.models import Session as SessionModel

logger = logging.getLogger(__name__)

# Regex patterns to extract file paths from text.
# Matches absolute paths like /home/user/file.md or ~/file.md
# and relative paths like ./file.md or ../file.md.
# Bare relative paths (e.g. src/file.md) are NOT matched; they are resolved
# by applying the session's main_project_path prefix at resolution time.
_FILE_PATH_RE = re.compile(
    r"""(?:^|[\s"'`({\[,;:=])"""  # boundary before path
    r"""("""
    r"""(?:[~]?/[\w.+\-]+(?:/[\w.+\-]+)*"""  # absolute or ~/ path
    r"""|\.{1,2}/[\w.+\-]+(?:/[\w.+\-]+)*)"""  # relative ./ or ../ path
    r""")"""
    r"""(?=[\s"'`)}:\],;!?]|$)""",  # boundary after path
    re.MULTILINE,
)


class ArtifactScanner:
    """Service for extracting file artifacts from session messages.

    Parses session conversation messages for file paths, verifies the files
    exist on disk, and tracks them as artifacts in the database.
    """

    def __init__(self, settings: Settings, db_session_factory: async_sessionmaker[AsyncSession]):
        self.settings = settings
        self.db_session_factory = db_session_factory
        self.include_pattern = settings.ARTIFACT_INCLUDE_PATTERN
        self.exclude_patterns = [
            p.strip().removesuffix("/**").removesuffix("/*")
            for p in settings.ARTIFACT_EXCLUDE_PATTERN.split(",")
            if p.strip()
        ]
        self.backend_id = settings.RCFLOW_BACKEND_ID

    def _should_include_file(self, file_path: Path) -> bool:
        """Check if a file should be included based on patterns."""
        if not fnmatch.fnmatch(file_path.name.lower(), self.include_pattern.lower()):
            return False

        # Check if any path component matches an exclude pattern
        for part in file_path.parts:
            for exclude_pattern in self.exclude_patterns:
                if fnmatch.fnmatch(part, exclude_pattern):
                    return False

        return True

    def _extract_paths_from_text(self, text: str) -> set[str]:
        """Extract file path candidates from a text string."""
        return set(_FILE_PATH_RE.findall(text))

    def _resolve_path(self, raw_path: str, project_path: Path | None = None) -> Path | None:
        """Resolve a raw path string to an absolute Path, or None if it doesn't exist.

        First tries standard resolution (absolute, expanduser, resolve against CWD).
        If that fails and *project_path* is provided, also tries resolving the path
        relative to the project directory.  This handles cases where tool outputs
        contain bare or dot-relative paths (e.g. ``./README.md``) whose CWD is
        the project root rather than the server's working directory.
        """
        try:
            p = Path(raw_path).expanduser().resolve()
            if p.is_file():
                return p
        except (OSError, ValueError):
            pass
        if project_path is not None:
            try:
                # Strip leading ./ or ../ to resolve relative to project root
                relative = Path(raw_path.lstrip("/"))
                p = (project_path / relative).resolve()
                if p.is_file():
                    return p
            except (OSError, ValueError):
                pass
        return None

    def _extract_paths_from_conversation(self, conversation_history: list[dict]) -> set[str]:
        """Extract file path candidates from a conversation history list."""
        paths: set[str] = set()
        for msg in conversation_history:
            content = msg.get("content")
            if isinstance(content, str):
                paths.update(self._extract_paths_from_text(content))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        for field in ("text", "content"):
                            val = block.get(field)
                            if isinstance(val, str):
                                paths.update(self._extract_paths_from_text(val))
        return paths

    async def _upsert_artifacts(
        self,
        db: AsyncSession,
        candidate_paths: set[str],
        session_id: uuid.UUID,
        project_path: Path | None = None,
    ) -> tuple[int, int]:
        """Resolve candidate paths, filter, and upsert artifact records.

        Args:
            db: Active database session.
            candidate_paths: Raw path strings extracted from messages.
            session_id: Session to associate newly discovered artifacts with.
            project_path: Optional project root directory used as a fallback base
                when a candidate path cannot be resolved from the server's CWD.

        Returns:
            Tuple of (new_count, updated_count).
        """
        if not candidate_paths:
            return 0, 0

        new_count = 0
        updated_count = 0

        # Check if the session row exists in the DB.  During real-time scanning
        # the session is still in-memory only, so the FK would violate the
        # foreign_keys constraint.  Use None when the row is absent.
        session_row = await db.get(SessionModel, session_id)
        safe_session_id: uuid.UUID | None = session_id if session_row is not None else None

        art_stmt = select(Artifact).where(Artifact.backend_id == self.backend_id)
        art_result = await db.execute(art_stmt)
        existing_artifacts = {a.file_path: a for a in art_result.scalars()}

        for raw_path in candidate_paths:
            file_path = self._resolve_path(raw_path, project_path)
            if file_path is None:
                continue

            if not self._should_include_file(file_path):
                continue

            try:
                stat = file_path.stat()
                file_size = stat.st_size
                modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            except OSError:
                logger.debug("Could not stat file: %s", file_path)
                continue

            if file_size > self.settings.ARTIFACT_MAX_FILE_SIZE:
                logger.debug("Skipping large file: %s (%d bytes)", file_path, file_size)
                continue

            file_path_str = str(file_path)
            file_name = file_path.name
            file_extension = file_path.suffix.lower()
            mime_type, _ = mimetypes.guess_type(file_path_str)

            existing = existing_artifacts.get(file_path_str)
            if existing:
                changed = existing.modified_at != modified_at or existing.file_size != file_size
                if changed:
                    existing.modified_at = modified_at
                    existing.file_size = file_size
                    existing.file_name = file_name
                    existing.file_extension = file_extension
                    existing.mime_type = mime_type
                    updated_count += 1
                    logger.debug("Updated artifact: %s", file_path_str)
                # Back-fill session_id for artifacts discovered during
                # real-time scanning (when the session wasn't in the DB yet).
                if not existing.session_id and safe_session_id:
                    existing.session_id = safe_session_id
            else:
                artifact = Artifact(
                    backend_id=self.backend_id,
                    file_path=file_path_str,
                    file_name=file_name,
                    file_extension=file_extension,
                    file_size=file_size,
                    mime_type=mime_type,
                    modified_at=modified_at,
                    session_id=safe_session_id,
                )
                db.add(artifact)
                existing_artifacts[file_path_str] = artifact
                new_count += 1
                logger.debug("Discovered new artifact: %s", file_path_str)

        await db.commit()
        return new_count, updated_count

    async def scan(self, session_id: str | uuid.UUID) -> int:
        """Extract artifacts from archived session messages.

        Reads conversation_history and session messages from the database,
        extracts file paths, verifies they exist on disk and match the
        configured include/exclude patterns, and adds them as artifacts.

        Args:
            session_id: Session ID whose messages to scan for file paths.

        Returns:
            Number of new artifacts discovered.
        """
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        logger.info("Extracting artifacts from session messages: %s", session_id)

        async with self.db_session_factory() as db:
            candidate_paths: set[str] = set()

            # 1. Scan conversation_history (complete messages, not streaming chunks)
            session_row = await db.get(SessionModel, session_id)
            if session_row and session_row.conversation_history:
                candidate_paths.update(self._extract_paths_from_conversation(session_row.conversation_history))

            # 2. Scan individual SessionMessage rows (tool outputs, metadata)
            msg_stmt = (
                select(SessionMessage).where(SessionMessage.session_id == session_id).order_by(SessionMessage.sequence)
            )
            msg_result = await db.execute(msg_stmt)
            for msg in msg_result.scalars():
                if msg.content:
                    candidate_paths.update(self._extract_paths_from_text(msg.content))
                if msg.metadata_:
                    for value in msg.metadata_.values():
                        if isinstance(value, str):
                            candidate_paths.update(self._extract_paths_from_text(value))

            if not candidate_paths:
                logger.debug("No file paths found in session %s messages", session_id)
                return 0

            logger.debug("Found %d candidate paths in session %s", len(candidate_paths), session_id)
            # Use main_project_path from the archived session row as a fallback
            # base directory for relative path resolution.
            project_path: Path | None = None
            if session_row and session_row.main_project_path:
                project_path = Path(session_row.main_project_path)
            new_count, updated_count = await self._upsert_artifacts(db, candidate_paths, session_id, project_path)

        logger.info(
            "Artifact extraction complete for session %s: %d new, %d updated",
            session_id,
            new_count,
            updated_count,
        )
        return new_count

    async def scan_texts(
        self,
        session_id: str | uuid.UUID,
        texts: list[str],
        project_path: Path | None = None,
    ) -> tuple[int, int]:
        """Extract artifacts from a list of raw text strings (real-time).

        Useful for scanning text that isn't in conversation_history format,
        e.g. tool outputs or Claude Code relay events.

        Args:
            session_id: Session ID to associate with discovered artifacts.
            texts: Raw text strings to scan for file paths.
            project_path: Optional project root directory used as a fallback base
                when a candidate path cannot be resolved from the server's CWD.

        Returns:
            Tuple of (new_count, updated_count).
        """
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        candidate_paths: set[str] = set()
        for text in texts:
            candidate_paths.update(self._extract_paths_from_text(text))
        if not candidate_paths:
            return 0, 0

        logger.debug(
            "Real-time text scan: %d candidate paths in session %s",
            len(candidate_paths),
            session_id,
        )

        async with self.db_session_factory() as db:
            new_count, updated_count = await self._upsert_artifacts(db, candidate_paths, session_id, project_path)

        if new_count > 0 or updated_count > 0:
            logger.info(
                "Real-time text scan for session %s: %d new, %d updated",
                session_id,
                new_count,
                updated_count,
            )
        return new_count, updated_count

    async def scan_from_history(
        self,
        session_id: str | uuid.UUID,
        conversation_history: list[dict],
        project_path: Path | None = None,
    ) -> tuple[int, int]:
        """Extract artifacts from in-memory conversation history (real-time).

        Called during an active session after each LLM turn to discover
        artifacts without waiting for session archival.

        Args:
            session_id: Session ID to associate with discovered artifacts.
            conversation_history: The session's in-memory conversation history.
            project_path: Optional project root directory used as a fallback base
                when a candidate path cannot be resolved from the server's CWD.

        Returns:
            Tuple of (new_count, updated_count).
        """
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        candidate_paths = self._extract_paths_from_conversation(conversation_history)
        if not candidate_paths:
            return 0, 0

        logger.debug(
            "Real-time scan: %d candidate paths in session %s",
            len(candidate_paths),
            session_id,
        )

        async with self.db_session_factory() as db:
            new_count, updated_count = await self._upsert_artifacts(db, candidate_paths, session_id, project_path)

        if new_count > 0 or updated_count > 0:
            logger.info(
                "Real-time scan for session %s: %d new, %d updated",
                session_id,
                new_count,
                updated_count,
            )
        return new_count, updated_count
