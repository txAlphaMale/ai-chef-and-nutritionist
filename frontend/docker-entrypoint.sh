#!/bin/sh
# Runs on every container start. Two jobs:
#
# 1. Author-reported 2026-08-03: launches server.js (a static file
#    server that ALSO reverse-proxies /api/* and /health to the backend
#    container over the internal Docker network -- see that file's own
#    docstring) instead of the old plain `serve`. BACKEND_TARGET is
#    built here from BACKEND_INTERNAL_HOST (the backend's Docker Compose
#    service name -- "backend" by default, see docker-compose.yml) and
#    BACKEND_PORT, always as plain HTTP -- the browser never talks to
#    the backend directly anymore, so there's nothing for it to trust,
#    and the private Docker bridge network between the two containers
#    doesn't need TLS. This replaces the old window.__CHEF_CONFIG__/
#    config.js mechanism entirely (it only ever existed so the BROWSER
#    could learn the backend's port to call it directly -- see the
#    retired backendOrigin logic in src/api.js -- which no longer
#    happens at all).
#
# 2. Backlog B15.1 (2026-08-01): decides plain-HTTP vs. HTTPS for
#    server.js itself by checking for a certificate on the chef-tls
#    volume (shared read-only from the backend container -- see
#    backend/app/services/tls_service.py's module docstring for the full
#    two-container architecture writeup) and keeps watching that same
#    file for changes, restarting server.js with updated flags whenever
#    it does. This has to live here, not just in the backend, because a
#    browser's camera/geolocation Secure Context check is about the
#    origin the PAGE ITSELF loaded from -- the frontend needs to serve
#    HTTPS too, not only the API it calls.
#
# 3. Backlog B15.1 follow-up (2026-08-02, author-requested): once HTTPS
#    is active, ALSO runs a tiny plain-HTTP redirect listener on
#    FRONTEND_PORT (redirect-server.js, plain Node `http`, no new
#    dependency) so a bookmarked/typed http://host:5173 URL 307-redirects
#    to https://host:5174 instead of going dead. Mirrors the backend's
#    own redirect listener in backend/app/run_server.py. This used to be
#    a documented, deliberate gap ("serve has no built-in way to do
#    that") -- built now since the author asked for it directly.
set -e

APP_UID="${APP_UID:-10001}"
APP_GID="${APP_GID:-10001}"

# Drop to the non-root `chef` user and re-exec. See the backend's own
# entrypoint for the full reasoning behind setpriv; the short version is
# that it is in util-linux (so no new dependency) and execs rather than
# forks, so this script keeps PID 1 and its TERM/INT trap below still
# fires on `docker stop`.
#
# No chown here: /app/tls is mounted READ-ONLY in this container (the
# backend owns writing the certificate), and the backend's entrypoint
# already chowns that volume to the same uid this drops to.
#
# Probed before it is committed to, for the same reason as the backend:
# a hardening step must never be the thing that stops the app starting.
if [ "$(id -u)" = "0" ]; then
  if setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all true 2>/dev/null; then
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups --inh-caps=-all "$0" "$@"
  fi
  echo "[chef-frontend] WARNING: could not drop privileges -- continuing as root" >&2
fi

BACKEND_PORT="${BACKEND_PORT:-8095}"
BACKEND_HTTPS_PORT="${BACKEND_HTTPS_PORT:-8446}"
# The backend's Docker Compose service name -- resolves over the private
# inter-container network Compose sets up automatically. Only needs to
# change if you rename the `backend:` service in docker-compose.yml.
BACKEND_INTERNAL_HOST="${BACKEND_INTERNAL_HOST:-backend}"
BACKEND_TARGET="http://${BACKEND_INTERNAL_HOST}:${BACKEND_PORT}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_HTTPS_PORT="${FRONTEND_HTTPS_PORT:-5174}"
CERT_FILE="/app/tls/cert.pem"
KEY_FILE="/app/tls/key.pem"
SERVER_PID=""
REDIRECT_PID=""

cert_active() {
  [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]
}

