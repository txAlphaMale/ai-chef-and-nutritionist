import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  // DEV ONLY. In production the app serves its own frontend from one
  // process (backend/app/static_files.py) and there is no proxy at all.
  // This exists so `npm run dev` keeps hot reload while still letting the
  // browser see a single origin.
  //
  // Deliberately its own variable, NOT the container's APP_PORT: in dev
  // Vite owns 5173 and the backend has to be somewhere else, so reusing
  // APP_PORT would make the two collide the moment someone set it.
  // Override with `DEV_API_PORT=9000 npm run dev`, or VITE_BACKEND_URL
  // for a full URL (different host or scheme).
  const env = loadEnv(mode, process.cwd(), "");
  const devApiPort = env.DEV_API_PORT || process.env.DEV_API_PORT || "8000";
  const backendTarget = env.VITE_BACKEND_URL || process.env.VITE_BACKEND_URL || `http://localhost:${devApiPort}`;

  return {
    plugins: [react()],
    define: {
      // A new value on every production build, used to version the
      // service worker's cache (see public/sw.js and src/main.jsx).
      //
      // CACHE_VERSION used to be a hand-maintained "v1" that was never
      // once bumped, so the shell cache was never invalidated and every
      // build's assets accumulated in it forever. index.html is not
      // content-hashed the way Vite's JS/CSS bundles are, so a stale one
      // could keep pointing at an asset hash that no longer exists -- a
      // redeploy that appears not to take effect, previously chased as a
      // Cache-Control problem.
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
