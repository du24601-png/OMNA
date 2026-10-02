import { useEffect, useRef, useState, type ReactNode } from "react"
import { ApiError, api, embeddingHelp, explain, getCredential, setCredential, type TrayStatus } from "./api"
import { Composer } from "./Composer"
import { MemoriesPage, type Filter } from "./Memories"
import { AgentPage } from "./Agents"
import { SettingsPage } from "./Settings"
import { BrandMark } from "./Brand"
import { canLeave, Notice } from "./ui"

type Page = "memories" | "agents" | "settings"
export type Service = { status: "checking" | "online" | "offline" | "error"; extractor: boolean | null; embeddings?: boolean; testMode: boolean; runtime?: { command: string[]; environment: Record<string, string> } }
const NAV: [Page, string][] = [["memories", "记忆"], ["agents", "Agent"], ["settings", "设置"]]
function rawHash() { return location.hash.replace(/^#\/?/, "") }
function route(): { page: Page; filter?: Filter } {
  const raw = rawHash()
  if (raw === "agents" || raw === "settings") return { page: raw }
  if (raw === "review" || raw === "memories/pending") return { page: "memories", filter: "pending" }
  return { page: "memories" }
}
function hashOf(page: Page) { return page === "memories" ? "#/" : `#/${page}` }

export function App() {
  const [authed, setAuthed] = useState(Boolean(getCredential()))
  const [page, setPage] = useState<Page>(() => route().page)
  const [filter, setFilter] = useState<Filter>(() => route().filter || "all")
  const [service, setService] = useState<Service>({ status: "checking", extractor: null, testMode: false })
  const [status, setStatus] = useState<TrayStatus | null>(null)
  const [composer, setComposer] = useState<"add" | "import" | null>(null)
  const [tick, setTick] = useState(0)
  const [healthTick, setHealthTick] = useState(0)
  const [resumeError, setResumeError] = useState("")
  const previousHealth = useRef("")
  const lastPending = useRef<string | null>(null)
  const refresh = () => setTick(n => n + 1)
  useEffect(() => {
    const onHash = () => {
      const next = route()
      if (canLeave()) { setPage(next.page); if (next.filter) setFilter(next.filter) }
      else history.replaceState(null, "", hashOf(page))
    }
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [page])
  useEffect(() => {
    if (!authed) return
    let alive = true, inflight = false
    const poll = async () => {
      if (inflight) return
      inflight = true
      try {
        const health = await api.ownerHealth()
        if (!alive) return
        setService({ status: "online", extractor: health.extractor_configured, embeddings: health.embeddings_loaded || health.connect_only, testMode: health.test_mode, runtime: health.mcp_runtime })
        if (previousHealth.current && previousHealth.current !== "online") refresh()
        previousHealth.current = "online"
        const next = await api.status().catch(() => null)
        if (!alive || !next) return
        setStatus(next)
        const marker = `${next.pending.count}:${next.pending.latest_at || ""}`
        if (lastPending.current !== null && lastPending.current !== marker) refresh()
        lastPending.current = marker
      } catch (err) {
        if (!alive) return
        const state = err instanceof ApiError && (err.code === "UNAVAILABLE" || err.status === 0) ? "offline" : "error"
        setService(old => ({ ...old, status: state, extractor: null }))
        previousHealth.current = state
      } finally { inflight = false }
    }
    const unavailable = () => { setService(old => ({ ...old, status: "offline", extractor: null })); previousHealth.current = "offline" }
    void poll()
    const timer = window.setInterval(poll, 4000)
    window.addEventListener("zhiwo:unavailable", unavailable)
    return () => { alive = false; clearInterval(timer); window.removeEventListener("zhiwo:unavailable", unavailable) }
  }, [authed, healthTick])
  function navigate(next: Page) {
    if (next === page) return
    if (!canLeave()) return
    history.pushState(null, "", next === "memories" && filter === "pending" ? "#/review" : hashOf(next))
    setPage(next)
  }
  const compose = (kind: "add" | "import") => { if (canLeave()) setComposer(kind) }
  const retry = () => { setHealthTick(n => n + 1); refresh() }
  async function resume() {
    setResumeError("")
    try { const sharing = await api.resumeSharing(); setStatus(old => old ? { ...old, sharing } : old); refresh() }
    catch (err) { setResumeError(explain(err)) }
  }
  const online = service.status === "online"
  const paused = !!status?.sharing.paused
  if (!authed) return <div className="app-shell"><TitleBar/><Gate onReady={() => setAuthed(true)}/></div>
  return <div className="app-shell">
    <TitleBar>
      <nav className="tabs" aria-label="主导航">{NAV.map(([id, label]) => <button key={id} type="button" className="tab" aria-current={page === id ? "page" : undefined} onClick={() => navigate(id)}>{label}</button>)}</nav>
      {service.testMode && <span className="demo-label" title="合成演示数据 · 独立测试库">演示</span>}
    </TitleBar>
    {service.status === "offline" && <div className="global-notice" role="alert">本地服务未运行。已有内容为上次加载的数据，草稿仍保留；恢复连接后可继续操作。<button className="text-button" onClick={retry}>重试</button></div>}
    {service.status === "error" && <div className="global-notice" role="alert">无法读取本地服务配置，请重试或检查本机凭证。<button className="text-button" onClick={retry}>重试</button></div>}
    {online && service.embeddings === false && <div className="global-notice" role="alert">本地向量模型没有加载，暂时不能保存或批准记忆。重试不会改变结果，{embeddingHelp}</div>}
    {paused && <div className="global-notice pause" role="status">已暂停共享，所有 Agent 现在都读不到、也不能提议{status?.sharing.paused_until ? ` · ${minutesLeft(status.sharing.paused_until)} 分钟后自动恢复` : ""}<button className="text-button" onClick={resume}>现在恢复</button>{resumeError && <span>{resumeError}</span>}</div>}
    <main className="app-main" id="main-content">
      {page === "memories" && <MemoriesPage tick={tick} online={online} paused={paused} filter={filter} onFilter={next => { setFilter(next); history.replaceState(null, "", next === "pending" ? "#/review" : "#/") }} onCompose={compose} onSaved={refresh}/>}
      {page === "agents" && <div className="page-scroll"><AgentPage tick={tick} online={online} runtime={service.runtime}/></div>}
      {page === "settings" && <SettingsPage onChanged={retry}/>}
    </main>
    {composer && <Composer kind={composer} service={service} onClose={() => setComposer(null)} onRefresh={refresh} onSaved={() => { setComposer(null); refresh() }} onReview={() => { setComposer(null); setFilter("pending"); navigate("memories"); refresh() }}/>}
  </div>
}

function minutesLeft(until: string) {
  return Math.max(1, Math.ceil((new Date(until).getTime() - Date.now()) / 60000))
}

function TitleBar({ children }: { children?: ReactNode }) {
  return <header className="titlebar">
    <span className="titlebar-brand"><BrandMark/><span>OMNA</span></span>
    {children}
    <span className="titlebar-fill"/>
    <WindowControls/>
  </header>
}

function WindowControls() {
  const [maximized, setMaximized] = useState(() => window.omna?.maximized?.() === true)
  useEffect(() => window.omna?.onMaximized?.(setMaximized), [])
  if (!window.omna?.windowAction) return null
  const run = (action: "minimize" | "maximize" | "close") => () => window.omna?.windowAction?.(action)
  return <div className="window-controls">
    <button type="button" aria-label="最小化" onClick={run("minimize")}><svg viewBox="0 0 12 12"><path d="M2 6h8" /></svg></button>
    <button type="button" aria-label={maximized ? "还原" : "最大化"} onClick={run("maximize")}>{maximized
      ? <svg viewBox="0 0 12 12"><rect x="3.5" y="1.5" width="7" height="7" /><path d="M1.5 3.5h7v7h-7z" /></svg>
      : <svg viewBox="0 0 12 12"><rect x="2" y="2" width="8" height="8" /></svg>}</button>
    <button type="button" className="close" aria-label="关闭到托盘" onClick={run("close")}><svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3L3 9" /></svg></button>
  </div>
}

function Gate({ onReady }: { onReady: () => void }) {
  const [value, setValue] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false)
  return <main className="gate"><form className="surface gate-card" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setCredential(value.trim()); try { await api.ownerHealth(); onReady() } catch (err) { setCredential(""); setError(explain(err)) } finally { setBusy(false) } }}><BrandMark/><h1>欢迎回到 OMNA</h1><p className="helper">用本机凭证打开你的个人记忆。凭证仅保存在当前浏览器会话。</p><label className="field">本机凭证<input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} required/></label>{error && <Notice tone="error">{error}</Notice>}<button className="button primary" disabled={busy || !value.trim()}>{busy ? "正在验证…" : "进入我的空间"}</button></form></main>
}
