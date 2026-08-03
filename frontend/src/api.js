// Thin fetch wrapper -- every request goes through here so error
// handling and the JSON dance stay consistent across pages.
//
// Author-reported 2026-08-03: this used to hit the backend container
// directly on its own port (BACKEND_PORT, learned at page load from
// window.__CHEF_CONFIG__/config.js) whenever this wasn't a `npm run dev`
// session -- a documented simplification ("Known simplification:
// frontend/backend origins" in PROJECT-PLAN.md) that worked fine from
// the machine running Docker, but asked every OTHER device on the LAN to
// independently reach a second origin and (once B15.1 added HTTPS) trust
// a second certificate. Any one of those failing looked like "Backend
// status: unreachable" on the Home page, or a barcode scanner that
// silently never engaged, with no obvious link back to the real cause.
//
// Fixed at the architecture level, not per-symptom: frontend/server.js
// now reverse-proxies /api/* and /health to the backend itself, over the
// private Docker network -- the BROWSER never talks to the backend at
// all anymore, only this frontend process does. That makes every
// request genuinely same-origin from the browser's point of view, in
// dev (via vite.config.js's proxy) and in the production Docker build
// (via server.js) alike -- so backendOrigin is now always empty, kept
// only so every existing `${backendOrigin}/api/...`-style call site
// elsewhere in this app (recipe images, calendar/JSON-LD/backup/
// certificate downloads, etc.) still resolves correctly as a plain
// relative, same-origin URL without needing to be touched individually.
export const backendOrigin = "";

const BASE = `${backendOrigin}/api`;

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    // Backlog B10.2 (2026-08-01) -- the session-cookie auth gate needs
    // the browser to send/receive its cookie. Now that every request is
    // genuinely same-origin (2026-08-03, see backendOrigin above), a
    // same-origin fetch would send the cookie by default anyway -- this
    // is kept as an explicit, harmless superset rather than removed, so
    // nothing changes here if a future deployment ever reintroduces a
    // cross-origin path.
    credentials: "include",
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      // ignore -- keep statusText
    }
    // Backlog B3.1 -- some endpoints (meal-plan entry confirm) return a
    // structured object as `detail` (message + allergen match lists),
    // not just a string. The stringified message below stays a sane
    // fallback for callers that just show e.message, but attach the raw
    // status/detail too so a caller that needs the structure (e.g. an
    // allergen-conflict dialog) doesn't have to re-parse a stringified
    // "[object Object]".
    const message = typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail);
    const err = new Error(`${res.status} ${message}`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body) }),
  patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};
