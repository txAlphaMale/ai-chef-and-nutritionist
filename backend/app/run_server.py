#!/usr/bin/env python3
"""The container's entrypoint process.

Decides plain HTTP vs. HTTPS by checking whether a valid certificate/key
pair currently exists under `tls_service.TLS_DIR`, then serves the whole
app -- API and frontend both, see `app/static_files.py` -- accordingly.

This indirection, rather than baking `--ssl-certfile`/`--ssl-keyfile` into
the Dockerfile CMD, is what makes `tls_service.restart_to_apply()` work:
installing a new certificate re-execs this script (os.execv, same PID, so
Docker's restart policy never sees an exit) and the HTTP-vs-HTTPS decision
is re-derived from whatever is on disk at that moment.

When HTTPS is active, `APP_PORT` becomes a plain-HTTP listener that
307-redirects to the HTTPS port, so a bookmarked `http://<host>:5173`
keeps working instead of going dead.

That redirect used to be something else, and the history is worth one
paragraph because it caused a real outage. While a separate frontend
container reverse-proxied `/api` to this one, `APP_PORT` had to serve the
REAL app over plain HTTP for the proxy to talk to -- a redirect there sent
the proxy a `Location:` pointing at an internal Docker hostname no browser
could resolve, which silently broke every API call the moment a
certificate was installed. With the proxy gone there is no internal
consumer left, so the plain port goes back to being a redirect, which is
what a person typing the http:// URL actually wants.

Started via `python -m app.run_server` -- see the repo-root Dockerfile.
"""

import http.server
import threading

import uvicorn

from app.config import settings
from app.services import tls_service

HOST = "0.0.0.0"
# From settings, not a second os.environ read -- see app/config.py.
# tls_service.status() reports these same two values to the Settings UI,
# and the only way they can be guaranteed to match what is actually bound
# is for both to read one declaration.
HTTP_PORT = settings.app_port
HTTPS_PORT = settings.app_https_port


def _start_redirect_listener() -> None:
    """Serves a 307 to the HTTPS port on APP_PORT, in a daemon thread.

    Deliberately `http.server` from the standard library rather than a
    second uvicorn: this handles a handful of redirects for people with
    an old bookmark, and pulling a second ASGI server into the process
    for that -- plus the signal handling two uvicorn Servers fight over
    -- is not a trade worth making.

    Best-effort. If the port cannot be bound, the HTTPS listener still
    starts; that degrades an old bookmark, not the app.
    """

    https_port = HTTPS_PORT

    class _RedirectHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _redirect(self) -> None:
            host = (self.headers.get("Host") or "").split(":")[0]
            target = f"https://{host}:{https_port}{self.path}"
            # 307, not 301: preserves method and body, and is not cached
            # permanently the way a 301 is -- which matters because this
            # listener's behaviour flips the moment a certificate is
            # removed.
            self.send_response(307)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect
        do_HEAD = _redirect
        do_POST = _redirect

        def log_message(self, *args) -> None:
            pass  # one line per redirect is noise, not signal

    def _run() -> None:
        try:
            http.server.ThreadingHTTPServer((HOST, HTTP_PORT), _RedirectHandler).serve_forever()
        except Exception as exc:
            print(
                f"[run_server] HTTP->HTTPS redirect listener on port {HTTP_PORT} failed to start "
                f"({exc}) -- the app is still served on HTTPS port {HTTPS_PORT}",
                flush=True,
            )

    threading.Thread(target=_run, daemon=True, name="http-redirect").start()


def main() -> None:
    if tls_service.has_active_cert():
        print(f"[run_server] serving HTTPS on port {HTTPS_PORT} (cert: {tls_service.CERT_PATH})", flush=True)
        print(f"[run_server] redirecting plain HTTP on port {HTTP_PORT} -> HTTPS {HTTPS_PORT}", flush=True)
        _start_redirect_listener()
        config = uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=HTTPS_PORT,
            ssl_certfile=tls_service.CERT_PATH,
            ssl_keyfile=tls_service.KEY_PATH,
        )
    else:
        print(
            f"[run_server] serving plain HTTP on port {HTTP_PORT} (no active certificate -- set "
            f"one up under Settings > Security > Certificate)",
            flush=True,
        )
        config = uvicorn.Config("app.main:app", host=HOST, port=HTTP_PORT)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
