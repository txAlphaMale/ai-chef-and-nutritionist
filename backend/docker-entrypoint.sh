#!/bin/sh
# Runs on every container start: applies any pending Alembic migrations,
# then ensures generic seed data (default kitchen profile, meal tags,
# system prompts, app settings) exists. Both are idempotent, so this is
# safe to run on every restart, not just the first one.
set -e

echo "[chef-backend] running migrations..."
alembic upgrade head

echo "[chef-backend] ensuring seed data..."
python -m app.seed

echo "[chef-backend] starting app..."
exec "$@"
