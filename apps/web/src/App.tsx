import { Dialog } from "@base-ui/react/dialog"
import { Menu } from "@base-ui/react/menu"
import { Popover } from "@base-ui/react/popover"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"
import { ApiError, api, embeddingHelp, explain, getCredential, setCredential, type Memory } from "./api"
import { Detail } from "./Detail"
import { CATEGORIES, categoryLabel, dateLabel, listTime } from "./format"
import { canLeave, Empty, Icon, Notice, ResourceNotice, Skeleton, SourceMark, useResource } from "./ui"
import { Composer } from "./Composer"
import { ReviewPage } from "./Review"
import { AgentPage } from "./Agents"
import { SettingsDialog } from "./Settings"
import { BrandLockup, BrandMark } from "./Brand"
import { ReadTrend } from "./Trend"

type Page = "profile" | "memories" | "review" | "agents"
export type Service = { status: "checking" | "online" | "offline" | "error"; extractor: boolean | null; embeddings?: boolean; testMode: boolean; runtime?: { command: string[]; environment: Record<string, string> } }
const NAV: [Page, string][] = [["profile", "关于我"], ["memories", "记忆"], ["review", "待确认"], ["agents", "我的 Agent"]]
function rawHash() { return location.hash.replace(/^#\/?/, "") }
function pageFromHash(): Page { const page = rawHash(); return ["memories", "review", "agents"].includes(page) ? page as Page : "profile" }

export function App() {
  const [authed, setAuthed] = useState(Boolean(getCredential()))
  const [page, setPage] = useState<Page>(pageFromHash)
  const [service, setService] = useState<Service>({ status: "checking", extractor: null, testMode: false })
  const [query, setQuery] = useState("")
  const [activeQuery, setActiveQuery] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [morph, setMorph] = useState<{ layoutId?: string; seed?: string; origin?: { client: string; name: string } }>({})
  const [composer, setComposer] = useState<"add" | "import" | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(() => rawHash() === "settings")
  const [tick, setTick] = useState(0)
  const [healthTick, setHealthTick] = useState(0)
  const previousHealth = useRef("")
  const main = useRef<HTMLElement>(null)
  const refresh = () => setTick(n => n + 1)
  useEffect(() => {
    const hash = () => { if (canLeave()) { setSettingsOpen(rawHash() === "settings"); setPage(pageFromHash()); closeMemory() } else history.replaceState(null, "", settingsOpen ? "#/settings" : page === "profile" ? "#/" : `#/${page}`) }
    window.addEventListener("hashchange", hash)
    return () => window.removeEventListener("hashchange", hash)
  }, [page, settingsOpen])
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
      } catch (err) {
        if (!alive) return
        const status = err instanceof ApiError && (err.code === "UNAVAILABLE" || err.status === 0) ? "offline" : "error"
        setService(old => ({ ...old, status, extractor: null }))
        previousHealth.current = status
      } finally { inflight = false }
    }
    const unavailable = () => { setService(old => ({ ...old, status: "offline", extractor: null })); previousHealth.current = "offline" }
    void poll()
    const timer = window.setInterval(poll, 4000)
    window.addEventListener("zhiwo:unavailable", unavailable)
    return () => { alive = false; clearInterval(timer); window.removeEventListener("zhiwo:unavailable", unavailable) }
  }, [authed, healthTick])
  function navigate(next: Page) {
    if (next === page && !settingsOpen) return
    if (!canLeave()) return
    history.pushState(null, "", next === "profile" ? "#/" : `#/${next}`)
    setSettingsOpen(false); setPage(next); closeMemory(); main.current?.scrollTo(0, 0)
  }
  function closeSettings() {
    if (!canLeave()) return
    history.pushState(null, "", page === "profile" ? "#/" : `#/${page}`)
    setSettingsOpen(false)
  }
  function openSettings() {
    if (settingsOpen) { closeSettings(); return }
    if (!canLeave()) return
    setComposer(null); closeMemory()
    history.pushState(null, "", "#/settings")
    setSettingsOpen(true)
  }
  const openMemory = (id: string, layoutId?: string, seed?: string, origin?: { client: string; name: string }) => { if (!selected || selected === id || canLeave()) { setSelected(id); setMorph({ layoutId, seed, origin }) } }
  const closeMemory = () => { setSelected(null); setMorph({}) }
  const compose = (kind: "add" | "import") => { if (canLeave()) { closeMemory(); setComposer(kind) } }
  const retry = () => { setHealthTick(n => n + 1); refresh() }
  const online = service.status === "online"
  if (!authed) return <><WindowDrag/><WindowControls/><Gate onReady={() => setAuthed(true)}/></>
  const serviceLabel = online ? "本地服务正常" : service.status === "checking" ? "正在检查本地服务" : service.status === "offline" ? "本地服务未运行" : "无法读取服务配置"
  return <div className="app-shell">
    <LiquidFilter/>
    <WindowDrag/>
    <WindowControls/>
    <DockNav page={page} settingsOpen={settingsOpen} onNavigate={navigate} onOpenSettings={openSettings}/>
    <div className="app-body">
      {service.status === "offline" && <div className="global-notice" role="alert">本地服务未运行。已有内容为上次加载的数据，草稿仍保留；恢复连接后可继续操作。<button className="text-button" onClick={retry}>重试</button></div>}
      {service.status === "error" && <div className="global-notice" role="alert">无法读取本地服务配置，请重试或检查本机凭证。提取模型配置尚未确认。<button className="text-button" onClick={retry}>重试</button></div>}
      {online && service.embeddings === false && <div className="global-notice" role="alert">本地向量模型没有加载，暂时不能保存或批准记忆。重试不会改变结果，{embeddingHelp}</div>}
      <div className="workspace">
        <div className="workspace-scroll">
          <main className="main-content" ref={main} id="main-content">
            {page === "profile" && <ProfilePage tick={tick} service={service} serviceLabel={serviceLabel} onRetry={retry} openLayout={selected ? morph.layoutId : undefined} onOpen={openMemory} onAdd={() => compose("add")} onImport={() => compose("import")}/>}
            {page === "memories" && <MemoryPage tick={tick} selectedId={selected || undefined} query={activeQuery} draft={query} onDraft={setQuery} onSearch={() => { if (canLeave()) { setActiveQuery(query.trim()); closeMemory() } }} onClear={() => { setQuery(""); setActiveQuery("") }} onOpen={openMemory} onAdd={() => compose("add")}/>}
            {page === "review" && <ReviewPage tick={tick} online={online} onOpen={openMemory} onSaved={refresh}/>}
            {page === "agents" && <AgentPage tick={tick} online={online} runtime={service.runtime}/>}
          </main>
        </div>
        {selected && <Detail key={selected} memoryId={selected} layoutId={morph.layoutId} seed={morph.seed} origin={morph.origin} online={online} onClose={closeMemory} onSaved={refresh}/>}
      </div>
    </div>
    {composer && <Composer kind={composer} service={service} onClose={() => setComposer(null)} onRefresh={refresh} onSaved={id => { setComposer(null); refresh(); if (id) setSelected(id) }} onReview={() => { setComposer(null); navigate("review"); refresh() }}/>}
    {settingsOpen && <SettingsDialog onClose={closeSettings} onChanged={retry}/>}
  </div>
}
function WindowDrag() {
  if (!window.omna) return null
  return <div className="window-drag" />
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
    <button type="button" className="close" aria-label="关闭" onClick={run("close")}><svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3L3 9" /></svg></button>
  </div>
}
function LiquidFilter() {
  return <svg className="liquid-defs" aria-hidden="true" focusable="false">
    <filter id="liquid-glass" x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
      <feTurbulence type="fractalNoise" baseFrequency="0.012" numOctaves="2" seed="4" result="noise"/>
      <feGaussianBlur in="noise" stdDeviation="1.4" result="map"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="14" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
  </svg>
}
function DockNav({ page, settingsOpen, onNavigate, onOpenSettings }: { page: Page; settingsOpen: boolean; onNavigate: (next: Page) => void; onOpenSettings: () => void }) {
  const items = useRef<(HTMLButtonElement | null)[]>([])
  const reduce = useReducedMotion()
  function magnify(x: number | null) {
    let nearest = -1
    let nearestScore = 0.28
    items.current.forEach((el, index) => {
      if (!el) return
      let scale = 1
      let score = 0
      if (x !== null) {
        const box = el.getBoundingClientRect()
        const distance = Math.abs(x - (box.left + box.width / 2))
        const near = Math.max(0, 1 - distance / 96)
        score = near * near * (3 - 2 * near)
        if (!reduce) scale = 1 + score * 0.62
      }
      el.style.setProperty("--dock", scale.toFixed(3))
      if (score > nearestScore) { nearestScore = score; nearest = index }
    })
    items.current.forEach((el, index) => el?.classList.toggle("named", index === nearest))
  }
  return <nav className="dock" aria-label="主导航" onMouseMove={event => magnify(event.clientX)} onMouseLeave={() => magnify(null)}>{NAV.map(([id, label], index) => <button key={id} ref={el => { items.current[index] = el }} type="button" className={`dock-item ${page === id ? "active" : ""}`} aria-label={label} aria-current={page === id ? "page" : undefined} onClick={() => onNavigate(id)}><Icon name={id}/><span className="dock-label" aria-hidden="true">{label}</span></button>)}<button ref={el => { items.current[NAV.length] = el }} type="button" className={`dock-item ${settingsOpen ? "active" : ""}`} aria-label="设置" aria-expanded={settingsOpen} aria-haspopup="dialog" onClick={onOpenSettings}><Icon name="settings"/><span className="dock-label" aria-hidden="true">设置</span></button></nav>
}
function Gate({ onReady }: { onReady: () => void }) {
  const [value, setValue] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false)
  return <main className="gate"><form className="surface gate-card" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setCredential(value.trim()); try { await api.ownerHealth(); onReady() } catch (err) { setCredential(""); setError(explain(err)) } finally { setBusy(false) } }}><BrandMark/><h1>欢迎回到 OMNA</h1><p className="helper">用本机凭证打开你的个人记忆。凭证仅保存在当前浏览器会话。</p><label className="field">本机凭证<input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} required/></label>{error && <Notice tone="error">{error}</Notice>}<button className="button primary" disabled={busy || !value.trim()}>{busy ? "正在验证…" : "进入我的空间"}</button></form></main>
}
function ProfileSkeleton() {
  const cards = [["100%", "72%"], ["86%", "48%"], ["92%"], ["70%", "40%"]]
  return <div className="profile-split" role="status" aria-label="正在加载关于我">
    <div className="profile-night"><div className="night-wall">{cards.map((lines, index) => <div className="memory-card skeleton-card" key={index}><Skeleton className="skeleton-chip"/>{lines.map((width, line) => <Skeleton key={line} className="skeleton-line" style={{ width }}/>)}</div>)}</div></div>
    <div className="profile-side">
      <div className="side-pane"><Skeleton className="skeleton-line" style={{ width: "40%" }}/><Skeleton className="skeleton-line" style={{ width: "88%", marginTop: 16 }}/><Skeleton className="skeleton-line" style={{ width: "64%", marginTop: 10 }}/></div>
      <div className="side-pane"><div className="trend-skeleton"/></div>
    </div>
  </div>
}
function moveSpot(event: { clientX: number; clientY: number; currentTarget: HTMLElement }) {
  const rect = event.currentTarget.getBoundingClientRect()
  event.currentTarget.style.setProperty("--spot-x", `${event.clientX - rect.left}px`)
  event.currentTarget.style.setProperty("--spot-y", `${event.clientY - rect.top}px`)
}
function ProfilePage({ tick, service, serviceLabel, onRetry, openLayout, onOpen, onAdd, onImport }: { tick: number; service: Service; serviceLabel: string; onRetry: () => void; openLayout?: string; onOpen: (id: string, layoutId?: string, seed?: string) => void; onAdd: () => void; onImport: () => void }) {
  const resource = useResource(() => api.profile(), [tick])
  const data = resource.data
  const online = service.status === "online"
  const count = data?.groups.reduce((n, g) => n + g.cards.length, 0) || 0
  const empty = data && !count && !data.recent.length
  const filled = data?.groups.flatMap(group => group.cards) || []
  const vacant = data?.groups.filter(group => !group.cards.length) || []
  return <div className="page profile-page">
    <div className="home-bar">
      <div className="home-brand">
        <a className="brand" href="#/" aria-label="OMNA" onClick={event => event.preventDefault()}><BrandLockup/></a>
        <span className={`service-status ${online ? "online" : ""}`} title={serviceLabel}><i/>{service.status === "offline" ? "未运行" : service.status === "error" ? "异常" : ""}</span>
        {service.testMode && <span className="demo-label" title="合成演示数据 · 独立测试库">演示</span>}
        {!online && service.status !== "checking" && <button className="text-button" onClick={onRetry}>重试</button>}
      </div>
      <div className="home-actions">
        <Menu.Root>
          <Menu.Trigger className="add-trigger">
            <Icon name="plus"/>
            添加
            <span className="add-trigger-chevron"><Icon name="chevron"/></span>
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className="add-menu-positioner" side="bottom" align="end" sideOffset={8}>
              <Menu.Popup className="add-menu-popup">
                <Menu.Item className="add-menu-item" onClick={onAdd}><Icon name="plus"/>添加记忆</Menu.Item>
                <Menu.Item className="add-menu-item" onClick={onImport}><Icon name="import"/>导入</Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
    </div>
    <ResourceNotice resource={resource} pending={false}/>
    {resource.loading && !data && !resource.error && <ProfileSkeleton/>}
    {data && <div className="profile-split">
      <section className="profile-night" aria-label="已确认的记忆">
        {empty && !resource.loading && !resource.error ? <Empty title="从一条真实的记忆开始" action={<><button className="button secondary" onClick={onAdd}>添加第一条记忆</button><button className="button secondary" onClick={onImport}>导入已有文本</button></>}>记录你的偏好、目标或正在做的事。只有你确认过的内容，才会出现在这里。</Empty> : <NightWall cards={filled} vacant={vacant} openLayout={openLayout} onOpen={onOpen} onAdd={onAdd}/>}
      </section>
      <div className="profile-side">
        <ProfileSummaryPanel tick={tick} online={online}/>
        <ReadTrend tick={tick}/>
      </div>
    </div>}
  </div>
}
function summaryBlocks(text: string) {
  const blocks: { heading: string | null; body: string }[] = []
  let heading: string | null = null
  let lines: string[] = []
  const flush = () => {
    const body = lines.join("\n").trim()
    if (heading || body) blocks.push({ heading, body })
    heading = null
    lines = []
  }
  for (const line of text.split("\n")) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (lines.length) lines.push("")
      continue
    }
    const match = trimmed.match(/^([^：:]{1,12})[：:]\s*(.*)$/)
    if (match && !/[。！？，,.!?]/.test(match[1])) {
      flush()
      heading = match[1].trim()
      if (match[2].trim()) lines.push(match[2].trim())
      continue
    }
    lines.push(trimmed)
  }
  flush()
  return blocks
}

