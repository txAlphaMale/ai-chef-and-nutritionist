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

# SQLite + uploaded images live here; mount a volume over this path
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Backlog B15.1 (2026-08-01) -- shared with the frontend container via
# the chef-tls volume, so both can serve the same certificate. See
# app/services/tls_service.py's module docstring.
RUN mkdir -p /app/tls
VOLUME ["/app/tls"]

EXPOSE 8095 8446

ENTRYPOINT ["./docker-entrypoint.sh"]
# Backlog B15.1 -- run_server.py (not uvicorn directly) decides plain-
# HTTP vs. HTTPS at launch, and can re-exec itself in place when a
# certificate is generated/imported/cleared. See its own module
# docstring and tls_service.py's restart_to_apply().
CMD ["python", "-m", "app.run_server"]
