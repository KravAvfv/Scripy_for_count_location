#!/usr/bin/env bash
# Build LocalCount.app on a Mac (Apple Silicon or Intel) and pack it into dist/LocalCount-mac.zip.
# Usage (Terminal, from the repo folder):   ./build_mac.sh            (or ./build_mac.sh --skip-tests)
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Потрібен Python 3.11 або новіший (зараз: $("$PY" --version 2>&1))."
  echo "Встановіть: brew install python@3.12   або   https://www.python.org/downloads/macos/"
  exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-dev.txt
if [ "${1:-}" != "--skip-tests" ]; then QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q; fi
.venv/bin/python packaging/make_icon.py
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/sitesizer.spec
# the app is not signed by Apple: drop the quarantine flag so it opens on this Mac
xattr -cr dist/LocalCount.app || true
(cd dist && rm -f LocalCount-mac.zip && ditto -c -k --keepParent LocalCount.app LocalCount-mac.zip)
echo "Готово: dist/LocalCount.app  (архів: dist/LocalCount-mac.zip)"
