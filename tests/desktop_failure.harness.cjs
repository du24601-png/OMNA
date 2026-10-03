"use strict"

// Executes the unchanged Electron entrypoint with platform/IO substitutes.
// This is a code regression test, not a native tray or client acceptance test.
const assert = require("node:assert/strict")
const { EventEmitter } = require("node:events")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const { test } = require("node:test")

const entry = path.join(__dirname, "../apps/desktop/src/main.cjs")

async function boot({ missingRuntime = false, route = null, notices = null } = {}) {
  const intervals = []
  const timeouts = []
  const windows = []
  const trays = []
  const child = new EventEmitter()
  child.pid = 1
  child.stdout = child.stderr = { pipe() {} }
  class Window extends EventEmitter {
    constructor(options) {
      super()
      this.options = options
      this.visible = false
      this.showCalls = this.focusCalls = 0
      this.urls = []
      this.webContents = new EventEmitter()
      this.webContents.send = () => {}
      windows.push(this)
    }
    removeMenu() {}
    isDestroyed() { return false }
    isMinimized() { return false }
    isVisible() { return this.visible }
    isFocused() { return false }
    show() { this.visible = true; this.showCalls++ }
    hide() { this.visible = false }
    focus() { this.focusCalls++ }
    loadURL(url) {
      this.urls.push(url)
      if (this.urls.length === 1) this.emit("ready-to-show")
      return Promise.resolve()
    }
  }
  class Tray extends EventEmitter {
    constructor() { super(); trays.push(this) }
    setImage() {}
    setToolTip() {}
    setContextMenu() {}
  }
  const app = Object.assign(new EventEmitter(), {
    isPackaged: false,
    commandLine: { getSwitchValue: name => name === "omna-port" ? "8785" : "" },
    requestSingleInstanceLock: () => true,
    whenReady: () => Promise.resolve(),
    getPath: () => path.join(__dirname, "synthetic-user-data"),
    getVersion: () => "test",
  })
  const electron = {
    app, BrowserWindow: Window, Tray,
    Menu: { buildFromTemplate: template => template },
    Notification: notices ? Object.assign(function Note(options) { return { on() {}, show() { notices.push(options) } } }, { isSupported: () => true }) : { isSupported: () => false },
    nativeImage: { createFromBitmap: () => ({}) },
    nativeTheme: Object.assign(new EventEmitter(), { shouldUseDarkColors: false }),
    powerMonitor: new EventEmitter(),
    ipcMain: Object.assign(new EventEmitter(), { handle() {} }),
    globalShortcut: { unregisterAll() {}, register: () => true },
    screen: { getPrimaryDisplay: () => ({ scaleFactor: 1 }) },
    session: { defaultSession: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} } },
    systemPreferences: { getAnimationSettings: () => ({ shouldRenderRichAnimation: true }) },
    shell: {},
  }
  const io = {
    existsSync: () => !missingRuntime,
    mkdirSync() {},
    statSync: () => ({ size: 0 }),
    createWriteStream: () => ({ write() {} }),
    readFileSync: file => {
      if (file.endsWith("owner.credential")) return "synthetic-owner".repeat(4)
      throw Object.assign(new Error("synthetic missing prefs"), { code: "ENOENT" })
    },
  }
  const http = {
    get(options, callback) {
      const request = new EventEmitter()
      request.destroy = () => {}
      queueMicrotask(() => {
        const routed = route && route(options.path)
        const response = Object.assign(new EventEmitter(), { statusCode: routed ? routed.status : 200, setEncoding() {} })
        callback(response)
        const body = routed ? routed.body : options.path === "/health" ? { status: "ok" }
          : options.path.startsWith("/api/v1/status") ? { service: { embeddings_loaded: true }, pending: { count: 0 }, recent_reads: [] }
            : {}
        response.emit("data", JSON.stringify(body))
        response.emit("end")
      })
      return request
    },
  }
  const net = {
    createServer() {
      const server = new EventEmitter()
      server.listen = () => queueMicrotask(() => server.emit("listening"))
      server.close = callback => callback()
      return server
    },
  }
  const substitutes = { electron, "node:fs": io, "node:http": http, "node:net": net, "node:child_process": { spawn: () => child } }
  vm.runInNewContext(fs.readFileSync(entry, "utf8"), {
    require: name => substitutes[name] ?? require(name.startsWith(".") ? path.join(path.dirname(entry), name) : name),
    __dirname: path.dirname(entry),
    process: { argv: [], env: {}, platform: "win32" },
    Buffer, console,
    setInterval: callback => { intervals.push(callback); return { callback } }, clearInterval() {},
    setTimeout: callback => { timeouts.push(callback); return { callback } }, clearTimeout() {},
  }, { filename: entry })
  for (let attempt = 0; attempt < 30; attempt++) {
    await new Promise(resolve => setImmediate(resolve))
    const latest = windows[0]?.urls.at(-1) ?? ""
    if (missingRuntime ? decodeURIComponent(latest).includes("安装不完整") : latest === "http://127.0.0.1:8785/") {
      return { win: windows[0], tray: trays[0], child, intervals, timeouts }
    }
  }
  throw new Error("Synthetic desktop boot did not settle")
}

