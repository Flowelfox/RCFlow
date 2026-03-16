#!/usr/bin/env bash
set -e

REMOTE="vpohribnichenko@mac.lan:~/Projects/RCFlow/"
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
