#!/usr/bin/env bash
# LocalCount on a Mac without building anything: double-click this file in Finder.
# The first start creates .venv and installs the libraries (needs the internet, ~2 minutes).
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  osascript -e 'display alert "LocalCount" message "Потрібен Python 3.11 або новіший. Встановіть його з python.org (Downloads → macOS) або командою: brew install python@3.12 — і запустіть цей файл ще раз."' || true
  open "https://www.python.org/downloads/macos/" || true
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Перший запуск: встановлюю бібліотеки…"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
fi
# reinstall only when requirements.txt changed (e.g. after git pull)
if [ ! -f .venv/.requirements ] || ! cmp -s requirements.txt .venv/.requirements; then
  .venv/bin/python -m pip install -q -r requirements.txt
  cp requirements.txt .venv/.requirements
fi
exec .venv/bin/python -m sitesizer "$@"
