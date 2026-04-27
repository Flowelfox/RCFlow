#!/usr/bin/env bash
# Sync the working tree to the Ubuntu VM used for live Linux GUI / .deb
# integration testing.  The remote is set up via ``Host vmubuntu`` in
# ``~/.ssh/config`` and resolves to the desktop user (``osboxes``).
#
# Usage:
#   scripts/rsync-to-vmubuntu.sh           # full sync (default)
#   scripts/rsync-to-vmubuntu.sh --dry-run # preview without changes
#
# After sync, run on the VM (or via ssh) something like:
#   sudo cp ~/Projects/RCFlow/src/gui/linux_app.py \
#           /opt/rcflow/lib/python/src/gui/linux_app.py
#   sudo cp ~/Projects/RCFlow/scripts/linux_gui_window.py \
#           /opt/rcflow/gui/linux_gui_window.py
# to overlay the live install without rebuilding the .deb.
set -e

REMOTE="vmubuntu:~/Projects/RCFlow/"
SOURCE="/home/flowelfox/Projects/RCFlow/"

rsync -avz --progress "$@" \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ty/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='*.egg-info/' \
  --exclude='.uv_cache/' \
  --exclude='*.db' \
  --exclude='*.sqlite3' \
  --exclude='.env' \
  --exclude='settings.json' \
  --exclude='certs/' \
  --exclude='logs/' \
  --exclude='data/' \
  --exclude='testclient/' \
  --exclude='plans/' \
  --exclude='.worktrees/' \
  --exclude='.claude/' \
  --exclude='.idea/' \
  --exclude='.vscode/' \
  --exclude='.DS_Store' \
  --exclude='.dart_tool/' \
  --exclude='rcflowclient/build/' \
  --exclude='rcflowclient/.dart_tool/' \
  --exclude='rcflowclient/.idea/' \
  --exclude='rcflowclient/android/.gradle/' \
  --exclude='rcflowclient/android/app/build/' \
  --exclude='rcflowclient/ios/Pods/' \
  --exclude='rcflowclient/ios/.symlinks/' \
  --exclude='rcflowclient/macos/Pods/' \
  --exclude='rcflowclient/macos/.symlinks/' \
  --exclude='rcflowclient/windows/flutter/' \
  --exclude='rcflowclient/linux/flutter/' \
  "$SOURCE" "$REMOTE"
