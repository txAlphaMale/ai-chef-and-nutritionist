// Backlog B7.3 -- minimal PWA service worker. Deliberately modest scope:
// this app is API-driven (inventory, recipes, meal plans, chat all need
// fresh server data), so genuine offline CRUD/sync is out of scope --
// that would be a much bigger feature than the backlog text actually
// asked for. What this buys: the installed app SHELL (HTML/JS/CSS/icons)
// loads instantly and still loads on a bad/dropped kitchen wifi
// connection instead of the browser's own "no internet" error page, and
// whatever page markup was last cached is still visible (its own API
// calls will fail and surface each page's existing error handling,
// which every page in this app already has -- nothing new needed there).
//
// A plain file under public/ (not processed by Vite) so it can register
// at the site root with the broadest possible scope -- see main.jsx for
// the registration side. Requires a secure context (HTTPS or localhost),
// same standing requirement as camera/geolocation in this app (B15.1).

// Versioned from the registration URL (`/sw.js?v=<build id>`, set in
// src/main.jsx from vite.config.js's __BUILD_ID__), not by hand.
//
// This was a literal "v1" that was never bumped across the whole life of
// the project, which meant the activate handler below -- which deletes
// every cache whose name differs from the current one -- had nothing to
// delete, ever. Two consequences: the shell cache grew without bound as
// each build added another set of hashed assets, and the un-hashed files
// (index.html, config.js) could be served from cache pointing at asset
// hashes that no longer existed. A deploy that "doesn't take effect".
//
// Falling back to "dev" matters for the `npm run dev` case, where the
// service worker is served without a query string.
const CACHE_VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const CACHE_NAME = `chef-shell-${CACHE_VERSION}`;

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Never cache API calls -- inventory/recipes/meal-plan/chat/etc. all
  // need live data every time; a stale cached response here would be
  // actively misleading (e.g. showing yesterday's inventory as current),
  // not just a minor staleness nicety. /health lives outside /api (see
  // backend/app/main.py) but needs the same exclusion -- author-reported
  // 2026-08-03: now that frontend/server.js proxies it same-origin, this
  // service worker would otherwise start caching it too (a stale cached
  // "backend ok" is exactly the kind of misleading result this app's
  // Home page status check exists to catch, not hide).
  if (url.pathname.startsWith("/api/") || url.pathname === "/health") return;

  // config.js is runtime configuration written by the container at start
  // (see frontend/server.js), not a build artifact -- it can legitimately
  // change without a rebuild, which would leave a cached copy stale for a
  // build id that is still current. Always live.
  if (url.pathname === "/config.js") return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        // A navigation this cache has never seen (e.g. the first visit was
        // already offline) -- fall back to the app shell so client-side
        // (Hash) routing still renders something instead of a browser
        // network-error page.
        //
        // Both keys are tried because they are genuinely different cache
        // entries: a visit to "/" is stored under "/", while a visit to
        // "/index.html" is stored under "/index.html". Only the latter
        // was checked before, so the fallback missed for anyone who had
        // ever loaded the app the normal way -- i.e. almost always.
        if (request.mode === "navigate") {
          return (
            (await caches.match("/index.html")) || (await caches.match("/")) || Response.error()
          );
        }
        return Response.error();
      })
  );
});
