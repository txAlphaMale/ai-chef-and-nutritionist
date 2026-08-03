#!/usr/bin/env python3
"""Backlog B15.1 (2026-08-01): the backend container's actual entrypoint
process, replacing a plain `uvicorn app.main:app` CMD. Decides plain-HTTP
vs. HTTPS by checking whether a valid certificate/key pair currently
exists under tls_service.TLS_DIR, then serves accordingly. Ported from
the sibling Fiduciary project's `portfolio-api/run_server.py` -- read
directly before writing this, adapted only for Chef's port-naming
(BACKEND_PORT/BACKEND_HTTPS_PORT vs. Fiduciary's PORT/HTTPS_PORT) and
its `app.main:app` import path.

This indirection (rather than baking --ssl-certfile/--ssl-keyfile
straight into the Dockerfile CMD) is what makes
tls_service.restart_to_apply() work: installing a new/imported
certificate re-execs THIS script (os.execv, same PID -- Docker's restart
policy never sees an exit), so the HTTP-vs-HTTPS decision gets
re-derived fresh from whatever's on disk at that moment.

When HTTPS is active, this ALSO serves the real app over plain HTTP on
BACKEND_PORT, in a background daemon thread. That is what the frontend
container's reverse proxy talks to over the private Docker network --
see _start_internal_http_thread() below, which documents the regression
that made this necessary and why it is a second Server in a thread
rather than a second concurrent serve() on the same loop.

Started via `python -m app.run_server` -- see backend/Dockerfile."""
import asyncio
import os
import threading

import uvicorn

from app.services import tls_service

HOST = "0.0.0.0"
HTTP_PORT = os.environ.get("BACKEND_PORT", "8095")
HTTPS_PORT = os.environ.get("BACKEND_HTTPS_PORT", "8446")


def _start_internal_http_thread():
    """Serves the REAL app over plain HTTP on BACKEND_PORT, in a
    background daemon thread, whenever HTTPS is active on
    BACKEND_HTTPS_PORT.

    This replaced a 307-redirect listener, and the reason matters.

    Since 2026-08-03 the frontend container reverse-proxies /api/* and
    /health to `http://<backend service>:$BACKEND_PORT` over the private
    Docker network -- the browser never talks to this container directly.
    But the old behaviour turned BACKEND_PORT into a redirect-only
    listener the moment a certificate was installed, so every proxied API
    call started coming back as a 307 pointing at
    `https://backend:8446/...` -- an internal Docker hostname no browser
    on the LAN can resolve. Net effect: installing a certificate (which
    B15.1 exists to make the camera and geolocation work at all) silently
    broke every API call in the app.

    TLS now terminates once, at the edge the browser actually talks to.
    This container keeps its own HTTPS listener for direct/scripted
    access, and always serves the real app on plain HTTP internally.

    What was given up: a bookmarked `http://<host>:8095` no longer
    auto-redirects to the HTTPS port -- it just serves the API plainly.
    That redirect only ever mattered for people hitting the API directly
    in a browser, and the frontend still runs its own HTTP-to-HTTPS
    redirect for the page origin, which is the URL people actually
    bookmark (see frontend/redirect-server.js).

    Best-effort: if BACKEND_PORT can't be bound, the HTTPS listener still
    starts. That degrades direct access, not the app."""

    def _run():
        try:
            config = uvicorn.Config("app.main:app", host=HOST, port=int(HTTP_PORT), log_level="warning")
            asyncio.run(uvicorn.Server(config).serve())
        except Exception as e:
            print(
                f"[run_server] internal plain-HTTP listener on port {HTTP_PORT} failed to start ({e}) -- "
                f"the frontend proxy and any direct HTTP client will not be able to reach this backend",
                flush=True,
            )

    # A non-main thread is required, not just convenient: uvicorn.Server
    # installs SIGTERM/SIGINT handlers via signal.signal() when it runs on
    # the main thread, and two Servers doing that in one process stomp on
    # each other -- only the last one registered actually handles a
    # graceful shutdown. Running from a non-main thread makes uvicorn skip
    # signal handling entirely, which is what is wanted here: a daemon
    # thread and its socket are torn down automatically when the process
    # exits or os.execv replaces it.
    threading.Thread(target=_run, daemon=True, name="internal-http").start()


def main():
    if tls_service.has_active_cert():
        print(f"[run_server] starting HTTPS on port {HTTPS_PORT} (cert: {tls_service.CERT_PATH})", flush=True)
        print(
            f"[run_server] also serving the app over plain HTTP on port {HTTP_PORT} for the "
            f"frontend container's internal reverse proxy (see _start_internal_http_thread)",
            flush=True,
        )
        _start_internal_http_thread()
        config = uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=int(HTTPS_PORT),
            ssl_certfile=tls_service.CERT_PATH,
            ssl_keyfile=tls_service.KEY_PATH,
        )
    else:
        print(
            f"[run_server] starting plain HTTP on port {HTTP_PORT} (no active certificate -- set "
            f"one up under Settings > Security > Certificate)",
            flush=True,
        )
        config = uvicorn.Config("app.main:app", host=HOST, port=int(HTTP_PORT))
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
