#!/usr/bin/env bash
# Fail when Flutter line coverage drops below rcflowclient/coverage_threshold.txt.
#
# Reads coverage/lcov.info (produced by `flutter test --coverage`) and
# compares the line-coverage percentage to the integer floor in
# coverage_threshold.txt. Exits non-zero on regression so CI and
# `just check` fail loudly.
set -euo pipefail

cd "$(dirname "$0")/.."

lcov_file="coverage/lcov.info"
threshold_file="coverage_threshold.txt"

if [[ ! -f "$lcov_file" ]]; then
    echo "check_coverage: $lcov_file not found; run 'flutter test --coverage' first" >&2
    exit 1
fi

if [[ ! -f "$threshold_file" ]]; then
    echo "check_coverage: $threshold_file not found" >&2
    exit 1
fi

threshold="$(tr -d '[:space:]' < "$threshold_file")"

current="$(awk -F: '
    /^LF:/ {lf+=$2}
    /^LH:/ {lh+=$2}
    END   {if (lf==0) {print 0} else {printf "%d\n", (lh*100)/lf}}
' "$lcov_file")"

echo "Flutter coverage: ${current}% (floor: ${threshold}%)"

if (( current < threshold )); then
    echo "check_coverage: FAIL — coverage ${current}% is below floor ${threshold}%" >&2
    exit 1
fi
