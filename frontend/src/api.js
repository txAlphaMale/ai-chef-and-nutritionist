// Thin fetch wrapper -- every request goes through here so error
// handling and the JSON dance stay consistent across pages.
//
// In dev (`npm run dev`), relative /api/* paths work via the vite proxy
// in vite.config.js. In the production Docker build, frontend and
// backend are separate containers on separate ports with no reverse
// proxy in front of them yet (a documented simplification -- see
// PROJECT-PLAN.md "Known simplification: frontend/backend origins" --
// a shared reverse proxy is a reasonable Phase 9 polish item), so we
// fall back to hitting the backend directly at BACKEND_PORT on the
// same host. The backend's CORS is wide open (allow_origins=["*"])
// specifically to make this work.
//
// BACKEND_PORT itself is read at page load from window.__CHEF_CONFIG__,
// written by docker-entrypoint.sh from the container's BACKEND_PORT env
// var (sourced from .env via docker-compose's env_file) -- so changing
// BACKEND_PORT in .env and restarting the container (`docker compose up`,
// no `--build` needed) is enough; no rebuild, no editing this file. The
// literal 8095 below is only a fallback for when that script hasn't run
// (e.g. a raw `npm run dev`, which never reaches this branch anyway --
// see the `import.meta.env.DEV` check below).
const BACKEND_PORT = window.__CHEF_CONFIG__?.backendPort || 8095;

export const backendOrigin = import.meta.env.DEV
  ? ""
  : `${window.location.protocol}//${window.location.hostname}:${BACKEND_PORT}`;

const BASE = `${backendOrigin}/api`;

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
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
    throw new Error(`${res.status} ${detail}`);
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
