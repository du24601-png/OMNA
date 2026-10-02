"use strict"

const { contextBridge, ipcRenderer } = require("electron")

function subscribe(channel, listener, map) {
  const wrapped = (_event, value) => listener(map(value))
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

const ownerCredential = ipcRenderer.sendSync("omna:owner")
contextBridge.exposeInMainWorld("omna", Object.freeze({
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
