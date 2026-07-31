// Backlog B3.1/B3.2 -- shared renderer for the deterministic allergen/
// restriction check's output (app/services/allergen_service.py), reused
// everywhere a recipe or meal-plan entry can carry restriction_warnings/
// cross_contact_warnings: recipe view, recipe import preview, meal-plan
// generation preview, and the meal-plan confirm 409 conflict dialog.

function formatAllergenLabel(key) {
  if (key === "gluten_cross_contact") return "Gluten (cross-contact risk)";
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** matches/crossContactMatches are arrays of {allergen, ingredient_name,
 * matched_keyword} (schemas/allergen.py's RestrictionMatchRead). Either
 * or both may be empty/undefined -- renders nothing in that case. */
export default function RestrictionWarnings({ matches, crossContactMatches, title }) {
  const hasMatches = matches && matches.length > 0;
  const hasCrossContact = crossContactMatches && crossContactMatches.length > 0;
  if (!hasMatches && !hasCrossContact) return null;

  return (
    <div className="restriction-warnings-group">
      {hasMatches && (
        <div className="restriction-warning">
          <strong>{title || "Contains a restricted allergen"}</strong>
          <ul>
            {matches.map((m, i) => (
              <li key={i}>
                {m.ingredient_name} — {formatAllergenLabel(m.allergen)} (matched "{m.matched_keyword}")
              </li>
            ))}
          </ul>
        </div>
      )}
      {hasCrossContact && (
        <div className="cross-contact-warning">
          <strong>Possible cross-contact risk</strong>
          <ul>
            {crossContactMatches.map((m, i) => (
              <li key={i}>
                {m.ingredient_name} — unless labeled certified gluten-free, this may not meet a strict no-cross-contact
                standard
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
