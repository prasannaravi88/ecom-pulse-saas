#!/usr/bin/env bash
# Run the backend test suite inside the backend image (no local Python needed).
# Pass --pg to also run it against the compose PostgreSQL + pgvector service.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q --build-arg INSTALL_DEV=true -t ecom-pulse-backend:dev backend >/dev/null
if [ "${1:-}" = "--pg" ]; then
  docker compose up -d --wait db
  docker compose exec -T db psql -qU pulse -d ecom_pulse -c "DROP DATABASE IF EXISTS ecom_pulse_test" -c "CREATE DATABASE ecom_pulse_test"
  exec docker run --rm --network ecom-pulse-saas_default -e PYTHONPATH=/app \
    -e TEST_DATABASE_URL=postgresql+psycopg://pulse:pulse@db:5432/ecom_pulse_test \
    -v "$PWD/backend":/app ecom-pulse-backend:dev python -m pytest -q -p no:cacheprovider
fi
exec docker run --rm -e PYTHONPATH=/app -v "$PWD/backend":/app ecom-pulse-backend:dev python -m pytest -q -p no:cacheprovider
