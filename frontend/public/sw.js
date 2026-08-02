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

const CACHE_VERSION = "v1";
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
  // not just a minor staleness nicety.
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => {
          if (cached) return cached;
          // A navigation this cache has never seen (e.g. first visit was
          // already offline) -- fall back to the app shell itself so
          // client-side (Hash) routing still renders something instead
          // of a browser network-error page.
          if (request.mode === "navigate") return caches.match("/index.html");
          return Response.error();
        })
      )
  );
});
