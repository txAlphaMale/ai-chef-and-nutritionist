import { useStepTimers } from "../hooks/useStepTimers";
import { formatDuration } from "../utils/cookingText";

// Backlog B7.2 -- app-wide, always-mounted (App.jsx, outside <Routes>,
// same placement as JobsBadge/ChatWidget) view of every running cooking
// timer, so a timer started from CookMode stays visible while the user
// wanders off to check the Grocery list or Inventory mid-cook.
export default function TimersBadge() {
  const { timers, dismiss } = useStepTimers();

  if (timers.length === 0) return null;

  return (
    <div className="timers-badge-stack" role="status">
      {timers.map((t) => {
        const done = t.remainingSeconds <= 0;
        return (
          <div key={t.id} className={`timers-badge${done ? " timers-badge-done" : ""}`}>
            <span>
              {done ? "Done: " : ""}
              {t.label}
            </span>
            <span className="timers-badge-time">{formatDuration(t.remainingSeconds)}</span>
            <button type="button" className="btn-link" onClick={() => dismiss(t.id)} aria-label={`Dismiss timer: ${t.label}`}>
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}
