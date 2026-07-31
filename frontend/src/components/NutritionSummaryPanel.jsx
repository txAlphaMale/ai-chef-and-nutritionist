import { useEffect, useState } from "react";
import { api } from "../api";

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// Same 9-key nutrition set as RecipeDetailPage.jsx/RecipeForm.jsx
// (food_data_service.NUTRITION_KEYS) -- short labels here since this
// renders as a dense table rather than a single recipe's list.
const NUTRIENT_COLUMNS = [
  { key: "calories", label: "Cal" },
  { key: "protein_g", label: "Protein (g)" },
  { key: "carbs_g", label: "Carbs (g)" },
  { key: "fat_g", label: "Fat (g)" },
  { key: "saturated_fat_g", label: "Sat. fat (g)" },
  { key: "fiber_g", label: "Fiber (g)" },
  { key: "sugars_g", label: "Sugars (g)" },
  { key: "sodium_mg", label: "Sodium (mg)" },
  { key: "cholesterol_mg", label: "Cholesterol (mg)" },
];

/** Backlog B1.4 -- per-day/week nutrition totals for a meal plan
 * (GET /meal-plans/{planId}/nutrition-summary), alongside each household
 * member's DRI-derived daily target so the totals have something to be
 * read against. Self-contained like GroceryListPanel -- fetches its own
 * data given `planId`, re-fetches on `refreshKey` bumps from the parent. */
export default function NutritionSummaryPanel({ planId, refreshKey }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setSummary(await api.get(`/meal-plans/${planId}/nutrition-summary`));
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
      <h3>Nutrition</h3>
      <p className="hint">
        Totals assume one serving of each planned meal per person, per day -- there's no per-person meal-attendance
        tracking, so this is a household-wide read, not an individual log. Daily targets are estimates from each
        member's age/sex/height/weight/activity level (Settings/Health page), not medical advice.
      </p>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading nutrition summary...</p>
      ) : !summary || summary.days.length === 0 ? (
        <p>No planned meals with a recipe yet -- add some to this week to see totals here.</p>
      ) : (
        <>
          <div className="nutrition-summary-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="l">Day</th>
                  {NUTRIENT_COLUMNS.map((c) => (
                    <th key={c.key}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.days.map((day) => (
                  <tr key={day.day_of_week}>
                    <td className="l" data-label="Day">
                      {DAY_NAMES[day.day_of_week]}
                      {day.contributing_entry_count < day.entry_count && (
                        <span className="hint">
                          {" "}
                          ({day.contributing_entry_count}/{day.entry_count} meals have nutrition data)
                        </span>
                      )}
                    </td>
                    {NUTRIENT_COLUMNS.map((c) => (
                      <td key={c.key} data-label={c.label}>
                        {day.totals[c.key] ?? "–"}
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="nutrition-summary-week-row">
                  <td className="l" data-label="Day">
                    Week total
                  </td>
                  {NUTRIENT_COLUMNS.map((c) => (
                    <td key={c.key} data-label={c.label}>
                      {summary.week_totals[c.key] ?? "–"}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>

          {summary.member_targets.length > 0 && (
            <>
              <h4>Daily targets</h4>
              <ul className="nutrition-target-list">
                {summary.member_targets.map((m) => (
                  <li key={m.member_id}>
                    <strong>{m.name}</strong>
                    {m.daily_targets ? (
                      <>
                        {": "}
                        {NUTRIENT_COLUMNS.map((c) => `${c.label} ${m.daily_targets[c.key]}`).join(" · ")}
                        {summary.week_totals.calories != null && (
                          <span className="tag">
                            week calories {Math.round((summary.week_totals.calories / (m.daily_targets.calories * 7)) * 100)}% of
                            7-day target
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="hint">
                        {" "}
                        no target yet -- log {m.missing_fields.join(", ")} on the Health page to see one
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
