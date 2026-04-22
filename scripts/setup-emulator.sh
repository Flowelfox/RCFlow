#!/usr/bin/env bash
set -euo pipefail

LINUX_ADB="/home/flowelfox/Android/Sdk/platform-tools/adb"
WIN_HOST=$(grep nameserver /etc/resolv.conf | awk '{print $2}')
ADB_PROXY_PORT=15555

echo "==> Killing Linux ADB server (if running)..."
"$LINUX_ADB" kill-server 2>/dev/null || true

echo "==> Starting Linux ADB server..."
"$LINUX_ADB" start-server 2>/dev/null

echo "==> Connecting to emulator at $WIN_HOST:$ADB_PROXY_PORT..."
for i in 1 2 3 4 5; do
    if "$LINUX_ADB" connect "$WIN_HOST:$ADB_PROXY_PORT" 2>&1 | grep -q "connected"; then
        break
    fi
    if [ "$i" -eq 5 ]; then
        echo "ERROR: Failed to connect. Is the emulator running? (just start-emulator)"
        exit 1
    fi
    echo "    Retry $i..."
    sleep 2
done

echo ""
echo "==> Connected devices:"
"$LINUX_ADB" devices

echo ""
echo "Done. You can now run: just run-android"
