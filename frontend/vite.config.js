import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  // Picks up BACKEND_PORT from a `.env`/`.env.local` in this directory (if
  // you keep one for the no-Docker dev workflow) or the shell environment,
  // so `BACKEND_PORT=9000 npm run dev` proxies to the right place without
  // editing this file -- same env var name docker-compose/.env.example use,
  // just for the dev-server proxy target instead of the container mapping.
  // VITE_BACKEND_URL remains an escape valve for a full URL override (e.g.
  // a different host/scheme), taking precedence over BACKEND_PORT if set.
  const env = loadEnv(mode, process.cwd(), "");
  const backendPort = env.BACKEND_PORT || process.env.BACKEND_PORT || "8095";
  const backendTarget = env.VITE_BACKEND_URL || process.env.VITE_BACKEND_URL || `http://localhost:${backendPort}`;

  return {
    plugins: [react()],
    define: {
      // A new value on every production build, used to version the
      // service worker's cache (see public/sw.js and src/main.jsx).
      //
      // CACHE_VERSION used to be a hand-maintained "v1" that was never
      // once bumped, so the shell cache was never invalidated and every
      // build's assets accumulated in it forever. index.html and
      // config.js are not content-hashed the way Vite's JS/CSS bundles
      // are, so a stale index.html could keep pointing at an asset hash
      // that no longer exists -- a redeploy that appears not to take
      // effect, which is exactly what was reported and previously
      // treated as a Cache-Control problem.
      //
      // Deriving it from the build removes the human step entirely.
      __BUILD_ID__: JSON.stringify(String(Date.now())),
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: backendTarget,
          changeOrigin: true,
        },
        // /health lives outside /api (see backend/app/main.py) but needs
        // the same dev-time proxy -- see src/api.js's backendOrigin.
        "/health": {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
