#!/usr/bin/env bash
# NEXT_PUBLIC_API_URL comes from frontend/.env.development unless set in the environment.
set -euo pipefail
cd "$(dirname "$0")/../frontend"
[ -d node_modules ] || npm install
exec npx next dev --port "${FRONTEND_PORT:-3000}"
