import { useEffect } from "react";
import { NavLink, Route, HashRouter as Router, Routes } from "react-router-dom";
import { api } from "./api";
import HomePage from "./pages/HomePage";
import InventoryPage from "./pages/InventoryPage";
import RecipesPage from "./pages/RecipesPage";
import RecipeDetailPage from "./pages/RecipeDetailPage";
import MealPlanPage from "./pages/MealPlanPage";
import HealthPage from "./pages/HealthPage";
import SettingsPage from "./pages/SettingsPage";
import ChatWidget from "./components/ChatWidget";
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
            <NavLink to="/health">Health</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/inventory" element={<InventoryPage />} />
            <Route path="/recipes" element={<RecipesPage />} />
            <Route path="/recipes/:id" element={<RecipeDetailPage />} />
            <Route path="/meal-plan" element={<MealPlanPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
        {/* Mounted here, outside <Routes>, so it stays alive (history,
            in-flight sends, panel open/closed state) across route
            navigation instead of remounting on every page change. */}
        <ChatWidget />
      </div>
    </Router>
  );
}
