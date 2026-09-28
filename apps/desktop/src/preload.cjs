"use strict"

const { contextBridge, ipcRenderer } = require("electron")

const ownerCredential = ipcRenderer.sendSync("omna:owner")
if (typeof ownerCredential === "string" && ownerCredential) {
  contextBridge.exposeInMainWorld("omna", Object.freeze({ ownerCredential }))
}
