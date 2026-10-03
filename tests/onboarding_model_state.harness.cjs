// Real import card; only /health is a browser fixture. No model is called.
const { chromium } = require(process.env.OMNA_PLAYWRIGHT_MODULE)
const fs = require('node:fs'), path = require('node:path')
const BASE = process.env.OMNA_BASE, WEB = process.env.OMNA_WEB
const OWNER = 'dev-onboarding-owner', evidence = process.env.OMNA_UI_EVIDENCE_DIR
const checks = []
const check = (name, ok, detail) => checks.push({ name, ok, detail })
async function call(route, body) {
  const response = await fetch(BASE + '/api/v1' + route, { method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${OWNER}`, 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: body ? JSON.stringify(body) : undefined })
  if (!response.ok) throw Error(`synthetic fixture API ${response.status}: ${await response.text()}`)
  return response.json()
}
;(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const context = await browser.newContext({ viewport: { width: 960, height: 600 } })
  let mode = 'hold', release, imports = 0
  const held = new Promise(resolve => { release = resolve })
  const errors = [], consoleIssues = []
  await context.addInitScript(owner => { sessionStorage.setItem('zhiwo-owner-credential', owner); localStorage.setItem('omna-onboarding', 'done') }, OWNER)
  const page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) consoleIssues.push({ type: message.type(), text: message.text(), url: message.location().url }) })
  page.on('request', request => { if (request.method() === 'POST' && /\/api\/v1\/imports$/.test(request.url())) imports++ })
  const shot = async name => {
    const filename = `card-${name}.png`
    await page.screenshot({ path: path.join(evidence, filename) })
    fs.writeFileSync(path.join(evidence, `card-${name}.txt`), await page.locator('body').innerText())
    return filename
  }
  try {
    const connected = await call('/agent-clients/codex/connect', { preset: 'read', confirm: true })
    const home = fs.realpathSync(process.env.OMNA_TEST_HOME)
    const relative = path.relative(home, fs.realpathSync(connected.config_path))
    check('connection writes only to fake home', relative === path.join('.codex', 'config.toml'), { relative })
    const health = await call('/health')
    check('real service is test mode without extractor', health.test_mode && !health.extractor_configured)
    await page.route('**/api/v1/health', async route => {
      if (mode === 'hold') await held
      if (mode === 'offline') return route.abort('connectionrefused')
      if (mode === 'error') return route.fulfill({ status: 401, json: { error: { code: 'UNAUTHENTICATED', message: 'synthetic configuration read failure' } } })
      await route.fulfill({ json: { ...health, extractor_configured: mode === 'model' } })
    })
    await page.goto(WEB + '/#/agents')
    await page.locator('.conn-row').filter({ hasText: 'ChatGPT' }).click()
    const card = page.getByRole('region', { name: '带上说明文件' })
    await card.waitFor()
    const open = async () => { await card.getByRole('button', { name: '更多方式' }).click(); return page.getByRole('menuitem', { name: /直接读取文件/ }) }
    const assertMenu = async (state, label, disabled) => {
      const item = await open()
      await page.getByRole('menuitem', { name: label }).waitFor()
      check(`${state}: menu label and availability`, await item.isDisabled() === disabled && imports === 0, await shot(state))
      await page.keyboard.press('Escape')
    }
    await assertMenu('unknown', '直接读取文件（等本机服务就绪）', true)
    mode = 'model'; release()
    await assertMenu('configured', '直接读取文件（用你配置的提取模型）', false)
    await (await open()).click()
    const dialog = page.getByRole('alertdialog')
    await dialog.waitFor()
    check('configured consent discloses sending to model; no import before consent', (await dialog.innerText()).includes('会把这个文件发给你配置的提取模型') && imports === 0, await shot('model-consent'))
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    mode = 'error'
    await page.getByText(/无法读取本地服务配置/).waitFor()
    await assertMenu('configuration-error', '直接读取文件（等本机服务就绪）', true)
    mode = 'offline'
    await page.getByText(/本地服务未运行。已有内容/).waitFor()
    await assertMenu('offline', '直接读取文件（等本机服务就绪）', true)
    mode = 'model'
    await page.getByText(/本地服务未运行。已有内容/).waitFor({ state: 'hidden' })
    await assertMenu('recovered', '直接读取文件（用你配置的提取模型）', false)
    mode = 'split'
    await assertMenu('unconfigured', '直接读取文件（不用 AI）', false)
    await (await open()).click()
    await dialog.waitFor()
    check('unconfigured consent describes structural split; no import before consent', (await dialog.innerText()).includes('没用 AI 判断') && (await dialog.innerText()).includes('按标题和列表') && imports === 0, await shot('split-consent'))
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    check('state regression never calls extraction', imports === 0)
    const expected = issue => issue.type === 'error' && /\/api\/v1\/health$/.test(issue.url) && /401|ERR_CONNECTION_REFUSED/.test(issue.text)
    const unexpectedConsole = consoleIssues.filter(issue => !expected(issue))
    check('page identity, content, runtime, console and overlay', await page.title() === 'OMNA' && page.url() === WEB + '/#/agents' && await card.isVisible() && !errors.length && !unexpectedConsole.length && await page.locator('vite-error-overlay').count() === 0, { errors, unexpectedConsole, expectedInjectedHealthErrors: consoleIssues.filter(expected) })
  } catch (error) { check('unexpected error', false, String(error.stack || error)); await shot('failure') }
  finally { mode = 'split'; release?.(); await context.close(); await browser.close() }
  console.log(JSON.stringify({ pass: checks.every(item => item.ok), checks, browser: 'headless Microsoft Edge via external Playwright', synthetic_health_only: true, screenshots: evidence, removed_obsolete_assertions: ['4-step guide direct-file / ChatGPT import navigation', 'guide extractor disclosure and import availability', 'guide text-input preservation during outage and recovery'], replacement: 'Agent import card menu: unknown, configured, config error, offline, recovery, unconfigured; consent copy and zero import requests' }))
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1 })
