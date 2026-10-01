export type ThemeMode = "light" | "dark" | "system"
export type Theme = { mode: ThemeMode }

const KEY = "zhiwo.theme"
const MODES = new Set<ThemeMode>(["light", "dark", "system"])

export function loadTheme(): Theme {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "")
    return { mode: MODES.has(raw.mode) ? raw.mode : "light" }
  } catch {
    return { mode: "light" }
  }
}

function resolvedMode(mode: ThemeMode): "light" | "dark" {
  if (mode !== "system") return mode
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

let themeReady = false
let stopSystem: (() => void) | null = null

export function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (themeReady && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.classList.add("theme-shift")
    window.setTimeout(() => root.classList.remove("theme-shift"), 400)
  }
  themeReady = true
  if (resolvedMode(theme.mode) === "dark") root.dataset.mode = "dark"
  else delete root.dataset.mode
  delete root.dataset.accent
  stopSystem?.()
  stopSystem = null
  if (theme.mode === "system") {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => applyTheme(theme)
    media.addEventListener("change", onChange)
    stopSystem = () => media.removeEventListener("change", onChange)
  }
}

export function saveTheme(theme: Theme) {
  localStorage.setItem(KEY, JSON.stringify(theme))
  applyTheme(theme)
}
