"use strict"

const { app, BrowserWindow, Menu, Notification, Tray, globalShortcut, ipcMain, nativeImage, nativeTheme, powerMonitor, screen, session, shell, systemPreferences } = require("electron")
const { execFile, spawn } = require("node:child_process")
const crypto = require("node:crypto")
const fs = require("node:fs")
const http = require("node:http")
const net = require("node:net")
const os = require("node:os")
const path = require("node:path")
const trayIcons = require("./tray-icons.cjs")

// A development run may use another port so it does not collide with an installed OMNA.
const DEV_PORT = app.isPackaged ? 0 : Number(app.commandLine.getSwitchValue("omna-port")) || 0
const PORT = DEV_PORT || 8765
const APP_ID = "app.omna.desktop"
const POLL_MS = 4000
const NOTE_GAP_MS = 30000
const DEFAULT_SHORTCUT = "CommandOrControl+Shift+M"
const CATEGORY_NAMES = { identity: "身份", goal: "目标", preference: "偏好", project: "项目", event: "事件", other: "其他" }
const ORIGIN = `http://127.0.0.1:${PORT}`
const START_TIMEOUT_MS = 180000
const LOG_LIMIT = 5 * 1024 * 1024

// Only these host variables reach the service. Keys, proxies and MNEMOSYNE_* stay out.
const KEEP_ENV = new Set([
  "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
  "HOMEDRIVE", "HOMEPATH", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "PROGRAMDATA", "PROGRAMFILES",
  "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
  "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATHEXT", "COMSPEC", "OS",
])

class Failure extends Error {
  constructor(title, detail) {
    super(detail)
    this.title = title
    this.detail = detail
  }
}

const customData = app.commandLine.getSwitchValue("omna-user-data")
if (customData) app.setPath("userData", path.resolve(customData))

if (!app.requestSingleInstanceLock()) {
  app.exit(0)
} else if (process.argv.includes("--quit")) {
  app.exit(0)
} else {
  main()
}

