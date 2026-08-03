#!/bin/sh
# Runs on every container start: applies any pending Alembic migrations,
# then ensures generic seed data (default kitchen profile, meal tags,
# system prompts, app settings) exists. Both are idempotent, so this is
# safe to run on every restart, not just the first one.
set -e

APP_UID="${APP_UID:-10001}"
APP_GID="${APP_GID:-10001}"

# Start as root, fix ownership of the mounted volumes, then drop
# privileges and re-exec this same script as the `chef` user.
#
# The chown is the part that cannot be skipped. Docker only copies image
# ownership into a named volume the FIRST time that volume is created --
# an existing chef-data volume from before this change is full of
# root-owned files, so simply adding `USER chef` to the Dockerfile would
# have left the app unable to write its own database. Doing it here means
# an existing deployment upgrades without the user having to know any of
# this. It is idempotent and costs nothing on later starts.
#
# setpriv rather than su/gosu: it is part of util-linux, which is present
# in every Debian base image including -slim, so this adds no dependency.
# It also execs directly instead of forking, so the app keeps PID 1 and
# still receives SIGTERM from `docker stop` for a clean shutdown -- `su`
# would sit in between and swallow it.
#
# The drop is attempted with a trivial command first and only committed
# to once that succeeds. Hardening must not be able to stop the app from
# starting: if setpriv is missing or blocked by an unusual runtime, this
# says so loudly and carries on as root rather than leaving the household
# with a container that restart-loops for a reason nothing explains.
if [ "$(id -u)" = "0" ]; then
  chown -R "$APP_UID:$APP_GID" /app/data /app/tls 2>/dev/null || true
  if setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all true 2>/dev/null; then
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all "$0" "$@"
  fi
  echo "[chef-backend] WARNING: could not drop privileges -- continuing as root" >&2
fi

echo "[chef-backend] running migrations..."
alembic upgrade head

echo "[chef-backend] ensuring seed data..."
python -m app.seed

echo "[chef-backend] starting app..."
exec "$@"
