"""A single, deliberately last-alphabetically-sorted smoke test: confirms
`app.main` (the whole FastAPI app -- every router, the CORS/session/
auth-gate middleware stack, and now the B15.1 TLS lifespan hook) actually
imports and constructs without error.

Named with a `zzz` prefix (not `test_main_app_import.py`) so it always
runs LAST in a default alphabetical pytest collection -- importing
`app.main` pulls in and registers every router module as an import side
effect, and if an earlier, more specific test file's own monkeypatching
of a service module happened to run afterward in the same process, this
early wide-net import could theoretically mask a narrower problem a
later test was trying to isolate. Costs nothing to order last since this
test has no fixtures or ordering dependencies of its own.

Depends on conftest.py's SESSION_SECRET_FILE/TLS_DIR redirects (added
alongside this test, backlog B15.1) -- both would otherwise try to write
under real container paths (/app/data, /app/tls) that don't exist in a
sandbox/CI environment."""

from __future__ import annotations


def test_main_app_imports_and_constructs():
    import app.main

    assert app.main.app is not None
    # A handful of routers registered under distinct prefixes -- if any
    # router module failed to import (a typo'd import, a missing schema
    # field, etc.), FastAPI would have raised well before this point, but
    # asserting a known route exists catches a router silently NOT being
    # included (e.g. a forgotten app.include_router() call) too.
    route_paths = {getattr(r, "path", None) for r in app.main.app.routes}
    assert "/api/tls/status" in route_paths
    assert "/api/system/status" in route_paths
    assert "/health" in route_paths
    assert "/api/health/import" in route_paths  # backlog B8.1