# A cheap "has this file changed" signal -- file modification time, not
# content hash (the cert/key pair is only ever replaced atomically via
# tmp+os.replace on the backend side, so a changed mtime reliably means
# a genuinely new file, never a partial write). "absent" is its own
# distinct state so clearing a certificate (both files removed) is
# detected as a change too, not just installing/replacing one.
cert_state() {
  if cert_active; then
    stat -c %Y "$CERT_FILE" 2>/dev/null || echo "present"
  else
    echo "absent"
  fi
}

start_server() {
  if cert_active; then
    echo "[chef-frontend] starting HTTPS on port ${FRONTEND_HTTPS_PORT} (cert: ${CERT_FILE}), proxying /api and /health to ${BACKEND_TARGET}..."
    LISTEN_PORT="$FRONTEND_HTTPS_PORT" DIST_DIR="./dist" BACKEND_TARGET="$BACKEND_TARGET" \
      TLS_CERT_FILE="$CERT_FILE" TLS_KEY_FILE="$KEY_FILE" node server.js &
    SERVER_PID=$!
    echo "[chef-frontend] also starting a plain-HTTP redirect on port ${FRONTEND_PORT} -> https://<host>:${FRONTEND_HTTPS_PORT}..."
    REDIRECT_LISTEN_PORT="$FRONTEND_PORT" REDIRECT_TARGET_PORT="$FRONTEND_HTTPS_PORT" node redirect-server.js &
    REDIRECT_PID=$!
  else
    echo "[chef-frontend] starting plain HTTP on port ${FRONTEND_PORT} (no certificate yet -- set one up under Settings > Security > Certificate), proxying /api and /health to ${BACKEND_TARGET}..."
    LISTEN_PORT="$FRONTEND_PORT" DIST_DIR="./dist" BACKEND_TARGET="$BACKEND_TARGET" node server.js &
    SERVER_PID=$!
    REDIRECT_PID=""
  fi
}

stop_server() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  [ -n "$REDIRECT_PID" ] && kill "$REDIRECT_PID" 2>/dev/null
  wait "$SERVER_PID" 2>/dev/null || true
  [ -n "$REDIRECT_PID" ] && wait "$REDIRECT_PID" 2>/dev/null
  true
}

shutdown() {
  echo "[chef-frontend] shutting down..."
  stop_server
  exit 0
}
trap shutdown TERM INT

start_server
last_state=$(cert_state)

# Backlog B15.1 -- polls the shared cert file (written by the backend
# container) and restarts server.js with updated HTTP/HTTPS flags
# whenever it changes (a fresh self-signed cert generated, an imported cert
# replacing it, or the cert cleared entirely reverting to plain HTTP).
# Mirrors the backend's own tls_service.restart_to_apply() in spirit
# (notice a changed cert, restart to pick it up) but as an independent
# poll rather than a direct signal between the two containers -- there's
# no cheap way for the backend container to reach into and restart a
# sibling container's process, so each container watches the same
# shared file on its own instead.
while true; do
  sleep 5
  current_state=$(cert_state)
  if [ "$current_state" != "$last_state" ]; then
    echo "[chef-frontend] certificate state changed -- restarting server..."
    stop_server
    start_server
    last_state="$current_state"
  elif ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[chef-frontend] server process exited unexpectedly -- restarting..."
    stop_server
    start_server
    last_state=$(cert_state)
  elif [ -n "$REDIRECT_PID" ] && ! kill -0 "$REDIRECT_PID" 2>/dev/null; then
    # Best-effort, matching redirect-server.js's own "must never take
    # down the real HTTPS server" philosophy -- if just the redirect
    # listener died (e.g. its port got taken by something else), restart
    # only it rather than bouncing the whole main server too.
    echo "[chef-frontend] redirect listener exited unexpectedly -- restarting it..."
    REDIRECT_LISTEN_PORT="$FRONTEND_PORT" REDIRECT_TARGET_PORT="$FRONTEND_HTTPS_PORT" node redirect-server.js &
    REDIRECT_PID=$!
  fi
done
