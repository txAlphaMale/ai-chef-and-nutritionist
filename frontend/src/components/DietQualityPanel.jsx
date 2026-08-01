import { useEffect, useState } from "react";
import { api } from "../api";

/** Backlog B2.2 -- an HEI-2020-inspired diet-quality estimate for a
 * meal plan (GET /meal-plans/{planId}/diet-quality-score). Self-contained
 * like NutritionSummaryPanel/GroceryListPanel -- fetches its own data
 * given `planId`, re-fetches on `refreshKey` bumps from the parent.
 *
 * This is deliberately NOT presented as "your Healthy Eating Index
 * score" anywhere in this component -- the backend's own `methodology`
 * string (always returned, see diet_quality_service.py) is rendered
 * verbatim rather than paraphrased into something that sounds more
 * authoritative than it is. */
export default function DietQualityPanel({ planId, refreshKey }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showComponents, setShowComponents] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setResult(await api.get(`/meal-plans/${planId}/diet-quality-score`));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (planId) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, refreshKey]);

  if (!planId) return null;

  return (
    <div className="card">
      <h3>Diet quality estimate</h3>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading diet quality estimate...</p>
      ) : !result || !result.computed ? (
        <p>
          {result?.reason || "No planned meals with nutrition data yet -- add some to this week to see an estimate here."}
        </p>
      ) : (
        <>
          <div className="diet-quality-score-headline">
            <span className="diet-quality-score-number">{result.score.points}</span>
            <span className="diet-quality-score-max">/ {result.score.max_points}</span>
            {result.score.percent != null && <span className="tag">{result.score.percent}%</span>}
          </div>
          <p className="hint">{result.methodology}</p>

          {result.unscored_components.length > 0 && (
            <p className="hint">
              Not scored this pass: {result.unscored_components.map((c) => c.label).join(", ")} -- see the
              breakdown below for why.
            </p>
          )}

          <button type="button" className="btn-link" onClick={() => setShowComponents((v) => !v)}>
            {showComponents ? "Hide" : "Show"} component breakdown
          </button>

          {showComponents && (
            <table className="data-table diet-quality-component-table">
              <thead>
                <tr>
                  <th className="l">Component</th>
                  <th>Points</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {result.components.map((c) => (
                  <tr key={c.key}>
                    <td className="l" data-label="Component">
                      {c.label}
                    </td>
                    <td data-label="Points">
                      {c.computable ? `${c.points} / ${c.max_points}` : <span className="hint">not scored</span>}
                    </td>
                    <td data-label="Value">
                      {c.computable && c.value != null ? `${c.value} ${c.unit}` : "–"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
