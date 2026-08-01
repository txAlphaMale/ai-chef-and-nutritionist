#!/bin/sh
# Runs on every container start. Two jobs:
#
# 1. Writes a small runtime-config file the already-built static bundle
#    reads at page load (window.__CHEF_CONFIG__.backendPort), so
#    BACKEND_PORT (from .env, via docker-compose's env_file) can be
#    changed and picked up on a plain container restart -- no frontend
#    rebuild, no hand-editing src/api.js. See src/api.js's
#    `backendOrigin` for the reader side.
#
# 2. Backlog B15.1 (2026-08-01): decides plain-HTTP vs. HTTPS for `serve`
#    itself by checking for a certificate on the chef-tls volume (shared
#    read-only from the backend container -- see
#    backend/app/services/tls_service.py's module docstring for the full
#    two-container architecture writeup) and keeps watching that same
#    file for changes, restarting `serve` with updated flags whenever it
#    does. This has to live here, not just in the backend, because a
#    browser's camera/geolocation Secure Context check is about the
#    origin the PAGE ITSELF loaded from -- the frontend needs to serve
#    HTTPS too, not only the API it calls.
#
# Known simplification, stated plainly rather than silently missing:
# unlike the backend (which keeps a tiny redirect listener on its old
# HTTP port once HTTPS takes over, see backend/app/run_server.py), this
# script does NOT run an HTTP-to-HTTPS redirect on FRONTEND_PORT once
# HTTPS is active -- `serve` has no built-in way to do that, and writing
# a second Node listener just for a redirect wasn't worth it for what's
# a one-time, one-line bookmark update (http://host:5173 ->
# https://host:5174). See the in-app WIKI's HTTPS entry.
set -e

BACKEND_PORT="${BACKEND_PORT:-8095}"
BACKEND_HTTPS_PORT="${BACKEND_HTTPS_PORT:-8446}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_HTTPS_PORT="${FRONTEND_HTTPS_PORT:-5174}"
CERT_FILE="/app/tls/cert.pem"
KEY_FILE="/app/tls/key.pem"
SERVER_PID=""

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

write_config() {
  if cert_active; then
    active_backend_port="$BACKEND_HTTPS_PORT"
  else
    active_backend_port="$BACKEND_PORT"
  fi
  echo "[chef-frontend] writing runtime config (backendPort=${active_backend_port})..."
  cat > ./dist/config.js <<EOF
window.__CHEF_CONFIG__ = { backendPort: "${active_backend_port}" };
EOF
}

start_server() {
  write_config
  if cert_active; then
    echo "[chef-frontend] starting HTTPS on port ${FRONTEND_HTTPS_PORT} (cert: ${CERT_FILE})..."
    serve -s dist -l "tcp://0.0.0.0:${FRONTEND_HTTPS_PORT}" --ssl-cert "$CERT_FILE" --ssl-key "$KEY_FILE" &
  else
    echo "[chef-frontend] starting plain HTTP on port ${FRONTEND_PORT} (no certificate yet -- set one up under Settings > Security > Certificate)..."
    serve -s dist -l "tcp://0.0.0.0:${FRONTEND_PORT}" &
  fi
  SERVER_PID=$!
}

shutdown() {
  echo "[chef-frontend] shutting down..."
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  exit 0
}
trap shutdown TERM INT

start_server
last_state=$(cert_state)

# Backlog B15.1 -- polls the shared cert file (written by the backend
# container) and restarts `serve` with updated HTTP/HTTPS flags whenever
# it changes (a fresh self-signed cert generated, an imported cert
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
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    start_server
    last_state="$current_state"
  elif ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[chef-frontend] server process exited unexpectedly -- restarting..."
    start_server
    last_state=$(cert_state)
  fi
done
