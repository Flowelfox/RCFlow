#!/usr/bin/env bash
set -euo pipefail

WIN_ADB="/mnt/c/Users/Flowelfox/AppData/Local/Android/Sdk/platform-tools/adb.exe"
WIN_EMULATOR="/mnt/c/Users/Flowelfox/AppData/Local/Android/Sdk/emulator/emulator.exe"
AVD_NAME="dev"

echo "==> Starting emulator '$AVD_NAME' (cold boot) on port 5554..."
"$WIN_EMULATOR" -avd "$AVD_NAME" -no-snapshot-load -port 5554 &>/dev/null &
disown

echo "==> Waiting for emulator to boot..."
for i in $(seq 1 30); do
    if "$WIN_ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' | grep -q "1"; then
        echo "    Emulator booted!"
        echo ""
        echo "Done. Run: make setup-emulator"
        exit 0
    fi
    sleep 3
done

echo "ERROR: Emulator did not boot within 90s."
exit 1
