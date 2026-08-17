/** One source for every DATE and TIMESTAMP the UI renders.
 *
 * Capstone review 2026-08-16. Elapsed/countdown DURATIONS were already
 * centralized -- `formatDuration` in `cookingText.js` is shared by
 * JobsBadge, TimersBadge and CookTimersPanel, which is what made the
 * bookmarks-import progress readable. Calendar dates and timestamps had
 * no such single source, and had drifted into three different
 * presentations of the same kind of value:
 *
 *   - raw ISO, rendered straight from the API           "2026-08-16"
 *     (inventory expiry, health entry_date, recall date, week_start_date)
 *   - `new Date(x).toLocaleString()`                    "8/16/2026, 3:04:12 PM"
 *     (inventory recall "last checked")
 *   - `new Date(x).toLocaleDateString()`                "8/16/2026"
 *     (Settings > Security certificate expiry)
 *
 * so the same app showed a date three ways on three pages. Everything
 * below is deliberately built on `Intl`/`toLocaleDateString` with an
 * explicit option bag rather than a hand-rolled string, so the household's
 * own locale decides day/month order instead of this file assuming US
 * convention.
 *
 * Rules of thumb for call sites:
 *   - `formatDate`      -- a calendar day the user thinks of as a day
 *                          (expiry, lab draw date, week start).
 *   - `formatDateTime`  -- a machine event where the time of day matters
 *                          (last recall check, last sync, job finished).
 *   - `formatTimestamp` -- the same, with SECONDS, where ordering within a
 *                          minute is the point (the Logs view).
 *   - `formatRelativeDay` -- pair with `formatDate` when "is this soon?"
 *                          is the actual question being asked (expiry
 *                          urgency), never on its own: "in 3 days" without
 *                          the date is harder to act on, not easier.
 *
 * Every function tolerates null/undefined/unparseable input and returns
 * the em-dash placeholder the tables already use, so no call site needs
 * its own `x ? ... : "—"` guard.
 */

export const EMPTY_VALUE = "—";

/** Accepts a Date, an epoch-seconds or epoch-milliseconds number, or a
 * string. Returns a valid Date or null -- never an Invalid Date, which
 * stringifies to the useless "Invalid Date" if it reaches the DOM.
 *
 * Date-only ISO strings ("2026-08-16") are parsed as LOCAL midnight, not
 * UTC midnight. This matters and is the reason this helper exists rather
 * than a bare `new Date(value)`: the spec parses a bare date-only string
 * as UTC, so west of Greenwich `new Date("2026-08-16").toLocaleDateString()`
 * renders as August 15th. Every date-only value in this app (expiration
 * dates, entry dates, week starts) is a wall-calendar date the user typed
 * or picked, so local is the correct reading.
 */
export function toDate(value) {
  if (value == null || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  if (typeof value === "number") {
    // Epoch seconds vs milliseconds: anything below ~1e11 cannot be a
    // sensible millisecond timestamp (1e11 ms is 1973), so treat it as
    // seconds. `tlsStatus.expires_at` is the one epoch-seconds field in
    // this app's API surface today.
    const ms = value < 1e11 ? value * 1000 : value;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  if (typeof value === "string") {
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (dateOnly) {
      const d = new Date(
        Number(dateOnly[1]),
        Number(dateOnly[2]) - 1,
        Number(dateOnly[3]),
      );
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  return null;
}

/** "Aug 16, 2026" -- a calendar day. Month is abbreviated rather than
 * numeric so "08/09" is never ambiguous between August 9th and September
 * 8th for anyone reading over the cook's shoulder. */
export function formatDate(value, { fallback = EMPTY_VALUE } = {}) {
  const d = toDate(value);
  if (!d) return fallback;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** "Aug 16, 2026, 3:04 PM" -- a machine event. Seconds are deliberately
 * omitted: nothing in this app is actionable at second resolution, and
 * they made the recall "last checked" line noticeably harder to scan. */
export function formatDateTime(value, { fallback = EMPTY_VALUE } = {}) {
  const d = toDate(value);
  if (!d) return fallback;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** "Aug 16, 2026, 3:04:12 PM" -- the same thing as `formatDateTime` but
 * with SECONDS, for machine output where ordering within a minute is the
 * point.
 *
 * Added 2026-08-16 after looking at the rendered Logs view: nine entries
 * written milliseconds apart all displayed as "5:50 PM", which makes the
 * one thing a log is for -- what happened in what order -- unreadable.
 * `formatDateTime` is still right everywhere else; "last recall check,
 * 8:00:21 PM" is noise, and dropping the seconds there was the point.
 * Two precisions, one source, each documented for when it applies. */
export function formatTimestamp(value, { fallback = EMPTY_VALUE } = {}) {
  const d = toDate(value);
  if (!d) return fallback;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Whole days from today to `value`, positive for future. Null when the
 * value will not parse. Both sides are floored to local midnight first,
 * so "tomorrow" is 1 whether it is now 6am or 11pm -- a difference in
 * clock time within the same day must not read as a different number of
 * days until an item expires. */
export function daysUntil(value) {
  const d = toDate(value);
  if (!d) return null;
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const MS_PER_DAY = 86400000;
  return Math.round((startOf(d) - startOf(new Date())) / MS_PER_DAY);
}

/** "today" / "tomorrow" / "in 5 days" / "3 days ago". Intended as a
 * SUFFIX beside `formatDate`, not a replacement for it. */
export function formatRelativeDay(value, { fallback = "" } = {}) {
  const days = daysUntil(value);
  if (days == null) return fallback;
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 1) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

/** The two together: "Aug 16, 2026 (in 3 days)". Used where expiry
 * urgency is the point of showing the date at all. */
export function formatDateWithRelative(value, { fallback = EMPTY_VALUE } = {}) {
  const d = toDate(value);
  if (!d) return fallback;
  const relative = formatRelativeDay(value);
  return relative ? `${formatDate(value)} (${relative})` : formatDate(value);
}

/** Today as `YYYY-MM-DD` in LOCAL time, for prefilling `<input type="date">`.
 * Replaces the `new Date().toISOString().slice(0, 10)` idiom that HealthPage
 * and MealPlanPage each defined separately -- that idiom is UTC, so after
 * 7pm Central it prefills tomorrow's date. */
export function todayIso() {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** `YYYY-MM-DD` in LOCAL time for any Date -- the inverse of `toDate`'s
 * date-only branch, for round-tripping a picked date back to the API. */
export function toIsoDate(value) {
  const d = toDate(value);
  if (!d) return "";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
