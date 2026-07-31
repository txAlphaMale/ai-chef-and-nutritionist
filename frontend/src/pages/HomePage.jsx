import { useEffect, useState } from "react";
import { backendOrigin } from "../api";

export default function HomePage() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    // /health lives outside the /api prefix (see backend/app/main.py),
    // so this bypasses api.js's request() helper and its /api base path,
    // but reuses the same backendOrigin resolution logic.
    fetch(`${backendOrigin}/health`)
      .then((res) => res.json())
      .then(setHealth)
      .catch(() => setHealth({ status: "unreachable" }));
  }, []);

  return (
    <div>
      <p>
        Backend status: <strong>{health ? health.status : "checking..."}</strong>
      </p>
      {health && health.household_size != null && (
        <p>Household size: {health.household_size}</p>
      )}
      <p className="hint">
        More here as phases land: dashboard widgets for expiring items, this
        week's meal plan, and the persistent chat panel.
      </p>
    </div>
  );
}
