"use strict"

// Real React/DOM/keyboard in headless Edge; synthetic HTTP and desktop bridge.
// Start the current web source with Vite and set OMNA_WEB_URL. This is a code
// regression, not native tray/focus/notification or Kernel acceptance.
const assert = require("node:assert/strict")
const { test, before, after } = require("node:test")
const { chromium } = require(process.env.OMNA_PLAYWRIGHT_MODULE || "playwright")
const WEB = process.env.OMNA_WEB_URL
if (!WEB) throw new Error("OMNA_WEB_URL must point to the current Vite web build")
let browser
before(async () => { browser = await chromium.launch({ channel: "msedge", headless: true }) })
after(async () => { await browser?.close() })
const proposal = id => ({ id, origin: "agent", status: "pending", target_id: null, payload: { content: "合成建议 " + id, category: "preference", kind: "fact", share_enabled: true }, source: { kind: "agent_claim", name: "Synthetic Agent" }, requester: { client: "claude-code", name: "Synthetic Agent" }, evidence: { text: "合成依据", verified: true } })

async function fixture(run) {
  const context = await browser.newContext({ viewport: { width: 368, height: 520 } })
  const page = await context.newPage()
  const old = ["p1", "p2", "p3"].map(proposal), all = [...old, proposal("p4"), proposal("p5")]
  let pending = old
  const decided = new Set()
  let getProposals = () => Promise.resolve({ status: 200, proposals: pending })
  await context.addInitScript(() => {
    window.reviewCalls = []
    window.omna = { ownerCredential: "synthetic-flyout-owner", onFlyoutShown(fn) { window.reviewShown = fn; return () => {} }, flyout: { hide() { window.reviewHidden = true }, openMain() {} } }
    const fetch = window.fetch.bind(window)
    window.fetch = (url, options) => {
      const match = String(url).match(/\/proposals\/([^/]+)\/decision$/)
      if (match) window.reviewCalls.push({ id: match[1], decision: JSON.parse(options.body).decision })
      return fetch(url, options)
    }
  })
  await page.route("**/api/v1/**", async route => {
    const pathname = new URL(route.request().url()).pathname
    const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
    if (/\/proposals\/[^/]+\/decision$/.test(pathname)) {
      decided.add(pathname.split("/").at(-2))
      return reply({ status: route.request().postDataJSON().decision === "accept" ? "accepted" : "rejected" })
    }
    if (pathname === "/api/v1/proposals") {
      const response = await getProposals()
      return reply(response.status === 200 ? { proposals: response.proposals.filter(item => !decided.has(item.id)) } : { error: { code: "UNAVAILABLE", message: "Synthetic unavailable" } }, response.status)
    }
    if (pathname === "/api/v1/memories") return reply({ items: [], total: 0 })
    if (pathname === "/api/v1/status") return reply({ service: { ok: true, embeddings_loaded: true }, sharing: { paused: false, paused_until: null }, pending: { count: 5, latest_at: null }, reads_today: { total: 0, agents: [] }, recent_reads: [] })
    throw new Error("Unexpected synthetic API request: " + pathname)
  })
  try {
    await page.goto(WEB + "/#/flyout")
    await page.locator(".flyout-ok").first().waitFor()
    page.setDefaultTimeout(3000)
    await run({ page, old, all, calls: () => page.evaluate(() => window.reviewCalls), show: id => page.evaluate(id => window.reviewShown(id), id), respond: fn => { getProposals = fn }, setPending: items => { pending = items } })
  } finally { await context.close() }
}

test("notification target blocks Y/N until loaded, then Y accepts exactly that target", () => fixture(async ({ page, all, calls, show, respond }) => {
  let release
  const delayed = new Promise(resolve => { release = resolve })
  respond(() => delayed)
  await show("p5")
  await page.keyboard.press("y")
  await page.keyboard.press("n")
  assert.deepEqual(await calls(), [], "cached p1 must not be processed while p5 is loading")
  respond(() => Promise.resolve({ status: 200, proposals: all }))
  release({ status: 200, proposals: all })
  await page.waitForFunction(() => document.querySelector(".flyout-row.focused .flyout-copy p")?.textContent === "合成建议 p5")
  assert.equal(await page.locator(".flyout-row").nth(2).locator(".flyout-copy p").textContent(), "合成建议 p5")
  await page.keyboard.press("y")
  assert.deepEqual(await calls(), [{ id: "p5", decision: "accept" }])
}))

