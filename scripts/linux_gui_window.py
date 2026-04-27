#!/usr/bin/env python3
"""Linux worker GUI launcher — runs under the host's system Python.

Spawned by the frozen ``rcflow`` worker via
:func:`src.__main__._run_linux_native_dashboard`.  The launcher is a thin
shim because the actual launcher implementation (AppIndicator tray,
jeepney portal helpers, CustomTkinter dashboard) lives in
:mod:`src.gui.linux_app` so it benefits from the project's lint/type
gates.

System Python is required: the PyInstaller-bundled tcl/tk aborts on
Ubuntu 25.04 with a libxcb 1.17 sequence-number assertion that
distro-shipped ``python3-tk`` does not.

Path resolution:

* ``/opt/rcflow/lib/python`` — vendored pure-Python deps (CustomTkinter)
  shipped by the ``.deb`` package.  Skipped when absent.
* ``/opt/rcflow`` — installed source tree (``src/...``).  Skipped when
  absent.
* Repo root (``..`` from this file) — used in dev mode so
  ``just run-gui`` works without a system install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_INSTALL_LIB = Path("/opt/rcflow/lib/python")
_INSTALL_ROOT = Path("/opt/rcflow")
_REPO_ROOT = Path(__file__).resolve().parent.parent

for candidate in (_INSTALL_LIB, _INSTALL_ROOT, _REPO_ROOT):
    if candidate.exists():
        path_str = str(candidate)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

from src.gui.linux_app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
