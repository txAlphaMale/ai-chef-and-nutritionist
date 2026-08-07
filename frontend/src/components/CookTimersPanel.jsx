import { useEffect, useState } from "react";
import { api } from "../api";
import { useStepTimers, WARN_AT_SECONDS } from "../hooks/useStepTimers";
import { formatDuration } from "../utils/cookingText";

// Named, hand-started cooking timers, alongside the step-linked ones
// B7.2 already parsed out of instruction text.
//
// Why more than one: a cook running a real meal has the tray in the oven,
// the pan on the hob and the dough resting, and a single timer forces
// them to choose which one the app is allowed to remember. Why a CAP
// (`cook_timer_max_widgets`, default 3): this panel is read from across a
// kitchen, at a glance, usually with wet hands. Past a few rows it stops
// being glanceable and becomes a list to search, which is worse than
// having no panel. The number is a setting because "a few" is a household
// judgement, not ours.
//
// The timers themselves live in useStepTimers' store, NOT here -- so one
// started in this panel keeps running, and keeps showing in TimersBadge,
// after the cook navigates away or reloads. This component is a control
// surface over that store, not a second one.

const SOUND_PREF_KEY = "chef_timer_sounds_v1";

/** The household's last-used warning/finish sounds. Read by the step
 * timer button too, so the pair is chosen once rather than on every
 * timer -- mid-cook is the wrong moment to be picking from a dropdown. */
export function readPreferredSounds() {
  try {
    const raw = localStorage.getItem(SOUND_PREF_KEY);
    return raw ? JSON.parse(raw) : { warnSoundId: null, doneSoundId: null };
  } catch {
    return { warnSoundId: null, doneSoundId: null };
  }
}

function writePreferredSounds(prefs) {
  try {
    localStorage.setItem(SOUND_PREF_KEY, JSON.stringify(prefs));
  } catch {
    // Non-fatal -- private browsing. The choice just won't outlive the tab.
  }
}

export default function CookTimersPanel() {
  const { timers, start, dismiss } = useStepTimers();
  const [sounds, setSounds] = useState([]);
  const [maxWidgets, setMaxWidgets] = useState(3);
  const [label, setLabel] = useState("");
  const [minutes, setMinutes] = useState("10");
  const [seconds, setSeconds] = useState("0");
  const [prefs, setPrefs] = useState(readPreferredSounds);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [library, settings] = await Promise.all([api.get("/sounds"), api.get("/system/settings")]);
        setSounds(library);
        const configured = Number(settings.find((s) => s.key === "cook_timer_max_widgets")?.value);
        // A blank, zero or nonsense setting must not silently disable the
        // feature -- fall back to the shipped default rather than to a
        // panel that refuses every timer with no explanation.
        setMaxWidgets(Number.isFinite(configured) && configured > 0 ? Math.floor(configured) : 3);

        // First run has no preference, and a dropdown defaulting to
        // "(none)" would mean a timer that ends in silence. Seed from the
        // library's own first entries instead.
        setPrefs((current) => {
          if (current.warnSoundId || current.doneSoundId || library.length === 0) return current;
          const seeded = { warnSoundId: library[0].id, doneSoundId: library[Math.min(1, library.length - 1)].id };
          writePreferredSounds(seeded);
          return seeded;
        });
      } catch (e) {
        setError(e.message);
      }
    })();
  }, []);

  function setPreference(key, value) {
    const next = { ...prefs, [key]: value ? Number(value) : null };
    setPrefs(next);
    writePreferredSounds(next);
  }

  function preview(soundId) {
    if (!soundId) return;
    // Deliberately fire-and-forget, and deliberately inside a click: some
    // browsers only allow audio to start from a user gesture, and this
    // button is the one place the household can find out whether a sound
    // is audible in their kitchen BEFORE trusting it with a roast.
    new Audio(`/api/sounds/${soundId}/audio`).play().catch(() => {});
  }

  function addTimer(event) {
    event.preventDefault();
    setError(null);
    const total = Math.round(Number(minutes || 0) * 60 + Number(seconds || 0));
    if (!Number.isFinite(total) || total <= 0) {
      setError("Set a duration first.");
      return;
    }
    if (timers.length >= maxWidgets) {
      setError(`That's the maximum of ${maxWidgets} timers. Dismiss one, or raise the limit in Settings.`);
      return;
    }
    start({
      label: label.trim() || `Timer ${timers.length + 1}`,
      durationSeconds: total,
      warnSoundId: prefs.warnSoundId,
      doneSoundId: prefs.doneSoundId,
    });
    setLabel("");
  }

  const atLimit = timers.length >= maxWidgets;

  return (
    <section className="cook-timers-panel" aria-label="Cooking timers">
      <h3>
        Timers <span className="cook-timers-count">{timers.length} of {maxWidgets}</span>
      </h3>

      {timers.length > 0 && (
        <ul className="cook-timers-list">
          {timers.map((t) => {
            const done = t.remainingSeconds <= 0;
            const warning = !done && t.remainingSeconds <= WARN_AT_SECONDS;
            return (
              <li key={t.id} className={`cook-timer${done ? " cook-timer-done" : warning ? " cook-timer-warning" : ""}`}>
                <span className="cook-timer-label">{t.label}</span>
                <span className="cook-timer-time">{done ? "Done" : formatDuration(t.remainingSeconds)}</span>
                <button type="button" className="btn-link" onClick={() => dismiss(t.id)} aria-label={`Dismiss ${t.label}`}>
                  ✕
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <form className="cook-timer-form" onSubmit={addTimer}>
        <label>
          Name
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Roast" />
        </label>
        <label>
          Min
          <input type="number" min="0" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </label>
        <label>
          Sec
          <input type="number" min="0" max="59" value={seconds} onChange={(e) => setSeconds(e.target.value)} />
        </label>
        <label>
          Warning ({WARN_AT_SECONDS}s left)
          <select value={prefs.warnSoundId || ""} onChange={(e) => setPreference("warnSoundId", e.target.value)}>
            <option value="">Silent</option>
            {sounds.map((s) => (
              <option key={s.id} value={s.id} disabled={s.missing_file}>
                {s.name}
                {s.missing_file ? " (file missing)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Finish
          <select value={prefs.doneSoundId || ""} onChange={(e) => setPreference("doneSoundId", e.target.value)}>
            <option value="">Silent</option>
            {sounds.map((s) => (
              <option key={s.id} value={s.id} disabled={s.missing_file}>
                {s.name}
                {s.missing_file ? " (file missing)" : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="cook-timer-form-actions">
          <button type="button" className="btn btn-secondary" onClick={() => preview(prefs.warnSoundId)}>
            Test warning
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => preview(prefs.doneSoundId)}>
            Test finish
          </button>
          <button type="submit" className="btn btn-primary" disabled={atLimit}>
            Add timer
          </button>
        </div>
      </form>

      {atLimit && !error && (
        <p className="hint">
          All {maxWidgets} timers are in use. Dismiss one, or raise "Cook mode: maximum simultaneous timers" in
          Settings.
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
