#!/bin/bash
# Refresh GNOME / FreeDesktop caches after a .deb install or removal so newly
# added .desktop entries and icons surface immediately in the application menu
# and GNOME Activities search without requiring a logout cycle.
#
# Used as both the postinst and postrm script for rcflow-client and (via
# scripts/bundle.py) for the rcflow worker .deb.
set -e

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
