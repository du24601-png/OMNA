// Real React screens against the throwaway service. Only /health is controlled
// for configured/unknown/offline states. No extraction model is configured or called.
const { chromium } = require(process.env.OMNA_PLAYWRIGHT_MODULE)
const fs = require("node:fs")
const path = require("node:path")
const BASE = process.env.OMNA_BASE, WEB = process.env.OMNA_WEB
const OWNER = "dev-onboarding-owner", evidence = process.env.OMNA_UI_EVIDENCE_DIR
const checks = []
const check = (name, ok, detail) => checks.push({ name, ok, detail })
const unsafe = text => /不用 AI|没用 AI|不会上传|不上传/.test(text)

async function call(route, body) {
  const response = await fetch(BASE + "/api/v1" + route, { method: body ? "POST" : "GET", headers: { Authorization: `Bearer ${OWNER}`, "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: body ? JSON.stringify(body) : undefined })
  if (!response.ok) throw Error(`synthetic fixture API ${response.status}: ${await response.text()}`)
  return response.json()
}

async function surface(browser, kind, health) {
  const context = await browser.newContext({ viewport: { width: 960, height: 600 } })
  await context.addInitScript(owner => { sessionStorage.setItem("zhiwo-owner-credential", owner); localStorage.setItem("omna-onboarding", "done") }, OWNER)
  const page = await context.newPage()
  const runtimeErrors = []
  const consoleIssues = []
  page.on("pageerror", error => runtimeErrors.push(error.message))
  page.on("console", message => {
    if (["error", "warning"].includes(message.type())) consoleIssues.push({ type: message.type(), text: message.text(), url: message.location().url })
  })
  let mode = "hold", release
  const held = new Promise(resolve => { release = resolve })
  let imports = 0
  page.on("request", request => { if (request.method() === "POST" && /\/api\/v1\/imports$/.test(request.url())) imports++ })
  await page.route("**/api/v1/health", async route => {
    if (mode === "hold") await held
    if (mode === "offline") { await route.abort("connectionrefused"); return }
    if (mode === "error") { await route.fulfill({ status: 401, json: { error: { code: "UNAUTHENTICATED", message: "synthetic configuration read failure" } } }); return }
    const model = mode === "model"
    await route.fulfill({ json: { ...health, extractor_configured: model } })
  })
  const shot = async name => {
    const filename = `${kind}-${name}.png`
    await page.screenshot({ path: path.join(evidence, filename) })
    fs.writeFileSync(path.join(evidence, `${kind}-${name}.txt`), await page.locator("body").innerText())
    return filename
  }
  try {
    await page.goto(WEB + (kind === "guide" ? "/#/onboarding" : "/#/agents"))
    let scope, button, input
    if (kind === "guide") {
      await page.getByRole("heading", { name: "先连上你常用的 AI 工具" }).waitFor()
      await page.getByRole("button", { name: "跳过", exact: true }).click()
      await page.getByRole("button", { name: "直接读取说明文件" }).click()
      await page.getByRole("button", { name: "从 ChatGPT、Kimi、豆包搬过来" }).click()
      input = page.locator("textarea")
      await input.fill("# 偏好\n- 合成测试：喜欢简短回复")
      scope = page.locator(".ob")
      button = page.locator(".ob-foot button.primary")
    } else {
      await page.locator(".conn-row").filter({ hasText: "ChatGPT" }).click()
      scope = page.getByRole("region", { name: "带上说明文件" })
      await scope.waitFor()
      check("card unknown offer makes no AI/upload promise", !unsafe(await scope.innerText()), await shot("unknown-offer"))
      await scope.getByRole("button", { name: /直接读取文件/ }).click()
      button = scope.getByRole("button", { name: "读取并放进待确认" })
    }
    check(`${kind} unknown state makes no AI/upload promise`, !unsafe(await scope.innerText()), await shot("unknown"))
    check(`${kind} unknown state blocks import`, await button.isDisabled() && imports === 0)

    mode = "model"; release()
    await scope.getByText(kind === "guide" ? "用你配置的提取模型整理" : /会把这个文件发给你配置的提取模型/).waitFor()
    await button.waitFor({ state: "visible" })
    check(`${kind} configured model disclosure is truthful`, !unsafe(await scope.innerText()) && (await scope.innerText()).includes("提取模型"), await shot("model"))
    check(`${kind} configured online state enables import`, !(await button.isDisabled()))

    mode = "error"
    await scope.getByText(kind === "guide" ? "用你配置的提取模型整理" : /会把这个文件发给你配置的提取模型/).waitFor({ state: "hidden", timeout: 8000 })
    check(`${kind} config error state makes no AI/upload promise`, !unsafe(await scope.innerText()), await shot("config-error"))
    check(`${kind} config error is visible and blocks import`, await page.getByText(/无法读取本地服务配置/).count() > 0 && await button.isDisabled())

    mode = "offline"
    await page.getByText(kind === "guide" ? /本地服务未运行。恢复后/ : /本地服务未运行。已有内容/).waitFor({ timeout: 8000 })
    check(`${kind} offline state makes no AI/upload promise`, !unsafe(await scope.innerText()), await shot("offline"))
    check(`${kind} offline state blocks import`, await button.isDisabled() && imports === 0)
    if (input) check("guide offline preserves input", (await input.inputValue()).includes("合成测试"))

    mode = "model"
    await scope.getByText(kind === "guide" ? "用你配置的提取模型整理" : /会把这个文件发给你配置的提取模型/).waitFor({ timeout: 8000 })
    check(`${kind} recovery restores model disclosure and import`, !unsafe(await scope.innerText()) && !(await button.isDisabled()), await shot("restored"))
    if (input) check("guide recovery preserves input", (await input.inputValue()).includes("合成测试"))

    mode = "split"
    await scope.getByText(kind === "guide" ? /不用 AI，按标题和列表拆/ : /没用 AI 判断/).waitFor({ timeout: 8000 })
    check(`${kind} confirmed no-model state shows structured fallback`, unsafe(await scope.innerText()) && !(await button.isDisabled()), await shot("split"))
    check(`${kind} fixture never calls extraction`, imports === 0)
    const expectedHealthFault = issue => issue.type === "error" && /\/api\/v1\/health$/.test(issue.url) && /401|ERR_CONNECTION_REFUSED/.test(issue.text)
    const unexpectedConsole = consoleIssues.filter(issue => !expectedHealthFault(issue))
    const identity = (await page.title()) === "OMNA" && page.url() === WEB + (kind === "guide" ? "/#/onboarding" : "/#/agents")
    check(`${kind} identity, console, runtime and overlay`, identity && !runtimeErrors.length && !unexpectedConsole.length && await page.locator("vite-error-overlay").count() === 0, { runtimeErrors, unexpectedConsole, expectedInjectedHealthErrors: consoleIssues.filter(expectedHealthFault) })
  } catch (error) {
    check(`${kind} unexpected error`, false, String(error.stack || error))
    await shot("failure")
  } finally {
    mode = "split"; release(); await context.close()
  }
}

;(async () => {
  const browser = await chromium.launch({ headless: true, channel: "msedge" })
  try {
    const connected = await call("/agent-clients/codex/connect", { preset: "read", confirm: true })
    const home = fs.realpathSync(process.env.OMNA_TEST_HOME)
    const relative = path.relative(home, fs.realpathSync(connected.config_path))
    check("connection fixture writes only to fake home", relative === path.join(".codex", "config.toml") && fs.existsSync(path.join(home, relative)), { relative })
    const health = await call("/health")
    check("real test service has no extractor", health.test_mode && !health.extractor_configured)
    await surface(browser, "guide", health)
    await surface(browser, "card", health)
  } catch (error) { check("fixture error", false, String(error.stack || error)) }
  finally { await browser.close() }
  console.log(JSON.stringify({ pass: checks.every(item => item.ok), checks, browser: "headless Microsoft Edge via external Playwright", synthetic_health_only: true, screenshots: evidence }))
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1 })
