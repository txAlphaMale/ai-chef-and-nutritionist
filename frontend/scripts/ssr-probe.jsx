/* Render probe -- the third frontend gate, alongside eslint and `vite build`.
 *
 * Committed 2026-08-16 (capstone review). This check was written and run
 * ad hoc on 2026-08-07 after a temporal-dead-zone ReferenceError in
 * `RecipesPage.jsx` blanked the entire Recipes page in the shipped
 * bundle -- and was then never committed, so the gate that caught a
 * whole-page outage lived only in that session's transcript. This file
 * makes it repeatable and puts it in CI.
 *
 * WHY THE OTHER TWO GATES CANNOT CATCH THIS. A bundler has no opinion
 * about runtime order, so `vite build` is happy to emit code that throws
 * on its first render. eslint now has `no-use-before-define` on (added
 * the same day, for the same bug), but a linter only catches the
 * statically visible shape of the problem -- any render-time throw that
 * is not lexically obvious still gets through. Neither gate ever
 * EXECUTES a component. This one does.
 *
 * `renderToString` runs the render pass and deliberately does not run
 * effects, which is exactly the split wanted here: render-time crashes
 * (TDZ errors, destructuring null, calling an undefined import) fail
 * loudly, while the data-loading `useEffect`s every page in this app has
 * simply never fire, so no network, no Ollama and no database are
 * needed.
 *
 * WHAT A PASS DOES AND DOES NOT MEAN. A pass means every page mounts and
 * completes one render against empty initial state. It does not mean the
 * page works: nothing here clicks anything, and no effect, event handler
 * or post-fetch re-render is exercised. Treat it as a smoke alarm, not
 * an integration test.
 */

import React from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import HomePage from "../src/pages/HomePage";
import InventoryPage from "../src/pages/InventoryPage";
import RecipesPage from "../src/pages/RecipesPage";
import RecipeDetailPage from "../src/pages/RecipeDetailPage";
import MealPlanPage from "../src/pages/MealPlanPage";
import DiningPage from "../src/pages/DiningPage";
import HealthPage from "../src/pages/HealthPage";
import SettingsPage from "../src/pages/SettingsPage";
import WikiPage from "../src/pages/WikiPage";

import ChatWidget from "../src/components/ChatWidget";
import LoginGate from "../src/components/LoginGate";
import ExpiringDigestBanner from "../src/components/ExpiringDigestBanner";
import RecallBanner from "../src/components/RecallBanner";
import JobsBadge from "../src/components/JobsBadge";
import TimersBadge from "../src/components/TimersBadge";

// Every route in App.jsx, plus the five always-mounted shell components
// that live OUTSIDE <Routes> -- those render on every page in the real
// app, so a render-time throw in one of them blanks all of them, which
// is strictly worse than a single bad page. `path` mirrors App.jsx so a
// route added there without a line here is visible in the diff.
const SUBJECTS = [
  { name: "HomePage", path: "/", element: <HomePage /> },
  { name: "InventoryPage", path: "/inventory", element: <InventoryPage /> },
  { name: "RecipesPage", path: "/recipes", element: <RecipesPage /> },
  // A real id, not ":id" -- RecipeDetailPage reads useParams().id and a
  // literal colon would be a genuinely different (and misleading) input.
  { name: "RecipeDetailPage", path: "/recipes/1", element: <RecipeDetailPage /> },
  { name: "MealPlanPage", path: "/meal-plan", element: <MealPlanPage /> },
  { name: "DiningPage", path: "/dining", element: <DiningPage /> },
  { name: "HealthPage", path: "/health", element: <HealthPage /> },
  { name: "SettingsPage", path: "/settings", element: <SettingsPage /> },
  { name: "WikiPage", path: "/wiki", element: <WikiPage /> },

  { name: "ChatWidget", path: "/", element: <ChatWidget /> },
  { name: "LoginGate", path: "/", element: <LoginGate onSuccess={() => {}} /> },
  { name: "ExpiringDigestBanner", path: "/", element: <ExpiringDigestBanner /> },
  { name: "RecallBanner", path: "/", element: <RecallBanner /> },
  { name: "JobsBadge", path: "/", element: <JobsBadge /> },
  { name: "TimersBadge", path: "/", element: <TimersBadge /> },
];

// Browser globals these components touch during render (not only in
// effects). Stubbed rather than polyfilled -- the probe asks "does this
// render", and a component that reads localStorage for its initial state
// should get a well-formed empty answer, not a crash that masks the
// render error this file exists to find.
function installBrowserStubs() {
  const store = new Map();
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };

  globalThis.localStorage ??= storage;
  globalThis.sessionStorage ??= storage;

  // Rejecting, not resolving: a fetch that never succeeds proves no
  // subject depends on network data to complete a first render. Effects
  // do not run under renderToString, so in practice this is belt and
  // braces.
  globalThis.fetch ??= () => Promise.reject(new Error("ssr-probe: no network"));

  globalThis.window ??= globalThis;
  globalThis.navigator ??= { userAgent: "ssr-probe", language: "en-US" };
  globalThis.matchMedia ??= () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
  globalThis.document ??= {
    documentElement: { style: { setProperty() {} }, dataset: {}, classList: { add() {}, remove() {} } },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
    body: { appendChild() {}, classList: { add() {}, remove() {} } },
  };
  globalThis.__BUILD_ID__ ??= "ssr-probe";
}

/** react-router calls `useLayoutEffect` unconditionally, which React warns
 * about once per subject under `renderToString`. It is expected, it is not
 * this app's code, and fifteen copies of it buried the pass/fail lines that
 * are the entire point of the output. Only this exact warning is dropped --
 * anything else React wants to say still gets through. */
function silenceUseLayoutEffectWarning() {
  const original = console.error;
  console.error = (...args) => {
    if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) return;
    original(...args);
  };
}

function main() {
  installBrowserStubs();
  silenceUseLayoutEffectWarning();

  const failures = [];
  for (const subject of SUBJECTS) {
    try {
      renderToString(
        <MemoryRouter initialEntries={[subject.path]}>
          <Routes>
            <Route path="*" element={subject.element} />
          </Routes>
        </MemoryRouter>,
      );
      process.stdout.write(`  ok   ${subject.name}\n`);
    } catch (error) {
      failures.push({ name: subject.name, error });
      process.stdout.write(`  FAIL ${subject.name}\n`);
    }
  }

  if (failures.length > 0) {
    process.stderr.write(`\n${failures.length} of ${SUBJECTS.length} subject(s) threw during render:\n\n`);
    for (const { name, error } of failures) {
      process.stderr.write(`--- ${name} ---\n${error && error.stack ? error.stack : String(error)}\n\n`);
    }
    process.exitCode = 1;
    return;
  }

  process.stdout.write(`\nssr-probe: ${SUBJECTS.length} subjects rendered cleanly.\n`);
}

main();
