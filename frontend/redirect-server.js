#!/usr/bin/env node
// Backlog B15.1 follow-up (2026-08-02, author-requested): a minimal
// plain-HTTP listener that does nothing but 307-redirect every request
// to the same path on the HTTPS port, so a bookmarked/typed
// http://host:<FRONTEND_PORT> URL doesn't go dead once HTTPS takes
// over. Mirrors the backend's own redirect listener
// (backend/app/run_server.py's _redirect_asgi) -- same 307, not
// 301/308, reasoning: HTTPS can be turned back off from Settings >
// Security > Certificate > Remove certificate, and a browser-cached
// PERMANENT redirect would keep bouncing to a now-dead HTTPS port
// forever after that.
//
// Deliberately plain Node `http` (no Express/etc) -- this app has
// stayed dependency-light throughout, and a redirect-only listener
// doesn't need a framework. Started by docker-entrypoint.sh alongside
// the real `serve` HTTPS process, only when a certificate is active;
// this was a documented, deliberate simplification when B15.1 first
// shipped ("serve has no built-in way to do this, and writing a
// second Node listener wasn't worth it for a one-time bookmark
// update") -- the author asked for it directly afterward, so it's
// built now rather than left as a permanent gap.
'use strict';

const http = require('http');

const LISTEN_PORT = process.env.REDIRECT_LISTEN_PORT;
const TARGET_PORT = process.env.REDIRECT_TARGET_PORT;

if (!LISTEN_PORT || !TARGET_PORT) {
  console.error('[chef-frontend-redirect] REDIRECT_LISTEN_PORT and REDIRECT_TARGET_PORT must both be set');
  process.exit(1);
}

const server = http.createServer((req, res) => {
  // Redirect to whatever host the browser actually asked for (a LAN
  // IP, a hostname, localhost) -- never hardcode a host, so this works
  // identically regardless of which address in the certificate's SAN
  // list the household happens to be browsing from.
  const hostHeader = req.headers.host || '';
  const hostname = hostHeader.split(':')[0] || 'localhost';
  const location = `https://${hostname}:${TARGET_PORT}${req.url || '/'}`;
  res.writeHead(307, { Location: location, 'Content-Length': 0 });
  res.end();
});

server.on('error', (err) => {
  // Best-effort, same philosophy as the backend's own redirect
  // listener: a failed bind here must never take down the real HTTPS
  // server. docker-entrypoint.sh doesn't track this process's health
  // separately from the main `serve` process, so if this exits, old
  // bookmarks just stop auto-redirecting -- nothing worse.
  console.error(`[chef-frontend-redirect] failed to listen on port ${LISTEN_PORT}: ${err.message}`);
  process.exit(1);
});

server.listen(Number(LISTEN_PORT), '0.0.0.0', () => {
  console.log(`[chef-frontend-redirect] redirecting http://*:${LISTEN_PORT} -> https://*:${TARGET_PORT}`);
});
