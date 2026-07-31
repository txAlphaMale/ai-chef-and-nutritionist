import { NavLink, Route, HashRouter as Router, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage";
import InventoryPage from "./pages/InventoryPage";
import RecipesPage from "./pages/RecipesPage";
import RecipeDetailPage from "./pages/RecipeDetailPage";
import MealPlanPage from "./pages/MealPlanPage";
import HealthPage from "./pages/HealthPage";
import ChatWidget from "./components/ChatWidget";

// HashRouter (not BrowserRouter): the production Dockerfile serves the
// built SPA with `serve -s`, and keeping routing hash-based avoids
// needing server-side history-API fallback configuration for a first pass.
export default function App() {
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
