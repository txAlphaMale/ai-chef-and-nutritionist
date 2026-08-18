import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, HashRouter as Router, Routes } from "react-router-dom";
import { api } from "./api";
import HomePage from "./pages/HomePage";

// Route-level code splitting (capstone review 2026-08-16, backlog B24.4).
//
// HomePage stays EAGER: it is the landing route, so lazy-loading it would
// add a round trip to the one navigation that has no previous page to hide
// it behind.
//
// Everything else is fetched on first navigation to it. On a LAN that is a
// few milliseconds; what it buys is a first paint that does not carry the
// Settings page's forms, the WIKI's 35 entries of prose and the dining
// finder for somebody who opened the app to see what is for dinner. The
// far larger win is BarcodeScanner, split separately at its own use site
// (see InventoryPage) because it is behind a button rather than a route.
const InventoryPage = lazy(() => import("./pages/InventoryPage"));
const RecipesPage = lazy(() => import("./pages/RecipesPage"));
const RecipeDetailPage = lazy(() => import("./pages/RecipeDetailPage"));
const MealPlanPage = lazy(() => import("./pages/MealPlanPage"));
const DiningPage = lazy(() => import("./pages/DiningPage"));
const HealthPage = lazy(() => import("./pages/HealthPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const WikiPage = lazy(() => import("./pages/WikiPage"));
import ChatWidget from "./components/ChatWidget";
import LoginGate from "./components/LoginGate";
import ExpiringDigestBanner from "./components/ExpiringDigestBanner";
import RecallBanner from "./components/RecallBanner";
import JobsBadge from "./components/JobsBadge";
import TimersBadge from "./components/TimersBadge";
import { applyTheme, getCachedTheme } from "./themes";

// HashRouter (not BrowserRouter): the production Dockerfile serves the
// built SPA with `serve -s`, and keeping routing hash-based avoids
// needing server-side history-API fallback configuration for a first pass.
export default function App() {
  // Theme reconciliation (2026-08-01): index.html's inline pre-hydration
  // script already applied the cached (localStorage) theme synchronously
  // before this component ever mounted, so there's no visible flash --
  // this effect just fetches the DB-backed "ui_theme" setting (the real
  // source of truth, persists across container rebuilds) and re-applies
  // it if it turns out to differ from the cache, e.g. after the setting
  // was changed on a different device/browser.
  useEffect(() => {
    api
      .get("/system/settings")
      .then((list) => {
        const uiTheme = list.find((s) => s.key === "ui_theme");
        if (uiTheme && uiTheme.value !== getCachedTheme()) {
          applyTheme(uiTheme.value);
        }
      })
      .catch(() => {
        // Non-fatal -- worst case the page keeps showing the cached/
        // default theme until the next successful load.
      });
  }, []);

  // Backlog B9.4 (via B10.2, 2026-08-01) -- the opt-in single-password
  // gate. `authStatus === null` while the initial check is in flight;
  // rendering nothing (rather than the app, then a flash of the login
  // screen) avoids a moment where protected content is visible before
  // the gate has had a chance to say otherwise.
  const [authStatus, setAuthStatus] = useState(null);

  function checkAuthStatus() {
    api
      .get("/auth/status")
      .then(setAuthStatus)
      // If the backend is unreachable, fail OPEN rather than locking
      // the user out of an app that may not even have auth enabled --
      // the API calls the rest of the app makes will fail on their own
      // and surface their own errors either way.
      .catch(() => setAuthStatus({ enabled: false, authenticated: true }));
  }

  useEffect(() => {
    checkAuthStatus();
  }, []);

  if (authStatus === null) return null;
  if (authStatus.enabled && !authStatus.authenticated) {
    return <LoginGate onSuccess={checkAuthStatus} />;
  }

  return (
    <Router>
      <div className="app-shell">
        <header className="app-header">
          <h1>Chef</h1>
          <p className="subtitle">AI meal planning &amp; kitchen inventory</p>
          <nav className="app-nav">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/inventory">Inventory</NavLink>
            <NavLink to="/recipes">Recipes</NavLink>
            <NavLink to="/meal-plan">Meal Plan</NavLink>
            <NavLink to="/dining">Dining Out</NavLink>
            <NavLink to="/health">Health</NavLink>
            <NavLink to="/settings">Settings</NavLink>
            <NavLink to="/wiki">WIKI</NavLink>
          </nav>
        </header>
        <ExpiringDigestBanner />
        {/* Backlog B3.3 -- same app-shell placement as the digest banner
            above, so a recall match reaches the household from any page. */}
        <RecallBanner />
        {/* Backlog B11.1 -- app-wide background-job indicator, same
            "outside <Routes>, always mounted" placement as the banner
            above, so it's visible no matter which page enqueued the
            job or which page is currently open. */}
        <JobsBadge />
        {/* Backlog B7.2 -- step-linked cooking timers, same "outside
            <Routes>, always visible" placement as the job badge above, so
            a timer started in Cook Mode stays visible while browsing
            elsewhere in the app. */}
        <TimersBadge />
        <main>
          {/* One boundary around the whole route table rather than one per
              route: they are mutually exclusive, so a shared fallback is
              the same behaviour with less machinery. */}
          <Suspense fallback={<p>Loading...</p>}>
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/inventory" element={<InventoryPage />} />
              <Route path="/recipes" element={<RecipesPage />} />
              <Route path="/recipes/:id" element={<RecipeDetailPage />} />
              <Route path="/meal-plan" element={<MealPlanPage />} />
              <Route path="/dining" element={<DiningPage />} />
              <Route path="/health" element={<HealthPage />} />
              <Route path="/settings" element={<SettingsPage />} />
                <Route path="/wiki" element={<WikiPage />} />
            </Routes>
          </Suspense>
        </main>
        {/* Mounted here, outside <Routes>, so it stays alive (history,
            in-flight sends, panel open/closed state) across route
            navigation instead of remounting on every page change. */}
        <ChatWidget />
      </div>
    </Router>
  );
}
