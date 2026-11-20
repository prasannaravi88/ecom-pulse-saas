#!/usr/bin/env bash
# Run the API without Docker. Uses SQLite unless DATABASE_URL is set.
# Needs Python 3.12 with venv support (Debian/Ubuntu: apt install python3.12-venv).
set -euo pipefail
cd "$(dirname "$0")/../backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements-dev.txt
exec .venv/bin/uvicorn app.main:app --reload --port "${BACKEND_PORT:-8000}"
