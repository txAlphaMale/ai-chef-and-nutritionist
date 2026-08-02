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
# 3. Backlog B15.1 follow-up (2026-08-02, author-requested): once HTTPS
#    is active, ALSO runs a tiny plain-HTTP redirect listener on
#    FRONTEND_PORT (redirect-server.js, plain Node `http`, no new
#    dependency) so a bookmarked/typed http://host:5173 URL 307-redirects
#    to https://host:5174 instead of going dead. Mirrors the backend's
#    own redirect listener in backend/app/run_server.py. This used to be
#    a documented, deliberate gap ("serve has no built-in way to do
#    that") -- built now since the author asked for it directly.
set -e

BACKEND_PORT="${BACKEND_PORT:-8095}"
BACKEND_HTTPS_PORT="${BACKEND_HTTPS_PORT:-8446}"
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
    SERVER_PID=$!
    echo "[chef-frontend] also starting a plain-HTTP redirect on port ${FRONTEND_PORT} -> https://<host>:${FRONTEND_HTTPS_PORT}..."
    REDIRECT_LISTEN_PORT="$FRONTEND_PORT" REDIRECT_TARGET_PORT="$FRONTEND_HTTPS_PORT" node redirect-server.js &
    REDIRECT_PID=$!
  else
    echo "[chef-frontend] starting plain HTTP on port ${FRONTEND_PORT} (no certificate yet -- set one up under Settings > Security > Certificate)..."
    serve -s dist -l "tcp://0.0.0.0:${FRONTEND_PORT}" &
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
