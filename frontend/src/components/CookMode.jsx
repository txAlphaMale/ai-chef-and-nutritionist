import { useEffect, useRef, useState } from "react";
import { useStepTimers } from "../hooks/useStepTimers";
import CookTimersPanel, { readPreferredSounds } from "./CookTimersPanel";
import { annotateTemperatures, parseStepDuration } from "../utils/cookingText";

// Backlog B7.1 -- full-screen, large-type, step-at-a-time cook-mode view.
// Table stakes in this category (Mealime, Plan to Eat, etc. all have
// one) that this app had none of -- recipes rendered as a single static
// page with no cooking-time affordance at all. Deliberately a plain
// overlay `<div>` toggled from RecipeDetailPage rather than a new route:
// this app uses HashRouter with no server-side history fallback need,
// and a route change would also make the browser back button exit cook
// mode in a way that's easy to trigger by accident mid-recipe.
export default function CookMode({ recipe, onExit }) {
  // {component, text} whatever the API sent: recipes saved before steps
  // had components are plain strings and the backend coerces on read
  // rather than migrating the column, so this has to tolerate both or an
  // older recipe cooks as a stack of blank steps.
  const steps = (recipe.instructions || []).map((s) =>
    typeof s === "string" ? { component: null, text: s } : { component: s.component || null, text: s.text || "" }
  );
  const progressKey = `chef_cook_progress_${recipe.id}`;

  const [stepIndex, setStepIndex] = useState(0);
  const [checked, setChecked] = useState({});
  const [showIngredients, setShowIngredients] = useState(false);
  const [showStepList, setShowStepList] = useState(false);
  const [wakeLockOn, setWakeLockOn] = useState(true);
  const [wakeLockActive, setWakeLockActive] = useState(false);
  const [wakeLockSupported, setWakeLockSupported] = useState(true);
  const wakeLockRef = useRef(null);
  const { start: startTimer } = useStepTimers();

  // Resume-in-progress: a household member who accidentally navigates
  // away (or the tab reloads) mid-recipe shouldn't lose their place --
  // same "don't lose in-progress state" discipline as the B11.1 job
  // queue's localStorage resume, just for a purely client-side concern.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(progressKey);
      if (raw) {
        const saved = JSON.parse(raw);
        if (typeof saved.stepIndex === "number") setStepIndex(saved.stepIndex);
        if (saved.checked) setChecked(saved.checked);
      }
    } catch {
      // Non-fatal -- starts from step 1 instead.
    }
     
  }, [progressKey]);

  useEffect(() => {
    try {
      localStorage.setItem(progressKey, JSON.stringify({ stepIndex, checked }));
    } catch {
      // Non-fatal.
    }
  }, [progressKey, stepIndex, checked]);

  // Screen Wake Lock API (backlog B7.1's explicit ask). Requires a
  // user-initiated context to acquire -- entering cook mode via a button
  // click satisfies that. The browser releases the lock automatically
  // whenever the tab is hidden (switching apps to check a timer
  // notification, etc.), so it's re-acquired on visibilitychange if the
  // toggle is still on and cook mode is still open, per the API's own
  // documented usage pattern (MDN).
  useEffect(() => {
    if (!("wakeLock" in navigator)) {
      setWakeLockSupported(false);
      return;
    }
    let cancelled = false;

    async function acquire() {
      if (!wakeLockOn) return;
      try {
        const lock = await navigator.wakeLock.request("screen");
        if (cancelled) {
          lock.release().catch(() => {});
          return;
        }
        wakeLockRef.current = lock;
        setWakeLockActive(true);
        lock.addEventListener("release", () => setWakeLockActive(false));
      } catch {
        // Non-fatal -- e.g. denied, or the device is in low-power mode.
        // The checkbox and its battery-impact hint stay visible either
        // way so the user knows the feature exists and its state.
        setWakeLockActive(false);
      }
    }

    async function release() {
      if (wakeLockRef.current) {
        await wakeLockRef.current.release().catch(() => {});
        wakeLockRef.current = null;
      }
      setWakeLockActive(false);
    }

    if (wakeLockOn) {
      acquire();
    } else {
      release();
    }

    function handleVisibility() {
      if (document.visibilityState === "visible" && wakeLockOn && !wakeLockRef.current) {
        acquire();
      }
    }
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibility);
      release();
    };
  }, [wakeLockOn]);

  function markDone(index) {
    setChecked((c) => ({ ...c, [index]: true }));
  }

  function goNext() {
    markDone(stepIndex);
    if (stepIndex < steps.length - 1) {
      setStepIndex((i) => i + 1);
    } else {
      setStepIndex(steps.length); // one past the end -- renders the "done" screen below.
    }
  }

  function goBack() {
    setStepIndex((i) => Math.max(0, i - 1));
  }

  function finish() {
    try {
      localStorage.removeItem(progressKey);
    } catch {
      // Non-fatal.
    }
    onExit();
  }

  const atEnd = stepIndex >= steps.length;
  const currentStep = !atEnd ? steps[stepIndex] : null;
  const currentDuration = currentStep ? parseStepDuration(currentStep.text) : null;

  return (
    <div className="cook-mode-overlay" role="dialog" aria-modal="true" aria-label={`Cook mode: ${recipe.title}`}>
      <div className="cook-mode-header">
        <strong>{recipe.title}</strong>
        <button type="button" className="btn btn-secondary btn-sm" onClick={finish}>
          Exit cook mode
        </button>
      </div>

      {wakeLockSupported && (
        <label className="checkbox-label cook-mode-wakelock">
          <input type="checkbox" checked={wakeLockOn} onChange={(e) => setWakeLockOn(e.target.checked)} />
          Keep screen on while cooking{" "}
          <span className="hint">
            ({wakeLockActive ? "active" : "off"} -- uses more battery; turn off if you're not plugged in)
          </span>
        </label>
      )}
      {!wakeLockSupported && (
        <p className="hint">Your browser doesn't support keeping the screen on automatically -- adjust your device's own screen-timeout setting if it locks mid-step.</p>
      )}

      <div className="cook-mode-toolbar">
        <button type="button" className="btn-link" onClick={() => setShowIngredients((v) => !v)}>
          {showIngredients ? "Hide ingredients" : "Show ingredients"}
        </button>
        {steps.length > 0 && (
          <button type="button" className="btn-link" onClick={() => setShowStepList((v) => !v)}>
            {showStepList ? "Hide step list" : "Jump to step..."}
          </button>
        )}
      </div>

      {showIngredients && (
        <ul className="cook-mode-ingredients">
          {(recipe.ingredients || []).map((ing, i) => (
            <li key={i}>
              {ing.quantity != null ? `${ing.quantity} ` : ""}
              {ing.unit ? `${ing.unit} ` : ""}
              {ing.ingredient_name}
              {ing.prep_note ? `, ${ing.prep_note}` : ""}
            </li>
          ))}
        </ul>
      )}

      {showStepList && (
        <ol className="cook-mode-step-list">
          {steps.map((s, i) => (
            <li key={i}>
              <button
                type="button"
                className={`btn-link${checked[i] ? " cook-mode-step-done" : ""}`}
                onClick={() => {
                  setStepIndex(i);
                  setShowStepList(false);
                }}
              >
                {checked[i] ? "✓ " : ""}
                Step {i + 1}: {s.component ? `[${s.component}] ` : ""}
                {s.text.slice(0, 60)}
                {s.text.length > 60 ? "..." : ""}
              </button>
            </li>
          ))}
        </ol>
      )}

      {atEnd ? (
        <div className="cook-mode-step cook-mode-finished">
          <h2>All steps done!</h2>
          <p>Enjoy -- don't forget to confirm this meal on the Meal Plan page so inventory stays accurate.</p>
          <button type="button" className="btn btn-primary" onClick={finish}>
            Exit cook mode
          </button>
        </div>
      ) : (
        <div className="cook-mode-step">
          <p className="cook-mode-step-counter">
            Step {stepIndex + 1} of {steps.length}
            {currentStep.component ? ` -- ${currentStep.component}` : ""}
          </p>
          <p className="cook-mode-step-text">{annotateTemperatures(currentStep.text)}</p>

          {currentDuration && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                startTimer({
                  label: `${recipe.title} -- step ${stepIndex + 1}`,
                  durationSeconds: currentDuration.seconds,
                  // The pair chosen in the timers panel below, so a
                  // step timer and a hand-made one sound the same.
                  ...readPreferredSounds(),
                })
              }
            >
              Start timer ({currentDuration.label})
            </button>
          )}

          <CookTimersPanel />

          <div className="cook-mode-nav">
            <button type="button" className="btn btn-secondary" onClick={goBack} disabled={stepIndex === 0}>
              &larr; Back
            </button>
            <label className="checkbox-label">
              <input type="checkbox" checked={!!checked[stepIndex]} onChange={() => markDone(stepIndex)} />
              Done
            </label>
            <button type="button" className="btn btn-primary" onClick={goNext}>
              {stepIndex < steps.length - 1 ? "Next →" : "Finish"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
