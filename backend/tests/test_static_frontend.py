"""The app serves its own frontend (app/static_files.py).

These tests exist because of what they replace. Chef used to run a second
container whose only jobs were serving these files and proxying /api back
here, and it crash-looped from the day it was added -- serving nothing,
while the build passed and the container reported no error. Nothing in
the test suite could have caught that, because nothing tested that the
app was reachable at all.

So: assert that "/" returns the SPA, that the API is not shadowed by the
mount, and that the cache headers are what a redeploy depends on.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import static_files


@pytest.fixture()
def built_frontend(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal stand-in for a real Vite build: an entry point, a
    fingerprinted asset, and the two unhashed files whose caching
    behaviour actually matters."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Chef</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('app')")
    (tmp_path / "sw.js").write_text("// service worker")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    return tmp_path


@pytest.fixture()
def client(built_frontend: pathlib.Path) -> TestClient:
    """A tiny app with one API route, mounted the same way main.py does
    it -- routes first, static last."""
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    static_files.mount_frontend(app, directory=str(built_frontend))
    return TestClient(app)


def test_root_serves_the_spa(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Chef</title>" in response.text


def test_the_mount_does_not_shadow_the_api(client: TestClient):
    """A Starlette mount at "/" matches greedily. If it were registered
    before the routers, the entire API would disappear behind the SPA --
    which is why main.py calls mount_frontend last and says so."""
    assert client.get("/api/ping").json() == {"ok": True}
    assert client.get("/health").json() == {"status": "ok"}


def test_fingerprinted_assets_are_cached_forever(client: TestClient):
    """Vite content-hashes everything under /assets, so a changed file is
    always a changed URL and immutable is safe."""
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/", "/index.html", "/sw.js", "/manifest.webmanifest"])
def test_unhashed_entry_files_must_revalidate(client: TestClient, path: str):
    """These are never fingerprinted, and a stale copy of any of them is
    actively harmful: an old index.html points at asset hashes that no
    longer exist, which is a redeploy that appears not to take effect.

    Starlette sets no Cache-Control at all by default, which leaves the
    browser free to apply its own heuristic freshness lifetime -- exactly
    the failure that was previously chased as a service-worker problem."""
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_a_missing_build_is_not_fatal(tmp_path: pathlib.Path):
    """The test suite and any `uvicorn app.main:app` run outside the
    container have no build present. The API must still come up."""
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    mounted = static_files.mount_frontend(app, directory=str(tmp_path / "does-not-exist"))
    assert mounted is False
    assert TestClient(app).get("/api/ping").json() == {"ok": True}
