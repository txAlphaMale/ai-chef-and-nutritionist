import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/theme.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Backlog B7.3 -- PWA service worker registration. `serviceWorker` is
// only ever exposed by the browser in a secure context (HTTPS or
// localhost), so this is a no-op plain-HTTP LAN deployment until B15.1's
// certificate is set up -- exactly the same gate camera/geolocation
// already hit, not a new limitation. Registered after `load` so it
// never competes with the initial page render for bandwidth/CPU.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    // The ?v= is what makes cache invalidation automatic. A browser
    // re-fetches and re-installs a service worker whose URL changed, and
    // sw.js reads this value to name its cache -- so each build gets a
    // fresh cache and the activate handler deletes every older one. See
    // vite.config.js's __BUILD_ID__ for why the previous hand-maintained
    // version constant did not work.
    navigator.serviceWorker.register(`/sw.js?v=${__BUILD_ID__}`).catch(() => {
      // Non-fatal -- the app works identically without it, just without
      // the app-shell caching/offline-reload benefit.
    });
  });
}
