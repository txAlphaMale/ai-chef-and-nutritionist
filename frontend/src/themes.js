// Multi-theme support -- the color values themselves live in
// styles/theme.css as `:root[data-theme="<key>"]` blocks; this module is
// just the JS-side registry (for the Settings-page picker's swatch
// previews, which -- same limitation noted in Fiduciary's own picker --
// can't read a non-active theme's CSS vars off the live cascade, so the
// preview colors are necessarily duplicated here) plus the small
// apply/cache/persist mechanics.
//
// "default" (Chef's own warm terracotta/sage palette) needs no
// data-theme attribute at all -- it's what :root already defines,
// mirroring how Fiduciary treats its own "amber" default. Every other
// key sets `data-theme` to that exact string, which must match a real
// `:root[data-theme="..."]` block in theme.css.
//
// Two theme families were ported in on 2026-08-01 at the author's
// request: Fiduciary's four terminal-dashboard themes (amber/cobalt/
// highcontrast/daylight, copied verbatim from
// portfolio-api/static/index.html's :root[data-theme] blocks) and all
// four Catppuccin flavors (colors from https://catppuccin.com/palette).
// Catppuccin's own token names (base/mantle/surface0-2/text/subtext0/
// mauve/green/red/peach/lavender) don't map 1:1 onto Chef's semantic
// vars, so a deliberate mapping was chosen and applied identically
// across all four flavors: bg=Base, surface=Surface0 (Mantle for Latte,
// since Latte's ramp has nothing brighter than Base to use as an
// "elevated card" tone), border=Surface1, text=Text, text-muted=
// Subtext0, primary=Mauve (Catppuccin's own iconic brand hue), primary-
// hover=Lavender, secondary=Green, danger=Red, accent=Peach.

export const STORAGE_KEY = "chefTheme";
export const DEFAULT_THEME = "default";

export const THEME_OPTIONS = [
  {
    key: "default",
    label: "Chef (default)",
    group: "Chef",
    swatches: { bg: "#faf7f2", surface: "#ffffff", primary: "#c1440e", secondary: "#4a7c59", danger: "#b3261e" },
  },
  {
    key: "amber",
    label: "Amber terminal",
    group: "Terminal (ported from Fiduciary)",
    swatches: { bg: "#0a0b0d", surface: "#121417", primary: "#f0a823", secondary: "#2ec27e", danger: "#ff5d5d" },
  },
  {
    key: "cobalt",
    label: "Cobalt",
    group: "Terminal (ported from Fiduciary)",
    swatches: { bg: "#080b12", surface: "#0d1420", primary: "#4d9bff", secondary: "#2ec27e", danger: "#ff5d5d" },
  },
  {
    key: "highcontrast",
    label: "High contrast",
    group: "Terminal (ported from Fiduciary)",
    swatches: { bg: "#050505", surface: "#0d0d0d", primary: "#ffb020", secondary: "#3ddb8f", danger: "#ff6b6b" },
  },
  {
    key: "daylight",
    label: "Daylight",
    group: "Terminal (ported from Fiduciary)",
    swatches: { bg: "#ece7dc", surface: "#ffffff", primary: "#a0530a", secondary: "#0f7a38", danger: "#c81e1e" },
  },
  {
    key: "catppuccin-latte",
    label: "Catppuccin Latte",
    group: "Catppuccin",
    swatches: { bg: "#eff1f5", surface: "#e6e9ef", primary: "#8839ef", secondary: "#40a02b", danger: "#d20f39" },
  },
  {
    key: "catppuccin-frappe",
    label: "Catppuccin Frappé",
    group: "Catppuccin",
    swatches: { bg: "#303446", surface: "#414559", primary: "#ca9ee6", secondary: "#a6d189", danger: "#e78284" },
  },
  {
    key: "catppuccin-macchiato",
    label: "Catppuccin Macchiato",
    group: "Catppuccin",
    swatches: { bg: "#24273a", surface: "#363a4f", primary: "#c6a0f6", secondary: "#a6da95", danger: "#ed8796" },
  },
  {
    key: "catppuccin-mocha",
    label: "Catppuccin Mocha",
    group: "Catppuccin",
    swatches: { bg: "#1e1e2e", surface: "#313244", primary: "#cba6f7", secondary: "#a6e3a1", danger: "#f38ba8" },
  },
];

const VALID_KEYS = new Set(THEME_OPTIONS.map((t) => t.key));

/** Sets/removes the data-theme attribute and mirrors the choice into
 * localStorage, which acts as an instant-apply local cache -- the DB
 * setting (ui_theme, via the existing Settings API) is the real source
 * of truth and survives container rebuilds, but reading it takes a
 * network round trip, so the last-applied value is cached here too and
 * re-applied synchronously (see index.html's inline pre-hydration
 * script) to avoid a flash of the wrong theme on every page load. */
export function applyTheme(key) {
  const safeKey = VALID_KEYS.has(key) ? key : DEFAULT_THEME;
  if (safeKey === DEFAULT_THEME) {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", safeKey);
  }
  try {
    localStorage.setItem(STORAGE_KEY, safeKey);
  } catch {
    // ignore -- localStorage can throw in some privacy modes; the DB
    // setting is still the real source of truth, this is only a cache
  }
  return safeKey;
}

export function getCachedTheme() {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}