function SummaryContent({ text, className }: { text: string; className?: string }) {
  return <div className={className}>{summaryBlocks(text).map((block, index) => <div key={index}>{block.heading && <h3>{block.heading}</h3>}{block.body && <p>{block.body}</p>}</div>)}</div>
}

const SUMMARY_GLOW = ["#0894FF", "#C959DD", "#FF2E54", "#FF9004"]

function summaryGlowFrames(colors: string[]) {
  return colors.map((color, index) => {
    const next = colors[(index + 1) % colors.length]
    return `conic-gradient(from 0deg at 50% 50%, ${color} 0%, ${next} 50%, ${color} 100%)`
  })
}

function SummaryDialogGlow({ active }: { active: boolean }) {
  const reduce = useReducedMotion()
  const frames = summaryGlowFrames(SUMMARY_GLOW)
  return <motion.div className="summary-dialog-glow" aria-hidden="true" initial={{ opacity: 0 }} animate={{ opacity: active ? 1 : 0 }} transition={{ duration: 0.2, ease: "easeOut" }}>
    <motion.div className="summary-dialog-glow-shift" style={{ background: frames[0] }} animate={reduce ? undefined : { background: frames }} transition={{ duration: 4, ease: "linear", repeat: Infinity, repeatType: "mirror" }} />
  </motion.div>
}