function main() {
  const runtime = app.isPackaged ? process.resourcesPath : path.join(__dirname, "..", "runtime")
  const paths = {
    python: path.join(runtime, "python", "python.exe"),
    web: path.join(runtime, "web"),
    model: path.join(runtime, "model"),
    icon: path.join(runtime, "icon.png"),
  }
  const userData = app.getPath("userData")
  const dataDir = path.join(userData, "data")
  const logDir = path.join(userData, "logs")
  const logFile = path.join(logDir, "service.log")
  const system = path.join(process.env.SystemRoot || "C:\\Windows", "System32")

  let win = null
  let tray = null
  let child = null
  let stopping = null
  let quitting = false
  let ready = false
  let hintShown = false
  let credential = ""
  const prefsFile = path.join(userData, "desktop.json")
  let prefs = loadPrefs()
  let flyout = null
  let flyoutHiddenAt = 0
  let shortcutOk = false
  let trayState = ""
  let trayTip = ""
  let readingTimer = null
  let lastReadId = null
  let pendingMark = null
  let pendingBusy = false
  let pollTimer = null
  let polling = false
  let locked = false
  const noteQueue = []
  let noteTimer = null
  let lastNoteAt = 0

  app.on("second-instance", (_event, argv) => {
    if (argv.includes("--quit")) quit()
    else show()
  })
  app.on("window-all-closed", () => {})
  app.on("before-quit", () => { quitting = true })
  app.on("will-quit", event => {
    globalShortcut.unregisterAll()
    if (!child) return
    event.preventDefault()
    stopService().finally(() => {
      child = null
      app.quit()
    })
  })
  ipcMain.on("omna:owner", event => {
    const url = event.senderFrame ? event.senderFrame.url : ""
    event.returnValue = ready && url.startsWith(`${ORIGIN}/`) ? credential : ""
  })
  ipcMain.on("omna:window", (event, action) => {
    if (!win || win.isDestroyed() || event.sender !== win.webContents) return
    if (action === "minimize") win.minimize()
    else if (action === "maximize") {
      if (win.isMaximized()) win.unmaximize()
      else win.maximize()
    } else if (action === "close") win.close()
  })
  ipcMain.on("omna:maximized", event => {
    event.returnValue = Boolean(win && !win.isDestroyed() && win.isMaximized())
  })
  ipcMain.on("omna:flyout", (event, action, route) => {
    if (!flyout || flyout.isDestroyed() || event.sender !== flyout.webContents) return
    if (action === "hide") hideFlyout()
    else if (action === "open-main") {
      hideFlyout()
      openMain(typeof route === "string" ? route : "")
    }
  })
  ipcMain.on("omna:prefs", event => {
    event.returnValue = fromOurPage(event) ? publicPrefs() : null
  })
  ipcMain.handle("omna:set-prefs", (event, patch) => {
    if (!fromOurPage(event) || !win || event.sender !== win.webContents) return null
    if (patch && typeof patch.notifications === "boolean") prefs.notifications = patch.notifications
    if (patch && typeof patch.shortcut === "string" && patch.shortcut.length <= 64) prefs.shortcut = patch.shortcut.trim() || DEFAULT_SHORTCUT
    savePrefs()
    registerShortcut()
    return publicPrefs()
  })
  app.whenReady().then(start)

  async function start() {
    lockDown()
    if (app.isPackaged) app.setAppUserModelId(APP_ID)
    createTray()
    createWindow()
    powerMonitor.on("lock-screen", () => { locked = true })
    powerMonitor.on("unlock-screen", () => { locked = false; pollStatus() })
    nativeTheme.on("updated", () => paintTray(trayState || "idle", trayTip, true))
    startPolling()
    showStatus("正在启动 OMNA…", "第一次打开要载入本地模型，可能需要半分钟。")
    try {
      fs.mkdirSync(dataDir, { recursive: true })
      fs.mkdirSync(logDir, { recursive: true })
      for (const required of [paths.python, path.join(paths.web, "index.html"), paths.model]) {
        if (!fs.existsSync(required)) throw new Failure("安装不完整", `缺少 ${required}。请重新安装 OMNA。`)
      }
      credential = ownerCredential()
      await claimPort()
      await startService()
      ready = true
      registerShortcut()
      createFlyout()
      await win.loadURL(`${ORIGIN}/`)
      pollStatus()
    } catch (error) {
      showFailure(error)
    }
  }

  function ownerCredential() {
    const file = path.join(userData, "owner.credential")
    try {
      const saved = fs.readFileSync(file, "utf8").trim()
      if (saved.length >= 32) return saved
    } catch (error) {
      if (error.code !== "ENOENT") throw error
    }
    const value = crypto.randomBytes(32).toString("base64url")
    fs.writeFileSync(file, `${value}\n`, { encoding: "utf8", mode: 0o600 })
    return value
  }

  async function claimPort() {
    if (await portFree()) return
    // A service that accepts this data directory's credential is ours, left over from a crash.
    if ((await ownerHealth()) !== 200) {
      throw new Failure(`端口 ${PORT} 被占用`, `另一个程序正在使用 127.0.0.1:${PORT}，OMNA 没有连到它。关闭那个程序后，再从开始菜单打开 OMNA。`)
    }
    const pid = await listenerPid()
    if (pid) await killTree(pid)
    for (let i = 0; i < 20; i += 1) {
      if (await portFree()) return
      await sleep(500)
    }
    throw new Failure(`端口 ${PORT} 没有释放`, "上一次的本地服务还没退出。稍等片刻，再重新打开 OMNA。")
  }

  function startService() {
    return new Promise((resolve, reject) => {
      const log = openLog()
      const args = ["-I", "-X", "utf8", "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", String(PORT), "--no-access-log"]
      let settled = false
      const fail = error => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (child) killTree(child.pid)
        reject(error)
      }
      const timer = setTimeout(() => fail(new Failure("本地服务没有按时启动", `等了 ${START_TIMEOUT_MS / 1000} 秒还没有就绪。`)), START_TIMEOUT_MS)
      child = spawn(paths.python, args, { cwd: dataDir, env: serviceEnv(), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
      child.stdout.pipe(log, { end: false })
      child.stderr.pipe(log, { end: false })
      child.once("error", error => fail(new Failure("本地服务无法启动", error.message)))
      child.once("exit", code => {
        child = null
        log.write(`[${new Date().toISOString()}] service exited code=${code}\n`)
        if (!settled) fail(new Failure("本地服务启动后退出了", `退出码 ${code}。`))
        else if (!quitting) {
          ready = false
          showFailure(new Failure("本地服务停止了", "已连接的 Agent 暂时读不到记忆。可以从托盘菜单重新启动 OMNA。"), false)
        }
      })
      const poll = async () => {
        while (!settled) {
          if (await publicHealth()) {
            const status = await ownerHealth()
            if (settled) return
            if (status === 200) {
              settled = true
              clearTimeout(timer)
              resolve()
            } else {
              fail(new Failure("本地服务没有认出这个窗口", `校验返回 ${status}。`))
            }
            return
          }
          await sleep(500)
        }
      }
      poll()
    })
  }

  function stopService() {
    if (!child) return Promise.resolve()
    if (stopping) return stopping
    const current = child
    stopping = new Promise(resolve => {
      current.once("exit", resolve)
      killTree(current.pid).then(() => setTimeout(resolve, 5000))
    })
    return stopping
  }

  function serviceEnv() {
    const env = {}
    for (const [key, value] of Object.entries(process.env)) {
      if (value && KEEP_ENV.has(key.toUpperCase())) env[key] = value
    }
    const root = process.env.SystemRoot || "C:\\Windows"
    env.PATH = [system, root, path.join(system, "Wbem"), path.join(system, "WindowsPowerShell", "v1.0")].join(";")
    Object.assign(env, {
      ZHIWO_DATA_DIR: dataDir,
      ZHIWO_OWNER_CREDENTIAL: credential,
      ZHIWO_HOST: "127.0.0.1",
      ZHIWO_FASTEMBED_CACHE_DIR: paths.model,
      ZHIWO_BRIDGE_PYTHON: paths.python,
      ZHIWO_WEB_DIST: paths.web,
      HF_HUB_OFFLINE: "1",
      HF_HUB_DISABLE_TELEMETRY: "1",
    })
    return env
  }

  function openLog() {
    try {
      if (fs.statSync(logFile).size > LOG_LIMIT) fs.renameSync(logFile, path.join(logDir, "service.1.log"))
    } catch (error) {
      if (error.code !== "ENOENT") throw error
    }
    const stream = fs.createWriteStream(logFile, { flags: "a" })
    stream.write(`[${new Date().toISOString()}] OMNA ${app.getVersion()} starting service\n`)
    return stream
  }

  function lockDown() {
    const allowed = permission => permission === "clipboard-sanitized-write"
    session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) => callback(allowed(permission)))
    session.defaultSession.setPermissionCheckHandler((_contents, permission) => allowed(permission))
    app.on("web-contents-created", (_event, contents) => {
      contents.setWindowOpenHandler(() => ({ action: "deny" }))
      contents.on("will-navigate", (event, url) => {
        if (!url.startsWith(`${ORIGIN}/`)) event.preventDefault()
      })
      contents.on("will-attach-webview", event => event.preventDefault())
    })
  }

  function createWindow() {
    win = new BrowserWindow({
      width: 1040,
      height: 680,
      minWidth: 960,
      minHeight: 600,
      show: false,
      title: "OMNA",
      icon: paths.icon,
      backgroundColor: "#f3f3f3",
      autoHideMenuBar: true,
      frame: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        devTools: !app.isPackaged,
      },
    })
    win.removeMenu()
    const tellMaximized = () => {
      if (win && !win.isDestroyed()) win.webContents.send("omna:maximized", win.isMaximized())
    }
    win.on("maximize", tellMaximized)
    win.on("unmaximize", tellMaximized)
    win.once("ready-to-show", () => win.show())
    win.on("page-title-updated", event => event.preventDefault())
    win.on("close", event => {
      if (quitting) return
      event.preventDefault()
      win.hide()
      if (!hintShown && tray) {
        hintShown = true
        tray.displayBalloon({ iconType: "info", title: "OMNA 还在运行", content: "已连接的 Agent 仍能读取记忆。要停止，请在托盘图标的菜单里选择退出。" })
      }
    })
  }

  function createTray() {
    tray = new Tray(trayImage("idle", 1))
    paintTray("idle", "OMNA 正在启动")
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "打开 OMNA", click: show },
      { label: "打开日志文件夹", click: () => shell.openPath(logDir) },
      { label: "重新启动 OMNA", click: () => { app.relaunch(); quit() } },
      { type: "separator" },
      { label: "退出 OMNA", click: quit },
    ]))
    tray.on("click", () => {
      if (!ready || trayState === "problem") return show()
      toggleFlyout()
    })
  }

  // ── Tray flyout ────────────────────────────────────────────────────────────

  function createFlyout() {
    if (flyout && !flyout.isDestroyed()) return
    const material = windows11()
    const dark = systemDark()
    flyout = new BrowserWindow({
      width: 368,
      height: 520,
      show: false,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      title: "OMNA",
      backgroundColor: material ? "#00000000" : dark ? "#242424" : "#f9f9f9",
      ...(material ? { backgroundMaterial: "acrylic" } : {}),
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        devTools: !app.isPackaged,
      },
    })
    flyout.removeMenu()
    flyout.on("blur", () => {
      if (!flyout.webContents.isDevToolsOpened()) hideFlyout()
    })
    flyout.on("close", event => {
      if (quitting) return
      event.preventDefault()
      hideFlyout()
    })
    flyout.on("page-title-updated", event => event.preventDefault())
    flyout.loadURL(`${ORIGIN}/#/flyout${material ? "?material=1" : ""}`)
  }

  function toggleFlyout(proposalId) {
    if (!flyout || flyout.isDestroyed()) return show()
    if (flyout.isVisible() && !proposalId) return hideFlyout()
    // A click on the tray first blurs the flyout; do not reopen it in the same click.
    if (!proposalId && Date.now() - flyoutHiddenAt < 250) return
    placeFlyout()
    flyout.show()
    flyout.focus()
    flyout.webContents.send("omna:flyout-shown", proposalId || "")
  }

  function hideFlyout() {
    if (!flyout || flyout.isDestroyed() || !flyout.isVisible()) return
    flyoutHiddenAt = Date.now()
    flyout.hide()
  }

  function placeFlyout() {
    const bounds = tray.getBounds()
    const area = screen.getDisplayMatching(bounds).workArea
    const [width, height] = flyout.getSize()
    const gap = 12
    let x = Math.round(bounds.x + bounds.width / 2 - width / 2)
    let y = area.y + area.height - height - gap
    if (bounds.y + bounds.height <= area.y + 1) {
      y = area.y + gap
    } else if (bounds.y < area.y + area.height - 1) {
      // Taskbar on the left or right edge.
      y = Math.round(bounds.y + bounds.height / 2 - height / 2)
      x = bounds.x >= area.x + area.width - 1 ? area.x + area.width - width - gap : area.x + gap
    }
    x = Math.min(Math.max(x, area.x + gap), area.x + area.width - width - gap)
    y = Math.min(Math.max(y, area.y + gap), area.y + area.height - height - gap)
    flyout.setPosition(x, y)
  }

  function openMain(route) {
    show()
    if (route && win && !win.isDestroyed()) win.webContents.send("omna:navigate", route)
  }

  // ── Tray state from the status endpoint ────────────────────────────────────

  function startPolling() {
    clearInterval(pollTimer)
    pollTimer = setInterval(pollStatus, POLL_MS)
  }

  async function pollStatus() {
    if (polling || locked) return
    polling = true
    try {
      if (!ready) {
        if (!credential || !win || child) return
        return paintTray("problem", "本机服务没在运行 · 点击查看原因")
      }
      const offset = new Date().getTimezoneOffset()
      const { status, body } = await request(`/api/v1/status?utc_offset_minutes=${offset}`, { Authorization: `Bearer ${credential}` })
      if (status !== 200) return paintTray("problem", "本机服务没在运行 · 点击查看原因")
      let data
      try {
        data = JSON.parse(body)
      } catch {
        return paintTray("problem", "本机服务回应异常 · 点击查看原因")
      }
      const latest = data.recent_reads && data.recent_reads[0]
      if (latest && lastReadId !== null && latest.event_id !== lastReadId) startReading(latest, data)
      lastReadId = latest ? latest.event_id : ""
      await notePending(data.pending)
      if (!readingTimer) paintTray(...baseState(data))
    } finally {
      polling = false
    }
  }

  function baseState(data) {
    if (!data.service || data.service.embeddings_loaded === false) return ["problem", "本地向量模型没有加载 · 点击查看原因"]
    if (data.sharing && data.sharing.paused) return ["paused", `已暂停共享 · ${minutesLeft(data.sharing.paused_until)} 分钟后恢复`]
    if (data.pending && data.pending.count > 0) return ["pending", `${data.pending.count} 条建议等你确认`]
    return ["idle", "OMNA 运行中"]
  }

  function startReading(read, data) {
    const parts = Object.entries(read.categories || {}).map(([key, count]) => `${CATEGORY_NAMES[key] || key} ${count}`)
    const tip = `${read.agent_name} 正在读取${parts.length ? ` · ${parts.join(" · ")}` : ""}`
    const animate = systemPreferences.getAnimationSettings().shouldRenderRichAnimation
    const started = Date.now()
    clearInterval(readingTimer)
    const frame = () => {
      const elapsed = Date.now() - started
      if (elapsed >= 2000) {
        clearInterval(readingTimer)
        readingTimer = null
        return paintTray(...baseState(data))
      }
      const phase = animate ? 0.5 + 0.5 * Math.cos((elapsed / 1600) * Math.PI * 2) : 1
      paintTray("reading", tip, false, Math.round(phase * 4) / 4)
    }
    readingTimer = setInterval(frame, 100)
    frame()
  }

  const iconCache = new Map()

  function trayImage(state, phase) {
    const scale = screen.getPrimaryDisplay().scaleFactor || 1
    const size = Math.round(16 * scale)
    const theme = systemDark() ? "dark" : "light"
    const key = `${state}:${theme}:${size}:${phase}`
    if (!iconCache.has(key)) {
      const bitmap = trayIcons.render(state, theme, size, phase)
      iconCache.set(key, nativeImage.createFromBitmap(bitmap, { width: size, height: size, scaleFactor: scale }))
    }
    return iconCache.get(key)
  }

  function paintTray(state, tip, force = false, phase = 1) {
    if (!tray) return
    if (force) iconCache.clear()
    if (force || state !== trayState || state === "reading") tray.setImage(trayImage(state, phase))
    if (tip !== trayTip) tray.setToolTip(tip)
    trayState = state
    trayTip = tip
  }

  // ── Notifications for new suggestions ──────────────────────────────────────

  async function notePending(pending) {
    const latestAt = pending && pending.latest_at
    if (pendingMark === null) {
      pendingMark = latestAt || new Date().toISOString()
      return
    }
    if (!latestAt || latestAt <= pendingMark || pendingBusy) return
    pendingBusy = true
    try {
      const since = pendingMark
      const { status, body } = await request(`/api/v1/proposals?status=pending&since=${encodeURIComponent(since)}`, { Authorization: `Bearer ${credential}` })
      if (status !== 200) return
      let items = []
      try {
        items = JSON.parse(body).proposals || []
      } catch {
        return
      }
      // Only a list that arrived and parsed moves the cursor; a failed fetch
      // is tried again on the next poll instead of dropping that round.
      pendingMark = latestAt
      const fromAgents = items.filter(item => item.origin === "agent")
      if (!fromAgents.length) return
      noteQueue.push(...fromAgents)
      if (!noteTimer) noteTimer = setTimeout(flushNotes, Math.max(0, lastNoteAt + NOTE_GAP_MS - Date.now()))
    } finally {
      pendingBusy = false
    }
  }

  function flushNotes() {
    noteTimer = null
    const items = noteQueue.splice(0)
    if (!items.length || !prefs.notifications || !Notification.isSupported()) return
    const looking = (flyout && !flyout.isDestroyed() && flyout.isVisible()) || (win && !win.isDestroyed() && win.isFocused())
    if (looking) return
    lastNoteAt = Date.now()
    const first = items[0]
    const who = first.requester && first.requester.name ? first.requester.name : "Agent"
    const verb = first.target_id ? "修改" : "新增"
    const title = items.length === 1 ? `${who} 提议${verb}一条记忆` : `${items.length} 条新建议等你确认`
    const lines = items.slice(0, 2).map(item => `${item.target_id ? "修改" : "新增"}：${clip(item.payload && item.payload.content)}`)
    if (items.length === 1 && first.evidence && first.evidence.text) lines.push(`依据：${clip(first.evidence.text)}`)
    if (items.length > 2) lines.push(`还有 ${items.length - 2} 条`)
    const note = new Notification({ title, body: lines.join("\n") })
    note.on("click", () => toggleFlyout(first.id))
    note.show()
  }

  // ── Global shortcut and desktop-only preferences ───────────────────────────

  function registerShortcut() {
    globalShortcut.unregisterAll()
    shortcutOk = false
    if (!prefs.shortcut) return
    try {
      shortcutOk = globalShortcut.register(prefs.shortcut, () => {
        show()
        if (win && !win.isDestroyed()) win.webContents.send("omna:focus-search")
      })
    } catch {
      shortcutOk = false
    }
  }

  function loadPrefs() {
    try {
      const saved = JSON.parse(fs.readFileSync(prefsFile, "utf8"))
      return {
        notifications: typeof saved.notifications === "boolean" ? saved.notifications : true,
        shortcut: typeof saved.shortcut === "string" && saved.shortcut ? saved.shortcut : DEFAULT_SHORTCUT,
      }
    } catch {
      return { notifications: true, shortcut: DEFAULT_SHORTCUT }
    }
  }

  function savePrefs() {
    fs.writeFileSync(prefsFile, JSON.stringify(prefs), "utf8")
  }

  function publicPrefs() {
    return { notifications: prefs.notifications, shortcut: prefs.shortcut, shortcutRegistered: shortcutOk, notificationsSupported: Notification.isSupported() }
  }

  function fromOurPage(event) {
    const url = event.senderFrame ? event.senderFrame.url : ""
    return ready && url.startsWith(`${ORIGIN}/`)
  }

  function show() {
    if (!win) return
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
  }

  function quit() {
    quitting = true
    app.quit()
  }

  function showStatus(title, detail) {
    return win.loadURL(page(title, detail, false))
  }

  function showFailure(error, reveal = true) {
    const title = error instanceof Failure ? error.title : "OMNA 没能打开"
    const detail = error instanceof Failure ? error.detail : String(error && error.message ? error.message : error)
    if (win) {
      win.loadURL(page(title, `${detail}\n日志：${logFile}`, true))
      // Startup failures need attention; background failures wait for a tray click.
      if (reveal) show()
    }
  }

  function page(title, detail, failed) {
    const escape = text => String(text).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch])
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>OMNA</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>
body{margin:0;height:100vh;display:grid;place-items:center;font:15px/1.7 "Segoe UI","Microsoft YaHei UI",sans-serif;background:#f6f6f7;color:#1d1d1f}
.chrome{position:fixed;top:0;right:0;display:flex}
.chrome button{width:46px;height:40px;border:0;border-radius:0;background:transparent;color:#1d1d1f;font:16px/1 "Segoe UI Symbol",sans-serif}
.chrome button:hover{background:#ececee}
.chrome button.close:hover{background:#e81123;color:#fff}
main{max-width:520px;padding:32px}
h1{font-size:22px;font-weight:600;margin:0 0 8px}
p{margin:0;color:#6e6e73;white-space:pre-wrap;word-break:break-all}
.bar{height:3px;border-radius:3px;background:#e5e5ea;overflow:hidden;margin-top:24px}
.bar i{display:block;width:30%;height:100%;background:#1d1d1f;animation:m 1.4s ease-in-out infinite}
@keyframes m{0%{transform:translateX(-100%)}100%{transform:translateX(340%)}}
@media (prefers-reduced-motion:reduce){.bar i{animation:none;width:100%}}
</style></head><body><div class="chrome"><button type="button" aria-label="最小化" onclick="omna.windowAction('minimize')">&#8211;</button><button type="button" aria-label="最大化" onclick="omna.windowAction('maximize')">&#9633;</button><button type="button" class="close" aria-label="关闭" onclick="omna.windowAction('close')">&#10005;</button></div><main role="${failed ? "alert" : "status"}"><h1>${escape(title)}</h1><p>${escape(detail)}</p>${failed ? "" : '<div class="bar"><i></i></div>'}</main></body></html>`
    return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
  }

  function request(pathname, headers) {
    return new Promise(resolve => {
      const req = http.get({ host: "127.0.0.1", port: PORT, path: pathname, headers, timeout: 5000 }, res => {
        let body = ""
        res.setEncoding("utf8")
        res.on("data", chunk => { body += chunk })
        res.on("end", () => resolve({ status: res.statusCode || 0, body }))
      })
      req.on("timeout", () => req.destroy())
      req.on("error", () => resolve({ status: 0, body: "" }))
    })
  }

  async function publicHealth() {
    const { status, body } = await request("/health", {})
    if (status !== 200) return false
    try {
      return JSON.parse(body).status === "ok"
    } catch {
      return false
    }
  }

  async function ownerHealth() {
    return (await request("/api/v1/health", { Authorization: `Bearer ${credential}` })).status
  }

  function portFree() {
    return new Promise(resolve => {
      const server = net.createServer()
      server.once("error", () => resolve(false))
      server.once("listening", () => server.close(() => resolve(true)))
      server.listen(PORT, "127.0.0.1")
    })
  }

  function listenerPid() {
    return new Promise(resolve => {
      execFile(path.join(system, "netstat.exe"), ["-ano", "-p", "TCP"], { windowsHide: true }, (error, stdout) => {
        if (error) return resolve(null)
        const match = stdout.match(new RegExp(`^\\s*TCP\\s+127\\.0\\.0\\.1:${PORT}\\s+0\\.0\\.0\\.0:0\\s+\\S+\\s+(\\d+)\\s*$`, "m"))
        resolve(match ? Number(match[1]) : null)
      })
    })
  }

  function killTree(pid) {
    return new Promise(resolve => {
      if (!pid) return resolve()
      execFile(path.join(system, "taskkill.exe"), ["/PID", String(pid), "/T", "/F"], { windowsHide: true }, () => resolve())
    })
  }
}

function minutesLeft(until) {
  return Math.max(1, Math.ceil((new Date(until).getTime() - Date.now()) / 60000))
}

function clip(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim()
  return value.length > 60 ? `${value.slice(0, 60)}…` : value
}

function windows11() {
  if (process.platform !== "win32") return false
  const build = Number(os.release().split(".")[2] || 0)
  return build >= 22621
}

function systemDark() {
  return typeof nativeTheme.shouldUseDarkColorsForSystemIntegratedUI === "boolean"
    ? nativeTheme.shouldUseDarkColorsForSystemIntegratedUI
    : nativeTheme.shouldUseDarkColors
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}
