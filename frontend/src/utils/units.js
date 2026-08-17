/** Weight unit conversion, in one place.
 *
 * Capstone review 2026-08-16. `kgToLbs`/`lbsToKg` were defined inside
 * `HealthPage.jsx`, which was fine while the Health page was the only
 * thing that showed a weight. The Home dashboard now shows one too, and a
 * second copy of a conversion constant is how two pages start disagreeing
 * about what somebody weighs.
 *
 * Same reasoning as `datetime.js` and `theme.css`: one source per kind of
 * thing. The API stores and returns kilograms; the household reads pounds.
 */

/** Exact, by definition (international avoirdupois pound, 1959). Not an
 * approximation worth rounding in a constant. */
export const KG_PER_LB = 0.45359237;

/** Kilograms in, pounds out, rounded to one decimal -- the precision a
 * bathroom scale actually offers. Returns "" for null/undefined so it can
 * be dropped straight into a form value. */
export function kgToLbs(kg) {
  return kg == null ? "" : Math.round((kg / KG_PER_LB) * 10) / 10;
}

/** Pounds in, kilograms out, rounded to two decimals. Returns null for an
 * empty field, so an untouched input stores nothing rather than zero. */
export function lbsToKg(lbs) {
  return lbs === "" || lbs == null ? null : Math.round(Number(lbs) * KG_PER_LB * 100) / 100;
}
