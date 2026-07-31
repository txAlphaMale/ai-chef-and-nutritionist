#!/bin/sh
# Runs on every container start: writes a small runtime-config file the
# already-built static bundle reads at page load, so BACKEND_PORT (from
# .env, via docker-compose's env_file) can be changed and picked up on a
# plain container restart -- no frontend rebuild, no hand-editing
# src/api.js. See src/api.js's `backendOrigin` for the reader side and
# README.md's "Known limitation" section for the fuller writeup of why
# this is needed at all (frontend/backend are separate origins with no
# reverse proxy in front of them).
set -e

BACKEND_PORT="${BACKEND_PORT:-8095}"
echo "[chef-frontend] writing runtime config (backendPort=${BACKEND_PORT})..."
cat > ./dist/config.js <<EOF
window.__CHEF_CONFIG__ = { backendPort: "${BACKEND_PORT}" };
EOF

echo "[chef-frontend] starting server..."
exec "$@"
