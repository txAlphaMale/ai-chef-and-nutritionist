// Thin fetch wrapper -- every request goes through here so error
// handling and the JSON dance stay consistent across pages.
//
// Empty on purpose: the API and this page are served from one origin, so
// every call is same-origin and a relative URL is correct everywhere --
// in dev (via vite.config.js's proxy) and in the Docker image (where
// app/static_files.py mounts the built frontend under the API) alike.
//
// Kept as an exported constant rather than deleted because roughly a
// dozen call sites elsewhere build URLs as `${backendOrigin}/api/...`
// for things fetch() doesn't handle -- recipe images, calendar/JSON-LD/
// backup/certificate downloads, anything that becomes an href or a src.
// Removing it would mean touching all of them for no behaviour change.
export const backendOrigin = "";

const BASE = `${backendOrigin}/api`;

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    // The session-cookie auth gate needs the browser to send/receive its
    // cookie. Same-origin fetch would do that by default, so this is an
    // explicit, harmless superset -- kept so nothing breaks here if a
    // deployment ever reintroduces a cross-origin path.
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
