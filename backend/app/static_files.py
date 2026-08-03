"""Serving the built frontend from this app.

Chef used to run a second container whose only jobs were to serve these
static files and reverse-proxy `/api` back here. That design is gone, and
this module is what replaced it.

**Why the proxy was removed rather than repaired.** The problem it was
built for was real: before it existed the browser talked to the backend
on one origin and the frontend on another, so every device on the LAN had
to reach two ports and -- once HTTPS was added -- trust two certificates.
Any one of those failing looked like "backend unreachable" with no clue
why. A reverse proxy does solve that. It is just not the cheapest thing
that solves it, and it cost ~520 lines across two Node servers, a
supervisor loop, a second image, a shared TLS volume with an rw/ro split,
and two independent poll loops watching the same certificate file. Every
one of those is a thing that can break, and one of them did: the proxy's
dependencies were installed with `npm install -g`, which Node does not
search when resolving `require()`, so the container crash-looped from the
day it was introduced and served nothing.

Serving the files from the API that already exists gets the same
single-origin, single-certificate result with one mount and a cache
policy. One container, one port, one certificate.

**HashRouter is what makes this nearly free.** The app uses hash routing
(`frontend/src/App.jsx`), so a client route never reaches the server --
`/#/recipes/5` sends only `/`. The hard part of serving an SPA, the
history fallback, does not apply here at all. `html=True` covers
directory-index resolution and that is the whole requirement.

Do not reintroduce a proxy in front of this without a concrete reason
that is written down. "Separation of concerns" is not one for a
single-household app whose two halves always ship together.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

# Where the frontend build lands in the image. Overridable so the backend
# can run outside a container (no build present) without failing.
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")

# Vite fingerprints everything under /assets with a content hash, so a
# changed file is always a changed URL and it can be cached forever.
_IMMUTABLE_PREFIX = "/assets/"

# These are never fingerprinted, and a stale copy of any of them is
# actively harmful: an old index.html points at asset hashes that no
# longer exist (a redeploy that appears not to take effect), and a stale
# sw.js never picks up its own updated fetch handler. `no-cache` still
# allows caching -- it requires revalidation before use, which is the
# behaviour wanted here.
_ALWAYS_REVALIDATE = {"/", "/index.html", "/sw.js", "/manifest.webmanifest"}


class SpaStaticFiles(StaticFiles):
    """StaticFiles with an explicit cache policy.

    Starlette sets no `Cache-Control` at all by default -- only
    `Last-Modified` -- which leaves the browser free to apply its own
    heuristic freshness lifetime and serve a stale shell after a
    redeploy without ever asking. That is not a hypothetical: it was
    reported as "the fix doesn't show up after redeploying" and
    previously chased as a service-worker problem.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        request_path = scope.get("path", "/")
        if request_path.startswith(_IMMUTABLE_PREFIX):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request_path in _ALWAYS_REVALIDATE:
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_frontend(app: FastAPI, directory: str | None = None) -> bool:
    """Mounts the built frontend at `/`, if a build is present.

    Returns True if mounted. A missing directory is not an error: the
    test suite and any `uvicorn app.main:app` run outside the container
    have no build, and the API must still come up. In the image the build
    is always there (the Dockerfile asserts it), so a missing directory
    at runtime means someone is running the backend directly, which is a
    supported thing to do.

    MUST be called after every `include_router`. A mount at `/` matches
    greedily, so mounting first would shadow the entire API.
    """
    directory = directory or STATIC_DIR
    if not os.path.isdir(directory):
        print(
            f"[static_files] no frontend build at {directory} -- serving the API only. "
            f"This is expected outside the container.",
            flush=True,
        )
        return False
    app.mount("/", SpaStaticFiles(directory=directory, html=True), name="frontend")
    print(f"[static_files] serving the frontend from {directory}", flush=True)
    return True
