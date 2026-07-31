// Placeholder default -- overwritten at container start by
// docker-entrypoint.sh with the real BACKEND_PORT (see src/api.js). Vite
// copies everything under public/ verbatim into dist/ at build time, so
// this also ensures a valid (if not port-correct) file exists even if
// something runs `serve` without going through the entrypoint script.
// In `npm run dev`, Vite serves this real static file directly too --
// without it, a request for /config.js falls through to Vite's SPA
// fallback (index.html), which isn't valid JavaScript and throws a
// console error when the browser tries to execute it as a script.
window.__CHEF_CONFIG__ = { backendPort: "8095" };