function ProfileSummaryPanel({ tick, online }: { tick: number; online: boolean }) {
  const [summaryTick, setSummaryTick] = useState(0)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const resource = useResource(() => api.profileSummary(), [tick, summaryTick])
  const summary = resource.data
  const text = summary?.text || ""
  const disabled = busy || !online || !summary?.extractor_configured || !summary.current_memory_count
  useEffect(() => { if (!text) setOpen(false) }, [text])
  async function generate() {
    if (disabled) return
    setBusy(true); setError("")
    try {
      await api.generateProfileSummary()
      setSummaryTick(value => value + 1)
    } catch (err) {
      setError(explain(err))
      if (err instanceof ApiError && err.code === "CONFLICT") setSummaryTick(value => value + 1)
    } finally {
      setBusy(false)
    }
  }
  const notice = error || (resource.error ? explain(resource.error) : "")
  const meta = summary?.status === "stale" ? "记忆有变化，摘要待更新" : summary?.status === "current" ? `${summary.memory_count} 条记忆 · ${dateLabel(summary.generated_at || undefined)}` : summary ? `${summary.current_memory_count} 条当前记忆` : ""
  return <section className={`profile-summary ${text ? "has-text" : "is-empty"} ${summary?.status === "stale" ? "is-stale" : ""}`} aria-label="AI 摘要">
    {resource.loading && !summary && <div className="summary-loading"><Skeleton className="skeleton-line" style={{ width: "92%" }}/><Skeleton className="skeleton-line" style={{ width: "68%", marginTop: 10 }}/></div>}
    {text ? <button type="button" className="profile-summary-open" onClick={() => setOpen(true)} aria-haspopup="dialog">
      <header className="profile-summary-head"><span className="summary-kicker">AI 摘要</span></header>
      <SummaryContent text={text} className="profile-summary-text"/>
      {meta && <p className={`profile-summary-meta${summary?.status === "stale" ? " is-stale" : ""}`}>{meta}</p>}
    </button> : !resource.loading && <div className="profile-summary-empty-state">
      <header className="profile-summary-head"><span className="summary-kicker">AI 摘要</span></header>
      <button type="button" className="button primary" disabled={disabled} onClick={generate}>{busy ? "正在生成…" : "生成摘要"}</button>
      {!summary?.extractor_configured && summary && <strong>请先在设置中配置提取模型</strong>}
      {notice && <p className="profile-summary-error" role="alert">{notice}</p>}
    </div>}
    {text && notice && <p className="profile-summary-error" role="alert">{notice}</p>}
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Backdrop className="dialog-backdrop" />
        <Dialog.Popup className="summary-dialog" aria-busy={busy || undefined}>
          <SummaryDialogGlow active={busy} />
          <div className="summary-dialog-sheet material">
            <header className="summary-dialog-head">
              <div>
                <Dialog.Title>记忆摘要</Dialog.Title>
                <p>{summary?.generated_at ? `更新于 ${dateLabel(summary.generated_at)}` : "尚未生成"}</p>
              </div>
              <Dialog.Close className="icon-button" aria-label="关闭摘要"><Icon name="close" /></Dialog.Close>
            </header>
            {summary?.status === "stale" && <p className="summary-dialog-stale">记忆有变化，摘要待更新</p>}
            <SummaryContent text={text} className="summary-dialog-body"/>
            {notice && <p className="profile-summary-error summary-dialog-error" role="alert">{notice}</p>}
            <footer className="summary-dialog-foot">
              {!summary?.extractor_configured && <strong>请先在设置中配置提取模型</strong>}
              <button type="button" className="button primary summary-update" disabled={disabled} onClick={generate}>{busy ? "正在更新…" : "更新摘要"}</button>
            </footer>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  </section>
}
function NightWall({ cards, vacant, openLayout, onOpen, onAdd }: { cards: Memory[]; vacant: { id: string; title: string }[]; openLayout?: string; onOpen: (id: string, layoutId?: string, seed?: string) => void; onAdd: () => void }) {
  const [hot, setHot] = useState<string | null>(null)
  return <div className={`night-wall${hot ? " is-hot" : ""}`}>
    {cards.map(card => <MemoryCard key={card.id} memory={card} layoutId={`memory-${card.id}`} openLayout={openLayout} hot={hot === card.id} onHot={setHot} onOpen={onOpen}/>)}
    {vacant.map(group => <button key={group.id} type="button" className={`memory-card memory-card-empty${hot === group.id ? " is-hot" : ""}`} onClick={onAdd} onMouseEnter={() => setHot(group.id)} onMouseLeave={() => setHot(null)} onMouseMove={moveSpot} onFocus={() => setHot(group.id)} onBlur={() => setHot(null)}><span className="night-spot" aria-hidden="true"/><span className="card-chip">{group.title}</span><p>{group.title}还是空的</p></button>)}
  </div>
}
const morphSpring = { type: "spring" as const, stiffness: 200, damping: 24 }
function MemoryCard({ memory, layoutId, openLayout, hot, onHot, onOpen }: { memory: Memory; layoutId: string; openLayout?: string; hot: boolean; onHot: (id: string | null) => void; onOpen: (id: string, layoutId?: string, seed?: string) => void }) {
  const reduce = useReducedMotion()
  const source = openLayout === layoutId
  const brief = (memory.content || "").length > 0 && (memory.content || "").length <= 40
  return <motion.button type="button" layoutId={reduce ? undefined : layoutId} className={`memory-card${hot ? " is-hot" : ""}`} data-brief={brief ? "1" : undefined} data-morph-source={source || undefined} style={{ borderRadius: 12 }} transition={morphSpring} aria-expanded={source} onClick={() => onOpen(memory.id, layoutId, memory.content || undefined)} onMouseEnter={() => onHot(memory.id)} onMouseLeave={() => onHot(null)} onMouseMove={moveSpot} onFocus={() => onHot(memory.id)} onBlur={() => onHot(null)}><span className="night-spot" aria-hidden="true"/><span className="card-chip">{categoryLabel(memory.category)}</span><p>{memory.content || "正文暂时无法读取。"}</p><span className="card-caption">{memory.scope && <span>{memory.scope}</span>}{memory.share_enabled === false && <span className="card-share">仅自己可见</span>}<span>版本 {memory.revision}</span><time>{dateLabel(memory.created_at)}</time></span></motion.button>
}
function MemoCard({ memory, open, onOpen }: { memory: Memory; open: boolean; onOpen: (id: string, layoutId?: string, seed?: string, origin?: { client: string; name: string }) => void }) {
  const privateOnly = memory.share_enabled === false
  const revised = memory.revision > 1
  return <button type="button" className={`memo-card${open ? " is-open" : ""}`} aria-expanded={open} onClick={() => onOpen(memory.id, undefined, memory.content || undefined, memory.origin)}>
    <span className="memo-sentence">{memory.content || "正文暂时无法读取。"}</span>
    <span className="memo-topic"><span className="memo-cat">{categoryLabel(memory.category)}</span>{memory.scope && <span className="memo-cat">{memory.scope}</span>}{privateOnly && <span className="memo-note">仅自己可见</span>}{revised && <span className="memo-note">版本 {memory.revision}</span>}</span>
    <SourceMark origin={memory.origin}/>
    <time dateTime={memory.created_at} title={dateLabel(memory.created_at)}>{listTime(memory.created_at)}</time>
  </button>
}
const SOURCE_FILTERS: [string, string][] = [
  ["omna", "OMNA"],
  ["workbuddy", "WorkBuddy"],
  ["zcode", "ZCode"],
  ["opencode", "OpenCode"],
  ["codex", "ChatGPT"],
  ["claude", "Claude"],
  ["claude-code", "Claude Code"],
]
function MemorySkeleton() {
  const rows = ["68%", "42%", "76%", "55%", "61%", "34%"]
  return <div className="memo-list" role="status" aria-label="正在加载记忆">
    <div className="memo-head" aria-hidden="true"><span>记忆</span><span>主题</span><span>来源</span><span>时间</span></div>
    {rows.map((width, index) => <div className="memo-card skeleton-row" key={index}><Skeleton className="skeleton-sentence" style={{ width }}/><Skeleton className="skeleton-pill"/><span className="memo-source"><Skeleton className="skeleton-mark"/><Skeleton className="skeleton-source"/></span><Skeleton className="skeleton-time"/></div>)}
  </div>
}
function byCreated(sort: "newest" | "oldest") {
  return (a: Memory, b: Memory) => {
    const left = a.created_at || "", right = b.created_at || ""
    if (left === right) return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    const newerFirst = left > right ? -1 : 1
    return sort === "newest" ? newerFirst : -newerFirst
  }
}
function MemoryPage({ tick, selectedId, query, draft, onDraft, onSearch, onClear, onOpen, onAdd }: { tick: number; selectedId?: string; query: string; draft: string; onDraft: (value: string) => void; onSearch: () => void; onClear: () => void; onOpen: (id: string, layoutId?: string, seed?: string, origin?: { client: string; name: string }) => void; onAdd: () => void }) {
  const [state, setState] = useState("current"), [category, setCategory] = useState(""), [origin, setOrigin] = useState(""), [sort, setSort] = useState<"newest" | "oldest">("newest")
  const searching = !!query && state === "current"
  const resource = useResource(() => api.memories({ state, category, origin, query, limit: searching ? 20 : 50, sort: searching ? undefined : sort }), [state, category, origin, query, tick, searching ? "search" : sort])
  const feedRef = useRef<HTMLDivElement>(null)
  const activeKey = useRef("")
  const flight = useRef(0)
  const [extra, setExtra] = useState<Memory[]>([])
  const [moreCursor, setMoreCursor] = useState<string | null>(null)
  const [moreLoading, setMoreLoading] = useState(false)
  const [moreError, setMoreError] = useState("")
  const pageKey = JSON.stringify([state, category, origin, query, sort, tick])
  const [seenKey, setSeenKey] = useState(pageKey)
  if (seenKey !== pageKey) {
    setSeenKey(pageKey)
    setExtra([])
    setMoreCursor(null)
    setMoreError("")
    setMoreLoading(false)
    flight.current += 1
  }
  activeKey.current = pageKey
  useEffect(() => { feedRef.current?.scrollTo({ top: 0 }) }, [pageKey])
  useEffect(() => {
    const node = feedRef.current
    if (!node) return
    let timer = 0
    const onScroll = () => {
      node.classList.add("is-scrolling")
      window.clearTimeout(timer)
      timer = window.setTimeout(() => node.classList.remove("is-scrolling"), 700)
    }
    node.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      window.clearTimeout(timer)
      node.removeEventListener("scroll", onScroll)
    }
  }, [])
  useEffect(() => {
    setExtra([])
    setMoreError("")
    if (!resource.data || searching) { setMoreCursor(null); return }
    setMoreCursor(resource.data.next_cursor ?? null)
  }, [resource.data, searching])
  const filtered = !!query || !!category || !!origin || state !== "current"
  const clear = () => { setState("current"); setCategory(""); setOrigin(""); onClear() }
  const statuses: [string, string][] = [["all", "全部"], ["current", "当前"], ["expired", "过期"], ["history", "历史"]]
  const knownOrigins = new Set(SOURCE_FILTERS.map(([id]) => id))
  const extraOrigins = (resource.data?.origins ?? []).filter(item => !knownOrigins.has(item.id))
  const topic = category ? categoryLabel(category) : ""
  const originName = SOURCE_FILTERS.find(([id]) => id === origin)?.[1] || extraOrigins.find(item => item.id === origin)?.name || ""
  const statusLabel = statuses.find(([id]) => id === state)?.[1] || ""
  const baseName = state === "current" && !topic ? "" : state === "all" ? (topic || "全部") : topic ? `${statusLabel} · ${topic}` : statusLabel
  const filterName = originName ? (baseName ? `${baseName} · ${originName}` : originName) : baseName
  const first = resource.data?.items ?? []
  const shown = searching ? first.slice().sort(byCreated(sort)) : [...first, ...extra]
  const total = typeof resource.data?.total === "number" ? resource.data.total : null
  const truncated = !!resource.data?.truncated
  const partial = !searching && total !== null && shown.length < total && (!!moreCursor || moreLoading)
  const canMore = !searching && !resource.loading && !!moreCursor
  const loadMore = () => {
    const cursor = moreCursor
    const key = activeKey.current
    if (!cursor || !canMore) return
    const token = ++flight.current
    setMoreLoading(true)
    setMoreError("")
    api.memories({ state, category, origin, query, limit: 50, sort, cursor }).then(page => {
      if (token !== flight.current || activeKey.current !== key) return
      setExtra(rows => [...rows, ...page.items])
      setMoreCursor(page.next_cursor ?? null)
    }).catch(err => {
      if (token !== flight.current || activeKey.current !== key) return
      setMoreError(explain(err))
    }).finally(() => {
      if (token === flight.current) setMoreLoading(false)
    })
  }
  return <div className="page library-page">
    <div className="library-feed" ref={feedRef}>
      {query && <p className="helper search-caption">搜索“{query}”，仅包含已确认内容{truncated ? "。只列出最相关的 20 条" : ""}</p>}
      <div className="list-tools">
        <div className="list-tool-group">
          <Popover.Root>
            <Popover.Trigger className={`list-tool${filterName ? " on" : ""}`} aria-label={filterName ? `筛选，${filterName}` : "筛选"}>
              <Icon name="filter"/>{filterName || "筛选"}
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Positioner className="tool-positioner" side="bottom" align="start" sideOffset={8}>
                <Popover.Popup className="select-popup tool-popup">
                  <p className="filter-label">状态</p>
                  <div className="filter-list" role="group" aria-label="记忆状态">{statuses.map(([id, label]) => <button key={id} type="button" className="filter-item" aria-pressed={state === id} onClick={() => setState(id)}>{label}</button>)}</div>
                  <p className="filter-label">主题</p>
                  <div className="filter-list" role="group" aria-label="主题筛选"><button type="button" className="filter-item" aria-pressed={!category} onClick={() => setCategory("")}>全部</button>{CATEGORIES.map(([id, label]) => <button key={id} type="button" className="filter-item" aria-pressed={category === id} onClick={() => setCategory(id)}>{label}</button>)}</div>
                  <p className="filter-label">来源</p>
                  <div className="filter-list" role="group" aria-label="来源筛选"><button type="button" className="filter-item" aria-pressed={!origin} onClick={() => setOrigin("")}>全部</button>{SOURCE_FILTERS.map(([id, label]) => <button key={id} type="button" className="filter-item" aria-pressed={origin === id} onClick={() => setOrigin(id)}>{label}</button>)}{extraOrigins.map(item => <button key={item.id} type="button" className="filter-item" aria-pressed={origin === item.id} onClick={() => setOrigin(item.id)}>{item.name}</button>)}</div>
                  {filtered && <button className="text-button filter-clear" type="button" onClick={clear}>清除筛选</button>}
                </Popover.Popup>
              </Popover.Positioner>
            </Popover.Portal>
          </Popover.Root>
          <Menu.Root modal={false}>
            <Menu.Trigger className={`list-tool${sort === "oldest" ? " on" : ""}`}>
              <Icon name="sort"/>排序: 创建时间<span className="list-tool-chevron"><Icon name="chevron"/></span>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Positioner className="tool-positioner" side="bottom" align="start" sideOffset={8}>
                <Menu.Popup className="select-popup tool-popup">
                  <Menu.RadioGroup value={sort} onValueChange={value => setSort(value === "oldest" ? "oldest" : "newest")}>
                    <Menu.RadioItem className="tool-option" value="newest" closeOnClick>新的在前</Menu.RadioItem>
                    <Menu.RadioItem className="tool-option" value="oldest" closeOnClick>旧的在前</Menu.RadioItem>
                  </Menu.RadioGroup>
                </Menu.Popup>
              </Menu.Positioner>
            </Menu.Portal>
          </Menu.Root>
        </div>
        <form className="library-search" onSubmit={e => { e.preventDefault(); onSearch() }}><Icon name="search"/><input aria-label="搜索记忆" placeholder="搜索已确认的记忆" value={draft} onChange={e => onDraft(e.target.value)}/>{(draft || query) && <button type="button" className="search-clear" aria-label="清除搜索" onClick={onClear}><Icon name="close"/></button>}</form>
      </div>
      <ResourceNotice resource={resource} pending={false}/>
      {resource.loading && !resource.data && !resource.error && <MemorySkeleton/>}
      {resource.data && !shown.length && !resource.loading && !resource.error && <Empty title={filtered ? "没有符合筛选条件的记忆" : "还没有已确认的记忆"} action={<button className="button secondary" onClick={filtered ? clear : onAdd}>{filtered ? "清除筛选" : "添加记忆"}</button>}>{filtered ? "换个关键词、主题或来源再试试。" : "从一条偏好、目标或近期事件开始。"}</Empty>}
      {!!shown.length && <div className="memo-list"><div className="memo-head" aria-hidden="true"><span>{partial ? <span className="memo-count">已显示 {shown.length} 条，共 {total} 条</span> : <>记忆 <span className="memo-count">{shown.length} 条</span></>}</span><span>主题</span><span>来源</span><span>时间</span></div>{shown.map(item => <MemoCard key={`${item.id}-${item.revision}`} memory={item} open={selectedId === item.id} onOpen={onOpen}/>)}{canMore && <button type="button" className="memo-more" disabled={moreLoading} aria-busy={moreLoading} onClick={loadMore}>{moreLoading ? "正在查看…" : sort === "oldest" ? "查看更新的" : "查看更早的"}</button>}{moreError && <p className="helper memo-more-error" role="alert">{moreError}</p>}</div>}
    </div>
  </div>
}
