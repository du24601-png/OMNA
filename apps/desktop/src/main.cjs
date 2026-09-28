"use strict"

const { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, session, shell } = require("electron")
const { execFile, spawn } = require("node:child_process")
const crypto = require("node:crypto")
const fs = require("node:fs")
const http = require("node:http")
const net = require("node:net")
const path = require("node:path")

const PORT = 8765
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

  app.on("second-instance", (_event, argv) => {
    if (argv.includes("--quit")) quit()
    else show()
  })
  app.on("window-all-closed", () => {})
  app.on("before-quit", () => { quitting = true })
  app.on("will-quit", event => {
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
  app.whenReady().then(start)

  async function start() {
    lockDown()
    createTray()
    createWindow()
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
      await win.loadURL(`${ORIGIN}/`)
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
      throw new Failure("端口 8765 被占用", "另一个程序正在使用 127.0.0.1:8765，OMNA 没有连到它。关闭那个程序后，再从开始菜单打开 OMNA。")
    }
    const pid = await listenerPid()
    if (pid) await killTree(pid)
    for (let i = 0; i < 20; i += 1) {
      if (await portFree()) return
      await sleep(500)
    }
    throw new Failure("端口 8765 没有释放", "上一次的本地服务还没退出。稍等片刻，再重新打开 OMNA。")
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
          showFailure(new Failure("本地服务停止了", "已连接的 Agent 暂时读不到记忆。可以从托盘菜单重新启动 OMNA。"))
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
      width: 1360,
      height: 900,
      minWidth: 1024,
      minHeight: 680,
      show: false,
      title: "OMNA",
      icon: paths.icon,
      backgroundColor: "#f5f5f7",
      autoHideMenuBar: true,
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
    const image = nativeImage.createFromPath(paths.icon)
    tray = new Tray(image.isEmpty() ? image : image.resize({ width: 16, height: 16 }))
    tray.setToolTip("OMNA")
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "打开 OMNA", click: show },
      { label: "打开日志文件夹", click: () => shell.openPath(logDir) },
      { label: "重新启动 OMNA", click: () => { app.relaunch(); quit() } },
      { type: "separator" },
      { label: "退出 OMNA", click: quit },
    ]))
    tray.on("click", show)
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

  function showFailure(error) {
    const title = error instanceof Failure ? error.title : "OMNA 没能打开"
    const detail = error instanceof Failure ? error.detail : String(error && error.message ? error.message : error)
    if (win) {
      win.loadURL(page(title, `${detail}\n日志：${logFile}`, true))
      show()
    }
  }

  function page(title, detail, failed) {
    const escape = text => String(text).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch])
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>OMNA</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
body{margin:0;height:100vh;display:grid;place-items:center;font:15px/1.7 "Segoe UI","Microsoft YaHei UI",sans-serif;background:#f5f5f7;color:#1d1d1f}
main{max-width:520px;padding:32px}
h1{font-size:22px;font-weight:600;margin:0 0 8px}
p{margin:0;color:#6e6e73;white-space:pre-wrap;word-break:break-all}
.bar{height:3px;border-radius:3px;background:#e5e5ea;overflow:hidden;margin-top:24px}
.bar i{display:block;width:30%;height:100%;background:#1d1d1f;animation:m 1.4s ease-in-out infinite}
@keyframes m{0%{transform:translateX(-100%)}100%{transform:translateX(340%)}}
@media (prefers-reduced-motion:reduce){.bar i{animation:none;width:100%}}
</style></head><body><main role="${failed ? "alert" : "status"}"><h1>${escape(title)}</h1><p>${escape(detail)}</p>${failed ? "" : '<div class="bar"><i></i></div>'}</main></body></html>`
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

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}
