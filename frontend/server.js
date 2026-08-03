#!/usr/bin/env node
// Author-reported 2026-08-03: a phone/tablet on the LAN (not the machine
// running Docker) got a "Backend status: unreachable" Home page and a
// barcode scanner that silently never engaged. Root cause, once looked
// at as an architecture problem rather than two separate bugs (the
// author's own framing, and the right one): this app was built so the
// BROWSER itself called the backend container directly, on its own
// separate origin/port (see the retired src/api.js `backendOrigin`
// logic and PROJECT-PLAN.md's long-standing "Known simplification:
// frontend/backend origins" note). That works from the machine serving
// the app, but asks every other device on the network to independently
// reach a second origin, resolve it, and (once B15.1 added HTTPS) trust
// a second certificate -- any one of which failing looks exactly like
// "unreachable" or "the camera doesn't work", with no obvious link back
// to "this device hasn't accepted the backend's cert" or "this device's
// DNS/hosts setup can't reach the backend's port the same way it
// reaches the frontend's."
//
// This file replaces the old `serve` static-file process with a small
// Node server that does two things itself, so the BROWSER never talks
// to the backend at all -- only this frontend process does, over the
// private Docker network:
//
//   1. Serves the built SPA (frontend/dist) -- identical behavior to
//      the old `serve -s dist`, including its single-page-app fallback
//      (unmatched paths rewrite to /index.html). Verified against
//      `serve`'s own source (node_modules/serve/build/main.js) that
//      `-s`/`--single` does exactly the `rewrites: [{source: "**",
//      destination: "/index.html"}]` config passed to serve-handler
//      below -- not guessed, read directly.
//   2. Reverse-proxies `/api/*` and `/health` to the backend container
//      over Docker's internal network (BACKEND_TARGET, e.g.
//      "http://backend:8095" -- see docker-entrypoint.sh for how that's
//      built from BACKEND_INTERNAL_HOST/BACKEND_PORT). Plain HTTP
//      internally always, regardless of whether THIS server is running
//      HTTP or HTTPS for the browser -- TLS only needs to terminate
//      once, at the edge the browser actually talks to; the Docker
//      bridge network between the two containers isn't exposed to the
//      LAN at all.
//
// Practical effect: every device on the network now only ever needs to
// reach ONE origin (this one) and trust ONE certificate -- the WIKI's
// old "visit and accept the warning at BOTH origins" step is gone
// entirely (see wikiContent.js's https-setup entry), and the backend no
// longer needs its own ports published to the LAN at all for normal
// app use (docker-compose.yml keeps them mapped, commented as optional,
// for direct API access/scripting only).
//
// Two small, well-established dependencies (http-proxy, serve-handler)
// installed globally in the runtime image (see Dockerfile) -- same
// pattern this file replaces (`serve` itself was a global install, not
// a package.json dependency, since none of this app's actual
// dependencies are needed after the Vite build produces static output;
// see Dockerfile's own comment). `serve-handler` is literally the
// library `serve` itself is built on, so static-file behavior is
// unchanged; `http-proxy` is the same proxying engine Vite's own dev
// server proxy (vite.config.js) already uses under the hood, so
// dev and production now share the same underlying proxy behavior,
// not just the same `/api`-relative-URL convention.
//
// Deliberately plain Node, no framework -- matches this project's
// existing redirect-server.js and its own "stayed dependency-light
// throughout" reasoning.
'use strict';

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const httpProxy = require('http-proxy');
const handler = require('serve-handler');

const LISTEN_PORT = process.env.LISTEN_PORT;
const DIST_DIR = process.env.DIST_DIR || path.join(__dirname, 'dist');
const BACKEND_TARGET = process.env.BACKEND_TARGET;
const TLS_CERT_FILE = process.env.TLS_CERT_FILE;
const TLS_KEY_FILE = process.env.TLS_KEY_FILE;

if (!LISTEN_PORT || !BACKEND_TARGET) {
  console.error('[chef-frontend] LISTEN_PORT and BACKEND_TARGET must both be set');
  process.exit(1);
}

// Idle timeouts on both legs of the proxy.
//
// Without these, http-proxy leaves both sockets open indefinitely, so a
// backend that accepts a connection and then stops responding holds a
// socket on this process forever. Enough of those and the frontend stops
// serving anything at all -- including the static shell, which has
// nothing to do with the backend.
//
// These are IDLE timeouts (Node's socket.setTimeout fires on inactivity,
// not on total duration), which is why a value this small is safe for a
// slow but live response such as a large backup download: as long as
// bytes keep arriving, the clock keeps resetting.
//
// It is deliberately unrelated to `ollama_timeout_seconds` (default 600).
// Long AI work does not run inside a request -- it is enqueued on the job
// worker and polled (audit P0-2/B11.1) -- so no legitimate proxied
// request should ever sit idle for two minutes.
const PROXY_IDLE_TIMEOUT_MS = Number(process.env.PROXY_IDLE_TIMEOUT_MS || 120000);

