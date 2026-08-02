// Backlog B7.1/B7.2 -- small, pure text-parsing helpers used by cook mode
// and step-linked timers. Deliberately regex-based and conservative
// (skip rather than guess on anything ambiguous) -- same discipline the
// backend's guess_grocery_category/foodkeeper matcher use for their own
// "wrong guess is a minor inconvenience, not worth being clever about"
// tradeoffs. Kept framework-free and independently unit-testable.

// Matches "1 hour 15 minutes" / "1 hr 30 min" as a single combined
// duration -- checked before the simpler single-unit pattern below so it
// isn't parsed as just "1 hour".
const COMBINED_RE = /(\d+)\s*(?:hours?|hrs?)\s+(?:and\s+)?(\d+)\s*(?:minutes?|mins?)\b/i;

// A single number (optionally a range, e.g. "8-10 minutes" or "8 to 10
// minutes") plus a time unit. Anchors on the LOWER/first bound of a range
// -- same "sooner, more conservative end" convention foodkeeper_service.py
// uses server-side for shelf-life suggestions.
const SINGLE_RE = /(\d+)(?:\s*(?:-|–|to)\s*\d+)?\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b/i;

const MAX_REASONABLE_SECONDS = 12 * 60 * 60; // 12 hours -- anything longer is almost certainly a mis-parse.

/** Returns { seconds, label } for the first plausible duration mentioned
 * in a recipe instruction step, or null if nothing matched. Never throws
 * on malformed/empty input. */
export function parseStepDuration(text) {
  if (!text || typeof text !== "string") return null;

  const combined = text.match(COMBINED_RE);
  if (combined) {
    const hours = parseInt(combined[1], 10);
    const minutes = parseInt(combined[2], 10);
    const seconds = hours * 3600 + minutes * 60;
    if (seconds > 0 && seconds <= MAX_REASONABLE_SECONDS) {
      return { seconds, label: `${hours}h ${minutes}m` };
    }
  }

  const single = text.match(SINGLE_RE);
  if (!single) return null;
  const value = parseInt(single[1], 10);
  const unit = single[2].toLowerCase();
  let seconds;
  let label;
  if (unit.startsWith("h")) {
    seconds = value * 3600;
    label = `${value}h`;
  } else if (unit.startsWith("m")) {
    seconds = value * 60;
    label = `${value}m`;
  } else {
    seconds = value;
    label = `${value}s`;
  }
  if (seconds <= 0 || seconds > MAX_REASONABLE_SECONDS) return null;
  return { seconds, label };
}

// Requires an explicit degree marker (° or the word "degree(s)") before
// the F/C letter -- deliberately NOT a bare "\d+\s*[FC]\b" pattern, which
// would false-positive on things like "2 C water" (cup, not Celsius) in
// loosely-written instruction text.
const TEMP_RE = /(-?\d+(?:\.\d+)?)\s*°\s*([FCfc])\b|(-?\d+(?:\.\d+)?)\s*degrees?\s*([FCfc])\b/g;

/** Returns `text` with a converted temperature appended in parentheses
 * after every degree-marked F/C mention, e.g. "350°F" -> "350°F (177°C)".
 * Leaves everything else untouched; never throws on malformed/empty input. */
export function annotateTemperatures(text) {
  if (!text || typeof text !== "string") return text;
  return text.replace(TEMP_RE, (match, v1, u1, v2, u2) => {
    const raw = v1 ?? v2;
    const unitRaw = u1 ?? u2;
    const value = parseFloat(raw);
    if (Number.isNaN(value)) return match;
    const unit = unitRaw.toUpperCase();
    const converted = unit === "F" ? ((value - 32) * 5) / 9 : (value * 9) / 5 + 32;
    const otherUnit = unit === "F" ? "C" : "F";
    return `${match} (${Math.round(converted)}°${otherUnit})`;
  });
}

/** mm:ss (or h:mm:ss once past an hour) formatting for a countdown display. */
export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}
