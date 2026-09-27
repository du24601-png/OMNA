export type ThemeMode = "light" | "dark"
export type ThemeAccent = "default" | "orange" | "blue" | "green" | "violet"
export type Theme = { mode: ThemeMode; accent: ThemeAccent }

const KEY = "zhiwo.theme"
const MODES = new Set<ThemeMode>(["light", "dark"])
const ACCENTS = new Set<ThemeAccent>(["default", "orange", "blue", "green", "violet"])

export function loadTheme(): Theme {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "")
    return {
      mode: MODES.has(raw.mode) ? raw.mode : "light",
      accent: ACCENTS.has(raw.accent) ? raw.accent : "default",
    }
  } catch {
    return { mode: "light", accent: "default" }
  }
}

let themeReady = false
export function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (themeReady && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.classList.add("theme-shift")
    window.setTimeout(() => root.classList.remove("theme-shift"), 400)
  }
  themeReady = true
  if (theme.mode === "dark") root.dataset.mode = "dark"
  else delete root.dataset.mode
  if (theme.accent === "default") delete root.dataset.accent
  else root.dataset.accent = theme.accent
}

export function saveTheme(theme: Theme) {
  localStorage.setItem(KEY, JSON.stringify(theme))
  applyTheme(theme)
}
