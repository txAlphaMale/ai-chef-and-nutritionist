// Says out loud, on the review screen, whether the ingredients below were
// checked against the source or merely produced by a model.
//
// Written because the silence was measured: a capture of this app's own
// review form was once imported as a recipe, two-pass correctly refused
// every block of it, and the resulting screen of unverified guesses looked
// exactly like a verified import. See recipe_service's
// INGREDIENT_PROVENANCE_KEY comment for the full incident.
//
// Wording rule: never say "verified" for anything that was not, and never
// bury the number the gate actually judged on.

function describe(provenance) {
  const { path, reason, verified, single_call: singleCall } = provenance;

  if (path === "two_pass") {
    const subject = verified === 1 ? "The one row below was" : `All ${verified} rows below were`;
    return {
      tone: "ok",
      title: "Ingredients verified against the source",
      body:
        `${subject} copied out of the source text, checked back against it character for character, and its ` +
        "amount read arithmetically rather than guessed. Names and notes still deserve a look; the numbers " +
        "have been checked.",
    };
  }

  if (path === "jsonld") {
    return {
      tone: "ok",
      title: "Ingredients came from the page's own recipe data",
      body:
        "The publisher marked this recipe up in schema.org format, so the quantities below are the ones it " +
        "states. No model read them.",
    };
  }

  const bodies = {
    no_source_text:
      "This was imported from an image, which has no text layer to check a copy against. The rows below are a " +
      "model's reading of the picture -- plausible, unchecked. Confirm every amount before saving.",
    nothing_verified:
      "Nothing in this document verified as an ingredient list, so the rows below are a model's unchecked " +
      "guesses rather than anything copied from a source. Confirm every amount -- and confirm the file you " +
      "uploaded is the recipe you meant, because a document that is not a recipe reaches this screen looking " +
      "much like one that is.",
    fewer_than_single_call:
      `Only ${verified} line${verified === 1 ? "" : "s"} could be checked against the source, set against the ` +
      `${singleCall} row${singleCall === 1 ? "" : "s"} below -- too few to stand in for the whole list, so the ` +
      "model's unchecked version is shown instead. Confirm every amount before saving.",
  };

  return {
    tone: "warn",
    title: "Ingredients are NOT verified",
    body: bodies[reason] || "These ingredients were not checked against a source. Confirm every amount before saving.",
  };
}

/** `provenance` is RecipeImportResponse.ingredient_provenance
 * (schemas/recipe.py's IngredientProvenance). Renders nothing when absent,
 * so a response from an older build degrades to today's silence rather
 * than to a wrong claim. */
export default function IngredientProvenance({ provenance }) {
  if (!provenance || !provenance.path) return null;
  const { tone, title, body } = describe(provenance);
  return (
    <div className={tone === "ok" ? "provenance-note provenance-ok" : "provenance-note provenance-warn"}>
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}
