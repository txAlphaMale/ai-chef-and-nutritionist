import InfoTip from "./InfoTip";
import {
  NOVA_EXPLANATION,
  NUTRISCORE_EXPLANATION,
  hasAnyFoodGrade,
  novaChipClass,
  novaLabel,
  nutriscoreChipClass,
  nutriscoreLabel,
} from "../utils/foodGrades";

/** The NOVA / Nutri-Score pair, drawn one way everywhere.
 *
 * Backlog B19.1. Two things this component does that a pair of inline
 * spans at each call site would not:
 *
 * **It refuses to imply a clearance.** When Open Food Facts has neither
 * classification it renders NOTHING in `compact` mode and an explicit
 * "not classified" line otherwise -- never an empty chip, which on a row
 * of items reads as "this one is fine". Absence of a NOVA group is
 * common and correlates with an unreadable ingredient list, so a blank
 * that looked like a pass would be backwards.
 *
 * **It carries its own attribution.** Both scales are somebody else's
 * judgement, and Nutri-Score in particular grades nutrient composition
 * per 100g rather than whether a food belongs in a diet. The InfoTip is
 * part of the chip, not an optional extra a call site might omit; see
 * utils/foodGrades.js for the wording and why.
 *
 * `compact` is for the Inventory table, where a row already carries six
 * other values and the chips are secondary. The full form is for the
 * scanner preview, where the classification is one of the reasons the
 * scan was worth doing and is worth a sentence.
 */
export default function FoodGradeChips({ item, compact = false }) {
  const nova = novaLabel(item?.nova_group);
  const nutriscore = nutriscoreLabel(item?.nutriscore_grade);

  if (!hasAnyFoodGrade(item)) {
    if (compact) return null;
    return (
      <p className="hint">
        Open Food Facts has no NOVA group or Nutri-Score for this product. That is common — both are worked
        out from a crowd-sourced ingredient list, and a missing one means nobody has filled it in, not that
        the food is unprocessed.
      </p>
    );
  }

  return (
    <div className={compact ? "food-grades food-grades-compact" : "food-grades"}>
      {nova ? (
        <span className={novaChipClass(item.nova_group)} title={nova}>
          {compact ? `NOVA ${item.nova_group}` : nova}
        </span>
      ) : null}
      {nutriscore ? <span className={nutriscoreChipClass(item.nutriscore_grade)}>{nutriscore}</span> : null}
      {compact ? null : (
        <InfoTip label="NOVA group and Nutri-Score" wikiEntry="food-classification">
          {NOVA_EXPLANATION} {NUTRISCORE_EXPLANATION}
        </InfoTip>
      )}
    </div>
  );
}
