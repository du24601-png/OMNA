import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { App } from "./App"
import { Flyout } from "./Flyout"
import { applyTheme, loadTheme } from "./theme"
import { applyLocale } from "./i18n"
import "./styles.css"

const flyout = location.hash.startsWith("#/flyout")
if (window.omna) document.documentElement.classList.add("omna-desktop")
if (flyout) document.documentElement.classList.add("omna-flyout")
if (flyout && location.hash.includes("material=1")) document.documentElement.classList.add("flyout-material")
applyTheme(loadTheme())
applyLocale()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {flyout ? <Flyout /> : <App />}
  </StrictMode>,
)
