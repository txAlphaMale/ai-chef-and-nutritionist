FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# SQLite + uploaded images live here; docker-compose.yml mounts the
# chef-data named volume over it.
#
# No VOLUME directive. VOLUME is not what makes the Compose mount work --
# Compose mounts the named volume regardless -- and it has a real cost:
# anyone who runs this image directly (`docker run`) silently gets an
# ANONYMOUS volume per container instead of an error or an obvious
# ephemeral directory, so their data appears to persist and then vanishes
# the next time they recreate the container without spotting why.
RUN mkdir -p /app/data /app/tls

# Runs as a non-root user. The uid is fixed rather than auto-assigned so
# that files on the shared chef-tls volume have predictable ownership
# across BOTH images -- the backend writes the certificate and the
# frontend has to be able to read it, and tls_service writes the key
# 0600, so "same uid in both containers" is what makes that work.
RUN groupadd --gid 10001 chef \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin chef

EXPOSE 8095 8446

ENTRYPOINT ["./docker-entrypoint.sh"]
# Backlog B15.1 -- run_server.py (not uvicorn directly) decides plain-
# HTTP vs. HTTPS at launch, and can re-exec itself in place when a
# certificate is generated/imported/cleared. See its own module
# docstring and tls_service.py's restart_to_apply().
CMD ["python", "-m", "app.run_server"]
