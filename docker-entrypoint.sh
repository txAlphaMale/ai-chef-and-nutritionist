#!/bin/sh
# Runs on every container start: applies any pending Alembic migrations,
# ensures seed data exists, then starts the app. Both steps are
# idempotent, so this is safe on every restart, not just the first.
set -e

APP_UID="${APP_UID:-10001}"
APP_GID="${APP_GID:-10001}"

# Start as root, fix ownership of the mounted volumes, then drop
# privileges and re-exec this same script as the `chef` user.
#
# The chown cannot be skipped. Docker only copies image ownership into a
# named volume the FIRST time that volume is created, so an existing
# chef-data volume from before the app ran non-root is full of root-owned
# files. Without this, adding a non-root user would leave the app unable
# to write its own database. Idempotent, and costs nothing on later
# starts.
#
# setpriv rather than su or gosu: it is part of util-linux, present in
# every Debian base image including -slim, so this adds no dependency. It
# also execs rather than forks, so the app keeps PID 1 and still receives
# SIGTERM from `docker stop` for a clean shutdown -- `su` would sit in
# between and swallow it.
#
# Probed with a trivial command before being committed to. Hardening must
# never be the reason the app fails to start: if setpriv is missing or
# blocked by an unusual runtime, this says so loudly and carries on as
# root rather than leaving a container that restart-loops for a reason
# nothing explains.
if [ "$(id -u)" = "0" ]; then
  chown -R "$APP_UID:$APP_GID" /app/data /app/tls 2>/dev/null || true
  if setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all true 2>/dev/null; then
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all "$0" "$@"
  fi
  echo "[chef] WARNING: could not drop privileges -- continuing as root" >&2
fi

echo "[chef] running migrations..."
alembic upgrade head

echo "[chef] ensuring seed data..."
python -m app.seed

echo "[chef] starting app..."
exec "$@"
