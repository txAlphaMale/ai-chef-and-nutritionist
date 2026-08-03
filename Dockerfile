# Chef: one image, one container, one port, one certificate.
#
# This replaced two images. The frontend used to be its own container
# running a Node static server that also reverse-proxied /api back to the
# backend, because the browser previously had to reach two origins and
# trust two certificates. Serving the built files from the API that
# already exists solves the same problem without a second runtime, and
# app/static_files.py records the full reasoning so this does not get
# rebuilt from scratch by someone who assumes a proxy must have been
# needed.
#
# Build context is the repo ROOT (see docker-compose.yml), because this
# needs both frontend/ and backend/.

# --- Stage 1: build the frontend ---------------------------------------
FROM node:22-slim AS frontend-build
WORKDIR /build

# npm ci, not npm install: installs exactly the locked tree and fails if
# the lockfile and package.json disagree, so a build produces the same
# dependency set every time.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# The build is the only thing this stage exists to produce. Assert it,
# rather than letting an empty directory get copied forward and surface
# as a blank page at runtime.
RUN test -f dist/index.html \
    && test -d dist/assets \
    && echo "frontend build OK: $(find dist -type f | wc -l) files"

# --- Stage 2: the application ------------------------------------------
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
# Operational scripts, not tests -- things run against the live deployment
# and its real Ollama, which the test suite cannot reach. Test data stays
# out: `.dockerignore` keeps backend/tests out of the build context, so
# any fixture a script needs is copied in at run time.
COPY backend/scripts ./scripts
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# The built SPA. app/static_files.py mounts this at "/" after every API
# router. STATIC_DIR defaults to exactly this path.
COPY --from=frontend-build /build/dist ./static

# SQLite + uploaded images live here; docker-compose.yml mounts the
# chef-data named volume over it.
#
# No VOLUME directive. VOLUME is not what makes the Compose mount work,
# and it silently hands anyone running this image directly an ANONYMOUS
# volume per container -- so their data appears to persist and then
# vanishes the next time the container is recreated.
RUN mkdir -p /app/data /app/tls

# Fail the BUILD rather than shipping an image that starts and then does
# not work. The proxy this replaced crash-looped from the day it was
# added precisely because nothing ever tried to load it at build time.
RUN python -c "import app.main; import app.run_server; print('app imports OK')" \
    && python -c "import os,sys; sys.exit(0 if os.path.isfile('/app/static/index.html') else 1)" \
    && echo "static build present OK"

# Runs as a non-root user. Fixed uid so ownership on the mounted volumes
# is predictable across rebuilds; the entrypoint chowns them on start.
RUN groupadd --gid 10001 chef \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin chef

# Defaults match docker-compose.yml. 5173/5174 rather than a backend-ish
# pair because this single service is what the browser talks to, and
# those are the ports households already have bookmarked and firewalled.
EXPOSE 5173 5174

ENTRYPOINT ["./docker-entrypoint.sh"]
# run_server.py, not uvicorn directly: it decides HTTP vs HTTPS from
# whatever certificate is on disk and can re-exec itself in place when
# that changes. See its module docstring and tls_service.restart_to_apply.
CMD ["python", "-m", "app.run_server"]
