#!/usr/bin/env bash
# Build a single-file LocalCount binary on Linux (macOS: build_mac.sh, Windows: build.ps1).
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements-dev.txt
if [ "${1:-}" != "--skip-tests" ]; then QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q; fi
.venv/bin/python packaging/make_icon.py
.venv/bin/python packaging/make_version.py
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/sitesizer.spec
ls -lh dist/LocalCount*
