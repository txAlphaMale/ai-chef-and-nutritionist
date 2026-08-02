import { useCallback, useEffect, useState } from "react";

// Backlog B7.2 -- step-linked cooking timers, architected the same way
// the persistent chat widget (Phase 7) and background job badge (B11.1)
// are: state lives outside any single page's component tree so a timer
// started from CookMode keeps running (visibly, via TimersBadge) even
// after navigating to another page or reloading the tab -- the backlog
// text's own "running in the same background-persistent way the chat
// widget already does" requirement. Unlike the job queue (B11.1), there
// is no server behind this -- it's a pure client-side countdown, so
// localStorage plus a tiny module-level pub/sub is the right amount of
// machinery, not a new backend endpoint. Remaining time is always
// computed from a stored wall-clock `startedAt`, never decremented in
// place, so it's correct immediately after a reload/tab-switch instead
// of drifting or needing to "catch up".

const STORAGE_KEY = "chef_cook_timers_v1";
let listeners = [];

function readTimers() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function writeTimers(timers) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(timers));
  } catch {
    // Non-fatal -- private browsing / full storage. Timers just won't
    // survive a reload in that case; the in-memory pub/sub below still
    // keeps every currently-mounted component in sync for this session.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener) {
  listeners.push(listener);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

function playChime() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    // Three short beeps rather than one -- meant to be noticeable across
    // a kitchen without anyone looking at the screen.
    [0, 0.8, 1.6].forEach((delay) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      const t0 = ctx.currentTime + delay;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.2, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.55);
      osc.start(t0);
      osc.stop(t0 + 0.6);
    });
    setTimeout(() => ctx.close().catch(() => {}), 3000);
  } catch {
    // Non-fatal -- some browsers require the audio context to be created
    // within a user-gesture call stack, which a timer completing on its
    // own isn't. The visual badge and (permission-gated) Notification
    // below are the reliable cues either way.
  }
}

function notifyDone(timer) {
  playChime();
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    try {
      new Notification("Timer done", { body: timer.label, tag: timer.id });
    } catch {
      // Non-fatal.
    }
  }
}

export function useStepTimers() {
  const [timers, setTimers] = useState(readTimers);

  useEffect(() => {
    const unsub = subscribe(() => setTimers(readTimers()));
    const tick = setInterval(() => {
      const current = readTimers();
      let changed = false;
      const next = current.map((t) => {
        const remaining = t.durationSeconds - (Date.now() - t.startedAt) / 1000;
        if (remaining <= 0 && !t.notified) {
          changed = true;
          notifyDone(t);
          return { ...t, notified: true };
        }
        return t;
      });
      if (changed) {
        writeTimers(next);
      } else {
        // Still force a re-render every second so remainingSeconds
        // (derived below, not stored) stays live even when nothing
        // "changed" in the persisted sense.
        setTimers(current);
      }
    }, 1000);
    return () => {
      unsub();
      clearInterval(tick);
    };
  }, []);

  const start = useCallback(({ label, durationSeconds }) => {
    const timer = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      label,
      durationSeconds,
      startedAt: Date.now(),
      notified: false,
    };
    writeTimers([...readTimers(), timer]);
    // Ask for notification permission lazily, on this deliberate,
    // user-initiated "start a timer" tap -- not on page load, which
    // browsers increasingly block/penalize.
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  const dismiss = useCallback((id) => {
    writeTimers(readTimers().filter((t) => t.id !== id));
  }, []);

  const withRemaining = timers.map((t) => ({
    ...t,
    remainingSeconds: Math.max(0, t.durationSeconds - (Date.now() - t.startedAt) / 1000),
  }));

  return { timers: withRemaining, start, dismiss };
}
