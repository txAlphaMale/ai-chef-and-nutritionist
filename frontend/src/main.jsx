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
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Non-fatal -- the app works identically without it, just without
      // the app-shell caching/offline-reload benefit.
    });
  });
}