test("service exit keeps a hidden main window hidden until tray click", async () => {
  const { win, tray, child } = await boot()
  win.hide()
  win.showCalls = win.focusCalls = 0
  child.emit("exit", 1)
  assert.match(decodeURIComponent(win.urls.at(-1)), /本地服务停止了/)
  assert.equal(win.visible, false, "background service failure must not reveal the window")
  assert.equal(win.focusCalls, 0)
  tray.emit("click")
  assert.equal(win.visible, true)
  assert.equal(win.focusCalls, 1)
  assert.match(decodeURIComponent(win.urls.at(-1)), /日志：/)
})

test("service exit updates an open window without taking keyboard focus", async () => {
  const { win, child } = await boot()
  win.showCalls = win.focusCalls = 0
  child.emit("exit", 1)
  assert.equal(win.visible, true)
  assert.match(decodeURIComponent(win.urls.at(-1)), /本地服务停止了/)
  assert.equal(win.focusCalls, 0, "background failure must not steal focus")
})

test("startup failure still opens an actionable error window", async () => {
  const { win } = await boot({ missingRuntime: true })
  assert.equal(win.visible, true)
  assert.equal(win.focusCalls, 1)
  assert.match(decodeURIComponent(win.urls.at(-1)), /安装不完整/)
})

test("a failed suggestion fetch is tried again instead of dropping the notification", async () => {
  const notices = []
  let latest = "2099-01-01T00:00:00+00:00"
  let failing = true
  const route = pathname => {
    if (pathname.startsWith("/api/v1/status")) return { status: 200, body: { service: { embeddings_loaded: true }, pending: { count: 1, latest_at: latest }, recent_reads: [] } }
    if (pathname.startsWith("/api/v1/proposals")) return failing ? { status: 500, body: {} }
      : { status: 200, body: { proposals: [{ id: "p1", origin: "agent", requester: { name: "Claude Code" }, payload: { content: "测试框架用 Vitest" }, evidence: {} }] } }
    return null
  }
  const { intervals, timeouts } = await boot({ route, notices })
  const poll = intervals.find(callback => callback.name === "pollStatus")
  assert.ok(poll, "status polling started")
  await poll()
  latest = "2099-01-01T00:00:05+00:00"
  await poll()
  assert.equal(notices.length, 0)
  failing = false
  await poll()
  for (const callback of timeouts.splice(0)) callback()
  assert.equal(notices.length, 1, "the suggestion from the failed round is still announced")
  assert.match(notices[0].title, /Claude Code/)
})

for (const flushBetweenPolls of [false, true]) {
  test(flushBetweenPolls ? "a proposal newer than the status snapshot is not announced twice" : "a status/list race counts two unique proposals as two", async () => {
    const notices = []
    let latest = "2099-01-01T00:00:00+00:00"
    let items = []
    const route = pathname => {
      if (pathname.startsWith("/api/v1/status")) return { status: 200, body: { service: { embeddings_loaded: true }, pending: { count: 2, latest_at: latest }, recent_reads: [] } }
      if (pathname.startsWith("/api/v1/proposals")) return { status: 200, body: { proposals: items } }
      return null
    }
    const { win, intervals, timeouts } = await boot({ route, notices })
    win.hide()
    await new Promise(resolve => setImmediate(resolve))
    const poll = intervals.find(callback => callback.name === "pollStatus")
    const proposal = (id, at) => ({ id, created_at: at, origin: "agent", requester: { name: "Synthetic Agent" }, payload: { content: "合成建议 " + id } })
    latest = "2099-01-01T00:00:05+00:00"
    items = [proposal("p1", latest), proposal("p2", "2099-01-01T00:00:06+00:00")]
    await poll()
    if (flushBetweenPolls) for (const callback of timeouts.splice(0)) callback()
    latest = "2099-01-01T00:00:06+00:00"
    items = [items[1]]
    await poll()
    for (const callback of timeouts.splice(0)) callback()
    assert.equal(notices.length, 1, "each unique suggestion is announced once")
    assert.equal(notices[0].title, "2 条新建议等你确认")
  })
}
