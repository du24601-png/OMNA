"use strict"

const { contextBridge, ipcRenderer } = require("electron")

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
    const wrapped = (_event, value) => listener(value === true)
    ipcRenderer.on("omna:maximized", wrapped)
    return () => ipcRenderer.removeListener("omna:maximized", wrapped)
  },
}))
