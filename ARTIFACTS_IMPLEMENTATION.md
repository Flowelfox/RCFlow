# Artifacts Feature Implementation Plan

## Overview
The Artifacts feature allows RCFlow to discover, track, and display files (particularly markdown files) from configured directories. Users can view these artifacts directly within the RCFlow interface.

## Completed Tasks

### Backend Implementation

1. **Database Model & Migration**
   - [x] Added `Artifact` model to `src/models/db.py`
   - [x] Created migration `a1b2c3d4e5f7_add_artifacts_table.py`
   - Model fields: id, backend_id, file_path, file_name, file_extension, file_size, mime_type, discovered_at, modified_at, session_id

2. **Configuration Settings**
   - [x] Added artifact settings to `src/config.py`:
     - `ARTIFACT_INCLUDE_PATTERN`: Pattern for files to include (default: `*.md`, matched case-insensitively)
     - `ARTIFACT_EXCLUDE_PATTERN`: Patterns to exclude (default: common build/cache directories)
     - `ARTIFACT_SCAN_DIRS`: Directories to scan (defaults to PROJECTS_DIR)
     - `ARTIFACT_AUTO_SCAN`: Auto-scan on session end (default: True)
     - `ARTIFACT_MAX_FILE_SIZE`: Max file size 5MB

3. **Artifact Scanner Service**
   - [x] Created `src/services/artifact_scanner.py`
   - Implements directory scanning with include/exclude patterns
   - Case-insensitive pattern matching via `fnmatch(name.lower(), pattern.lower())`
   - Updates or creates artifact records in database

4. **HTTP API Endpoints** (consolidated — duplicates removed)
   - [x] Added to `src/api/http.py`:
     - GET `/api/artifacts` - List artifacts with `?search=`, `?limit=`, `?offset=`. Sorted by `discovered_at` desc.
     - GET `/api/artifacts/{id}` - Get single artifact metadata (JSON)
     - GET `/api/artifacts/{id}/content` - Get raw file content (`PlainTextResponse`)
     - DELETE `/api/artifacts/{id}` - Delete artifact record
     - GET `/api/artifacts/settings` - Get settings
     - PATCH `/api/artifacts/settings` - Update settings (recreates scanner)

5. **WebSocket Support**
   - [x] Added `list_artifacts` handler in `src/api/ws/output_text.py`
   - [x] Added broadcast methods in `src/core/session.py`:
     - `broadcast_artifact_update()`
     - `broadcast_artifact_deleted()`
     - `broadcast_artifact_list()`

6. **Integration**
   - [x] Wired up ArtifactScanner in `src/main.py`
   - [x] Added auto-scan trigger in `src/core/prompt_router.py` after session archive
   - [x] Added `_broadcast_artifact_list()` to notify clients after scan discovers new artifacts

### Frontend Implementation

1. **Models**
   - [x] `ArtifactInfo` model in `rcflowclient/lib/models/artifact_info.dart`

2. **Service Layer**
   - [x] Added artifact methods to `WebSocketService`:
     - `getArtifacts()`, `getArtifact()`, `getArtifactContent()`, `deleteArtifact()`
     - `getArtifactSettings()`, `updateArtifactSettings()`, `requestArtifacts()`

3. **State Management** (`rcflowclient/lib/state/app_state.dart`)
   - [x] `_artifacts` map with `loadArtifacts()`, `getArtifact()`
   - [x] `_handleArtifactList()`, `_handleArtifactUpdate()`, `_handleArtifactDeleted()`
   - [x] `openArtifactInPane()`, `closeArtifactView()`
   - [x] Pane close/reopen support for artifact panes

4. **Settings Persistence** (`rcflowclient/lib/services/settings_service.dart`)
   - [x] `artifactsFilterSearch` get/set

5. **Pane State** (`rcflowclient/lib/state/pane_state.dart`)
   - [x] `PaneType.artifact` enum value
   - [x] `artifactId` field with `setArtifactId()` / `clearArtifactId()`
   - [x] `PaneNavEntry` includes `artifactId`

6. **UI Components**
   - [x] `SessionListPanel` has 3 tabs: Workers, Tasks, Artifacts
   - [x] `ArtifactListPanel` with search, artifact list tiles, click-to-open
   - [x] `ArtifactPane` — full pane viewer with header, content display, delete, back nav
   - [x] `SessionPane` routes `PaneType.artifact` to `ArtifactPane`

### Documentation

- [x] `Design.md` updated:
  - Artifact Messages section (WebSocket protocol)
  - HTTP endpoints table
  - Artifact Scanner description
  - Database schema (artifacts table)
  - Configuration settings

## Remaining Tasks

### Testing

- [ ] Backend: Unit tests for `ArtifactScanner`
- [ ] Backend: Integration tests for artifact HTTP endpoints
- [ ] Frontend: Artifacts scan on session completion
- [ ] Frontend: Search filters artifacts correctly
- [ ] Frontend: Markdown files display properly
- [ ] Frontend: Text files display in monospace
- [ ] Frontend: Binary files show unsupported message
- [ ] Frontend: Settings persistence works
- [ ] Frontend: WebSocket updates work (artifact_list, artifact_deleted)
- [ ] Frontend: File size limit enforced (5MB)
