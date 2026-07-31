import { NavLink, Route, HashRouter as Router, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage";
import InventoryPage from "./pages/InventoryPage";

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
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/inventory" element={<InventoryPage />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}
