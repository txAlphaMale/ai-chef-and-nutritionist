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

When HTTPS is active, this ALSO starts a tiny plain-HTTP listener on the
old port (BACKEND_PORT/8095) that does nothing but 307-redirect to the
same path on BACKEND_HTTPS_PORT -- so a bookmarked/typed
http://host:8095 URL doesn't go completely dead once HTTPS takes over.
Runs in a background daemon thread with its own event loop rather than
uvicorn's normal `--port` flag, because uvicorn can only bind one
(host, port) pair per Config/Server -- see _start_redirect_thread()
below for why a thread, not a second concurrent Server.serve() in the
same loop, was chosen.

Started via `python -m app.run_server` -- see backend/Dockerfile."""
import asyncio
import os
import threading

import uvicorn

from app.services import tls_service

HOST = "0.0.0.0"
HTTP_PORT = os.environ.get("BACKEND_PORT", "8095")
HTTPS_PORT = os.environ.get("BACKEND_HTTPS_PORT", "8446")


async def _redirect_asgi(scope, receive, send):
    """Minimal hand-rolled ASGI app (no FastAPI/Starlette needed for
    this) -- 307-redirects every request to the same path+query on
    HTTPS_PORT. 307 (Temporary Redirect), not 301/308: this app can
    toggle HTTPS off again (Settings > Security > Certificate > clear),
    at which point HTTP_PORT needs to go back to serving normally -- a
    permanently-cached 301/308 in the browser would keep bouncing
    requests to a now-dead HTTPS port forever after that."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return
    if scope["type"] != "http":
        return
    headers = dict(scope.get("headers") or ())
    host_header = headers.get(b"host", b"").decode("latin-1")
    if host_header:
        hostname = host_header.split(":")[0]
    else:
        server = scope.get("server") or ("localhost", None)
        hostname = server[0]
    location = f"https://{hostname}:{HTTPS_PORT}{scope.get('path', '/')}"
    query = scope.get("query_string", b"")
    if query:
        location += "?" + query.decode("latin-1")
    await send(
        {
            "type": "http.response.start",
            "status": 307,
            "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def _start_redirect_thread():
    """Runs the redirect listener on HTTP_PORT in its own background
    daemon thread + event loop, separate from the main app server's
    loop. Deliberately NOT a second uvicorn.Server.serve() gathered into
    the same asyncio loop as the main app: uvicorn.Server installs
    SIGTERM/SIGINT handlers via plain signal.signal() when running in
    the main thread, and two Server instances doing that in the same
    loop stomp on each other's handler -- only the second one actually
    gets wired up, so the first would silently stop responding to a
    graceful shutdown signal. Running this one from a non-main thread
    makes uvicorn skip signal handling for it entirely, which is exactly
    what's wanted: it's a daemon thread, so it (and its socket) is torn
    down automatically the instant the process exits or os.execv
    replaces it, with no separate shutdown path needed.

    Best-effort: if HTTP_PORT can't be bound for some reason, the main
    app on HTTPS_PORT still starts fine -- this is a convenience
    redirect, not core functionality."""

    def _run():
        try:
            config = uvicorn.Config(_redirect_asgi, host=HOST, port=int(HTTP_PORT), log_level="warning")
            asyncio.run(uvicorn.Server(config).serve())
        except Exception as e:  # noqa: BLE001 -- a failed convenience redirect must not take down the main app
            print(
                f"[run_server] redirect listener on port {HTTP_PORT} failed to start ({e}) -- "
                f"the app itself is unaffected, but old links to port {HTTP_PORT} won't auto-redirect",
                flush=True,
            )

    threading.Thread(target=_run, daemon=True, name="http-redirect").start()


def main():
    if tls_service.has_active_cert():
        print(f"[run_server] starting HTTPS on port {HTTPS_PORT} (cert: {tls_service.CERT_PATH})", flush=True)
        print(
            f"[run_server] also starting a plain-HTTP redirect on port {HTTP_PORT} -> "
            f"https://<host>:{HTTPS_PORT} (so old bookmarks/typed URLs still land you somewhere "
            f"instead of going dark)",
            flush=True,
        )
        _start_redirect_thread()
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
