// Audit P3: the frontend had no linter, so unused imports, undeclared
// globals and React-hook dependency mistakes went unchecked across ~11k
// lines. Flat config (ESLint 9).
//
// Rule selection is deliberately narrow, for the same reason as the
// backend's ruff config: a linter that fires on things nobody intends to
// change gets ignored, which is worse than not having one. Everything
// enabled below catches either a real bug or something genuinely
// misleading to read. Formatting is not linted at all -- Prettier is not
// installed and reformatting the whole tree would bury every real change
// in the diff.
import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**", "dist.stale-*/**"] },

  // Browser application code.
  {
    files: ["src/**/*.{js,jsx}"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        // Injected at build time by vite.config.js's `define` -- it is a
        // real global to this code, not an undeclared variable. See
        // public/sw.js for what it versions.
        __BUILD_ID__: "readonly",
      },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,

      // This project uses the automatic JSX runtime (@vitejs/plugin-react),
      // so React does not need to be in scope for JSX and prop-types are
      // not used anywhere.
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",

      // Escaping every apostrophe in user-facing copy hurts readability
      // for no correctness benefit -- JSX text is not HTML-parsed.
      "react/no-unescaped-entities": "off",

      // Off, deliberately, after looking at all 28 reports.
      //
      // This rule ships in react-hooks' recommended set and targets the
      // React Compiler's stricter model. Every hit here is the ordinary
      // "load data when the component mounts" pattern -- an effect that
      // calls a fetch function which sets state. Satisfying the rule
      // would mean restructuring data loading in nearly every page and
      // component in the app, with no bug fixed at the end of it: the
      // cascading render it warns about is one extra render on mount,
      // which is the accepted cost of this pattern and is why React's
      // own docs still describe it.
      //
      // rules-of-hooks and exhaustive-deps stay ON -- those catch real
      // defects, and exhaustive-deps in particular already caught a live
      // one in this codebase (the barcode scanner's camera tearing down
      // on every parent render, audit P0-4).
      "react-hooks/set-state-in-effect": "off",

      // A caught error that is deliberately ignored is a real pattern
      // here (crypto.randomUUID probing, localStorage in private
      // browsing). `catch {}` with no binding already expresses that;
      // this only allows an unused binding when it is named to say so.
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },

  // Service worker: different globals entirely, and not a module.
  {
    files: ["public/sw.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...globals.serviceworker, ...globals.browser },
    },
    rules: { ...js.configs.recommended.rules },
  },

  // Node-side config files. There used to be a container static server
  // and a redirect listener here too; both went when the app started
  // serving its own frontend (see backend/app/static_files.py).
  {
    files: ["vite.config.js", "eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: { ...js.configs.recommended.rules },
  },
];