const proxy = httpProxy.createProxyServer({
  target: BACKEND_TARGET,
  changeOrigin: true,
  xfwd: true,
  // Outgoing: this process -> backend.
  proxyTimeout: PROXY_IDLE_TIMEOUT_MS,
  // Incoming: browser -> this process.
  timeout: PROXY_IDLE_TIMEOUT_MS,
});

// A transient backend hiccup (it restarts itself in place to apply a new
// TLS cert, or a container is mid-(re)start) must never crash THIS
// process -- http-proxy's default behavior on an unhandled 'error'
// event is to throw, which would take the whole frontend down over a
// blip nothing else in the app treats as fatal. Respond with a plain
// 502 instead, so the browser sees a normal failed-request state (which
// every page's existing error handling already deals with) rather than
// the frontend itself going dark.
proxy.on('error', (err, req, res) => {
  console.error(`[chef-frontend] proxy error for ${req.method} ${req.url}: ${err.message}`);
  // A timeout surfaces here as ECONNRESET/ETIMEDOUT. `res` is a plain
  // Socket rather than a ServerResponse for a failed WebSocket upgrade,
  // hence the writeHead guard -- calling it on a Socket would throw
  // inside the very handler that exists to stop this process throwing.
  if (!res || typeof res.writeHead !== 'function') {
    if (res && typeof res.destroy === 'function') res.destroy();
    return;
  }
  if (res.headersSent) {
    res.end();
    return;
  }
  const timedOut = err.code === 'ETIMEDOUT' || err.code === 'ESOCKETTIMEDOUT';
  res.writeHead(timedOut ? 504 : 502, { 'Content-Type': 'application/json' });
  res.end(
    JSON.stringify({
      detail: timedOut
        ? `Backend did not respond within ${Math.round(PROXY_IDLE_TIMEOUT_MS / 1000)}s`
        : `Backend unreachable from the frontend proxy (${err.code || err.message})`,
    })
  );
});

const serveConfig = {
  public: DIST_DIR,
  rewrites: [{ source: '**', destination: '/index.html' }],
  // Author-reported 2026-08-03, added defensively while investigating a
  // "the fix still doesn't show up after redeploying" report: neither
  // `serve-handler` (this) nor the old `serve` CLI it replaced set any
  // `Cache-Control` header by default -- only `Last-Modified`, no
  // `Expires`/`Cache-Control` at all -- which leaves a browser free to
  // apply its OWN heuristic freshness lifetime to `index.html`/`sw.js`/
  // the manifest and serve a stale copy after a rebuild without ever
  // re-asking this server. Vite's hashed `/assets/*` filenames already
  // make long-lived caching safe for THOSE (a content change is a new
  // URL), so this only forces revalidation for the small set of
  // never-hashed entry files that a stale copy of would be actively
  // misleading (an old `index.html` pointing at JS that no longer
  // exists, or a `sw.js` that never picks up its own updated fetch
  // handler).
  headers: [
    {
      source: 'assets/**',
      headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }],
    },
    {
      source: '{index.html,sw.js,manifest.webmanifest}',
      headers: [{ key: 'Cache-Control', value: 'no-cache' }],
    },
  ],
};

function requestHandler(req, res) {
  if (req.url === '/health' || req.url.startsWith('/api/')) {
    proxy.web(req, res);
    return;
  }
  handler(req, res, serveConfig);
}

let server;
if (TLS_CERT_FILE && TLS_KEY_FILE) {
  server = https.createServer(
    { cert: fs.readFileSync(TLS_CERT_FILE), key: fs.readFileSync(TLS_KEY_FILE) },
    requestHandler
  );
} else {
  server = http.createServer(requestHandler);
}

// Not used by this app today (no WebSocket/SSE endpoint exists anywhere
// in the backend -- checked before writing this), but proxying upgrade
// requests too costs nothing and means a future streaming feature
// wouldn't silently need this file revisited.
server.on('upgrade', (req, socket, head) => {
  if (req.url === '/health' || req.url.startsWith('/api/')) {
    proxy.ws(req, socket, head);
  } else {
    socket.destroy();
  }
});

server.on('error', (err) => {
  console.error(`[chef-frontend] failed to listen on port ${LISTEN_PORT}: ${err.message}`);
  process.exit(1);
});

server.listen(Number(LISTEN_PORT), '0.0.0.0', () => {
  const scheme = TLS_CERT_FILE ? 'https' : 'http';
  console.log(
    `[chef-frontend] serving ${DIST_DIR} on ${scheme}://0.0.0.0:${LISTEN_PORT}, proxying /api/* and /health to ${BACKEND_TARGET}`
  );
});
