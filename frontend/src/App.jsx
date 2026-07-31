import { useEffect, useState } from "react";

// Phase 0 placeholder: confirms the frontend can reach the backend.
// Real routing/pages (Dashboard, Inventory, Recipes, Meal Plan, Chat,
// Settings) land in later phases -- see PROJECT-PLAN.md.
export default function App() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => res.json())
      .then(setHealth)
      .catch(() => setHealth({ status: "unreachable" }));
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Chef</h1>
        <p className="subtitle">AI meal planning &amp; kitchen inventory</p>
      </header>
      <main>
        <p>
          Backend status:{" "}
          <strong>{health ? health.status : "checking..."}</strong>
        </p>
      </main>
    </div>
  );
}