test("a missing notified target cannot fall back to accepting another proposal", () => fixture(async ({ page, calls, show }) => {
  await show("missing")
  await page.getByRole("alert").waitFor()
  await page.keyboard.press("y")
  await page.keyboard.press("n")
  assert.deepEqual(await calls(), [])
  await page.keyboard.press("ArrowDown")
  await page.keyboard.press("y")
  assert.deepEqual(await calls(), [{ id: "p1", decision: "accept" }], "explicit selection can process another proposal")
}))

test("a failed target refresh remains safe and a later show retries the target", () => fixture(async ({ page, all, calls, show, respond }) => {
  respond(() => Promise.resolve({ status: 503, proposals: [] }))
  await show("p5")
  await page.getByRole("alert").waitFor()
  await page.keyboard.press("y")
  await page.keyboard.press("n")
  assert.deepEqual(await calls(), [])
  respond(() => Promise.resolve({ status: 200, proposals: all }))
  await show("p5")
  await page.waitForFunction(() => document.querySelector(".flyout-row.focused .flyout-copy p")?.textContent === "合成建议 p5")
  await page.keyboard.press("n")
  assert.deepEqual(await calls(), [{ id: "p5", decision: "reject" }])
}))

test("a late response from a previous show cannot overwrite the new target", () => fixture(async ({ page, old, all, calls, show, respond }) => {
  let release, markStarted
  const delayed = new Promise(resolve => { release = resolve })
  const started = new Promise(resolve => { markStarted = resolve })
  respond(() => { markStarted(); return delayed })
  await show("p5")
  await started
  respond(() => Promise.resolve({ status: 200, proposals: all }))
  await show("p4")
  await page.waitForFunction(() => document.querySelector(".flyout-row.focused .flyout-copy p")?.textContent === "合成建议 p4")
  const lateResponse = page.waitForResponse(response => response.url().includes("/api/v1/proposals"))
  release({ status: 200, proposals: old })
  await lateResponse
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.keyboard.press("y")
  assert.deepEqual(await calls(), [{ id: "p4", decision: "accept" }])
}))

test("row button focus ignores list Y/N and arrows; Enter accepts its own row", () => fixture(async ({ page, calls }) => {
  const button = page.locator(".flyout-row").nth(1).getByRole("button", { name: "记住", exact: true })
  await button.focus()
  await page.keyboard.press("y")
  await page.keyboard.press("n")
  await page.keyboard.press("ArrowDown")
  assert.deepEqual(await calls(), [])
  assert.equal(await page.locator(".flyout-row.focused .flyout-copy p").textContent(), "合成建议 p1")
  await page.keyboard.press("Enter")
  assert.deepEqual(await calls(), [{ id: "p2", decision: "accept" }])
}))

test("outer buttons ignore Y/N; each show focuses list and list arrows/Y/N still work", () => fixture(async ({ page, calls, show }) => {
  for (const name of ["暂停共享 1 小时", "打开主窗口", "设置"]) {
    await page.getByRole("button", { name, exact: true }).focus()
    await page.keyboard.press("y")
    await page.keyboard.press("n")
  }
  assert.deepEqual(await calls(), [])
  await show("")
  assert.equal(await page.evaluate(() => document.activeElement?.className), "flyout-list")
  await page.keyboard.press("ArrowDown")
  await page.keyboard.press("y")
  await page.waitForFunction(() => document.querySelectorAll(".flyout-row").length === 2)
  await page.keyboard.press("n")
  assert.deepEqual(await calls(), [{ id: "p2", decision: "accept" }, { id: "p1", decision: "reject" }])
  await page.keyboard.press("Escape")
  assert.equal(await page.evaluate(() => window.reviewHidden), true)
}))
