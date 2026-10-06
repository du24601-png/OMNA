"use strict"

const { contextBridge, ipcRenderer } = require("electron")

function subscribe(channel, listener, map) {
  const wrapped = (_event, value) => {
    const mapped = map(value)
    if (mapped !== null) listener(mapped)
  }
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

const ownerCredential = ipcRenderer.sendSync("omna:owner")
const locale = ipcRenderer.sendSync("omna:locale")
contextBridge.exposeInMainWorld("omna", Object.freeze({
  locale: locale === "zh-CN" ? "zh-CN" : "en",
  setLocale(next) {
    if (next !== "en" && next !== "zh-CN") return false
    return ipcRenderer.sendSync("omna:locale", next) === true
  },
  onLocaleChange(listener) {
    return subscribe("omna:locale-changed", listener, value => value === "zh-CN" ? "zh-CN" : value === "en" ? "en" : null)
  },
  ownerCredential: typeof ownerCredential === "string" ? ownerCredential : "",
  windowAction(action) {
    ipcRenderer.send("omna:window", action)
  },
  maximized() {
    return ipcRenderer.sendSync("omna:maximized") === true
  },
  onMaximized(listener) {
    return subscribe("omna:maximized", listener, value => value === true)
  },
  onFocusSearch(listener) {
    return subscribe("omna:focus-search", listener, () => undefined)
  },
  onNavigate(listener) {
    return subscribe("omna:navigate", listener, value => (typeof value === "string" ? value : ""))
  },
  onFlyoutShown(listener) {
    return subscribe("omna:flyout-shown", listener, value => (typeof value === "string" ? value : ""))
  },
  flyout: Object.freeze({
    hide() {
      ipcRenderer.send("omna:flyout", "hide")
    },
    openMain(route) {
      ipcRenderer.send("omna:flyout", "open-main", typeof route === "string" ? route : "")
    },
  }),
  prefs() {
    return ipcRenderer.sendSync("omna:prefs")
  },
  setPrefs(patch) {
    return ipcRenderer.invoke("omna:set-prefs", patch)
  },
}))
