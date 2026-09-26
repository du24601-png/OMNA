import { Menu } from "@base-ui/react/menu"
import { Popover } from "@base-ui/react/popover"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"
import { ApiError, api, explain, getCredential, setCredential, type Memory } from "./api"
import { Detail } from "./Detail"
import { CATEGORIES, categoryLabel, dateLabel, listTime, sharingLabel, sourceAsset } from "./format"
import { canLeave, Empty, Icon, Notice, ResourceNotice, Skeleton, useResource } from "./ui"
import { Composer } from "./Composer"
import { ReviewPage } from "./Review"
import { AgentPage } from "./Agents"
import { SettingsDialog } from "./Settings"
import { BrandLockup, BrandMark } from "./Brand"

type Page = "profile" | "memories" | "review" | "agents"
export type Service = { status: "checking" | "online" | "offline" | "error"; extractor: boolean | null; testMode: boolean; runtime?: { command: string[]; environment: Record<string, string> } }
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
  const [morph, setMorph] = useState<{ layoutId?: string; seed?: string }>({})
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
        setService({ status: "online", extractor: health.extractor_configured, testMode: health.test_mode, runtime: health.mcp_runtime })
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
  const openMemory = (id: string, layoutId?: string, seed?: string) => { if (!selected || selected === id || canLeave()) { setSelected(id); setMorph({ layoutId, seed }) } }
  const closeMemory = () => { setSelected(null); setMorph({}) }
  const compose = (kind: "add" | "import") => { if (canLeave()) { closeMemory(); setComposer(kind) } }
  const retry = () => { setHealthTick(n => n + 1); refresh() }
  const online = service.status === "online"
  if (!authed) return <Gate onReady={() => setAuthed(true)}/>
  const serviceLabel = online ? "本地服务正常" : service.status === "checking" ? "正在检查本地服务" : service.status === "offline" ? "本地服务未运行" : "无法读取服务配置"
  return <div className="app-shell">
    <DockNav page={page} settingsOpen={settingsOpen} onNavigate={navigate} onOpenSettings={openSettings}/>
    <div className="app-body">
      {service.status === "offline" && <div className="global-notice" role="alert">本地服务未运行。已有内容为上次加载的数据，草稿仍保留；恢复连接后可继续操作。<button className="text-button" onClick={retry}>重试</button></div>}
      {service.status === "error" && <div className="global-notice" role="alert">无法读取本地服务配置，请重试或检查本机凭证。提取模型配置尚未确认。<button className="text-button" onClick={retry}>重试</button></div>}
      {online && service.extractor === false && <div className="model-notice">提取模型未配置，仍可手动添加和查看已有记忆。</div>}
      <div className="workspace">
        <div className="workspace-scroll">
          <main className="main-content" ref={main} id="main-content">
            {page === "profile" && <ProfilePage tick={tick} service={service} serviceLabel={serviceLabel} onRetry={retry} openLayout={selected ? morph.layoutId : undefined} onOpen={openMemory} onAdd={() => compose("add")} onImport={() => compose("import")}/>}
            {page === "memories" && <MemoryPage tick={tick} openLayout={selected ? morph.layoutId : undefined} query={activeQuery} draft={query} onDraft={setQuery} onSearch={() => { if (canLeave()) { setActiveQuery(query.trim()); closeMemory() } }} onClear={() => { setQuery(""); setActiveQuery("") }} onOpen={openMemory} onAdd={() => compose("add")}/>}
            {page === "review" && <ReviewPage tick={tick} online={online} onOpen={openMemory} onSaved={refresh}/>}
            {page === "agents" && <AgentPage tick={tick} online={online} runtime={service.runtime}/>}
          </main>
        </div>
        {selected && <Detail key={selected} memoryId={selected} layoutId={morph.layoutId} seed={morph.seed} online={online} onClose={closeMemory} onSaved={refresh}/>}
      </div>
    </div>
    {composer && <Composer kind={composer} service={service} onClose={() => setComposer(null)} onRefresh={refresh} onSaved={id => { setComposer(null); refresh(); if (id) setSelected(id) }} onReview={() => { setComposer(null); navigate("review"); refresh() }}/>}
    {settingsOpen && <SettingsDialog onClose={closeSettings} onChanged={retry}/>}
  </div>
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
  return <main className="gate"><form className="surface gate-card" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setCredential(value.trim()); try { await api.ownerHealth(); onReady() } catch (err) { setCredential(""); setError(explain(err)) } finally { setBusy(false) } }}><BrandMark/><h1>欢迎回到知我</h1><p className="helper">用本机凭证打开你的个人记忆。凭证仅保存在当前浏览器会话。</p><label className="field">本机凭证<input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} required/></label>{error && <Notice tone="error">{error}</Notice>}<button className="button primary" disabled={busy || !value.trim()}>{busy ? "正在验证…" : "进入我的空间"}</button></form></main>
}
function ProfileSkeleton() {
  const cards = [["100%", "88%", "46%"], ["94%", "62%"], ["100%", "80%", "36%"], ["78%", "52%"]]
  return <div className="profile-skeleton" role="status" aria-label="正在加载关于我">
    <Skeleton className="skeleton-count"/>
    <div className="card-wall">{cards.map((lines, index) => <div className="memory-card skeleton-card" key={index}><Skeleton className="skeleton-chip"/>{lines.map((width, line) => <Skeleton key={line} className="skeleton-line" style={{ width }}/>)}<span className="card-caption"><Skeleton className="skeleton-meta"/><Skeleton className="skeleton-meta short"/></span></div>)}</div>
  </div>
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
        <button className="button secondary" onClick={onImport}>导入</button>
        <button className="button primary" onClick={onAdd}><Icon name="plus"/>添加记忆</button>
      </div>
    </div>
    <ResourceNotice resource={resource} pending={false}/>
    {resource.loading && !data && !resource.error && <ProfileSkeleton/>}
    {empty && !resource.loading && !resource.error ? <Empty title="从一条真实的记忆开始" action={<><button className="button primary" onClick={onAdd}>添加第一条记忆</button><button className="button secondary" onClick={onImport}>导入已有文本</button></>}>记录你的偏好、目标或正在做的事。只有你确认过的内容，才会出现在这里。</Empty> : data && <>
      <p className="quiet-label profile-count">{count} 条已确认事实</p>
      <div className="card-wall">{filled.map(card => <MemoryCard key={card.id} memory={card} layoutId={`memory-${card.id}`} openLayout={openLayout} onOpen={onOpen}/>)}{vacant.map(group => <button key={group.id} className="memory-card memory-card-empty" onClick={onAdd}><span className="card-chip">{group.title}</span><p>{group.title}还是空的</p></button>)}</div>
      {data.recent.length > 0 && <section className="recent-section"><h2>近期变化</h2><div className="card-wall">{data.recent.map(card => <MemoryCard key={`${card.id}-recent`} memory={card} layoutId={`memory-${card.id}-recent`} openLayout={openLayout} onOpen={onOpen}/>)}</div></section>}
    </>}
  </div>
}
const morphSpring = { type: "spring" as const, stiffness: 200, damping: 24 }
function MemoryCard({ memory, layoutId, openLayout, onOpen }: { memory: Memory; layoutId: string; openLayout?: string; onOpen: (id: string, layoutId?: string, seed?: string) => void }) {
  const reduce = useReducedMotion()
  const source = openLayout === layoutId
  return <motion.button type="button" layoutId={reduce ? undefined : layoutId} className="memory-card" data-morph-source={source || undefined} style={{ borderRadius: 12 }} transition={morphSpring} aria-expanded={source} onClick={() => onOpen(memory.id, layoutId, memory.content || undefined)}><span className="card-chip">{categoryLabel(memory.category)}</span><p>{memory.content || "正文暂时无法读取。"}</p><span className="card-caption">{memory.scope && <span>{memory.scope}</span>}<span className="card-share">{sharingLabel(memory)}</span><span>版本 {memory.revision}</span><time>{dateLabel(memory.created_at)}</time></span></motion.button>
}
const SOURCE_CLIENTS = new Set(["omna", "workbuddy", "zcode", "opencode", "codex", "claude", "claude-code"])
const SOURCE_DUAL = new Set(["omna", "opencode", "zcode", "codex"])
function SourceMark({ origin }: { origin?: { client: string; name: string } }) {
  const client = origin?.client || "omna"
  const name = origin?.name || "OMNA"
  if (!SOURCE_CLIENTS.has(client)) {
    const mark = name === "Agent 提案" ? "" : Array.from(name)[0]
    return <span className="memo-source"><span className="source-fallback" aria-hidden="true">{mark}</span><span>{name}</span></span>
  }
  const dual = SOURCE_DUAL.has(client)
  return <span className="memo-source"><img className={dual ? "source-mark light dual" : "source-mark"} src={sourceAsset(client)} alt="" />{dual && <img className="source-mark dark" src={sourceAsset(client, true)} alt="" />}<span>{name}</span></span>
}
function MemoCard({ memory, layoutId, openLayout, onOpen }: { memory: Memory; layoutId: string; openLayout?: string; onOpen: (id: string, layoutId?: string, seed?: string) => void }) {
  const privateOnly = memory.share_enabled === false
  const revised = memory.revision > 1
  const reduce = useReducedMotion()
  const source = openLayout === layoutId
  return <motion.button type="button" layoutId={reduce ? undefined : layoutId} className="memo-card" data-morph-source={source || undefined} style={{ borderRadius: 0 }} transition={morphSpring} aria-expanded={source} onClick={() => onOpen(memory.id, layoutId, memory.content || undefined)}>
    <span className="memo-sentence">{memory.content || "正文暂时无法读取。"}</span>
    <span className="memo-topic"><span className="memo-cat">{categoryLabel(memory.category)}</span>{memory.scope && <span className="memo-cat">{memory.scope}</span>}{privateOnly && <span className="memo-note">仅自己可见</span>}{revised && <span className="memo-note">版本 {memory.revision}</span>}</span>
    <SourceMark origin={memory.origin}/>
    <time dateTime={memory.created_at} title={dateLabel(memory.created_at)}>{listTime(memory.created_at)}</time>
  </motion.button>
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
function MemoryPage({ tick, openLayout, query, draft, onDraft, onSearch, onClear, onOpen, onAdd }: { tick: number; openLayout?: string; query: string; draft: string; onDraft: (value: string) => void; onSearch: () => void; onClear: () => void; onOpen: (id: string, layoutId?: string, seed?: string) => void; onAdd: () => void }) {
  const [state, setState] = useState("current"), [category, setCategory] = useState(""), [origin, setOrigin] = useState(""), [sort, setSort] = useState<"newest" | "oldest">("newest")
  const resource = useResource(() => api.memories({ state, category, origin, query, limit: 50 }), [state, category, origin, query, tick])
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
  const shown = (resource.data?.items ?? []).slice().sort((a, b) => {
    const left = a.created_at || "", right = b.created_at || ""
    if (left === right) return a.id < b.id ? -1 : 1
    const newerFirst = left > right ? -1 : 1
    return sort === "newest" ? newerFirst : -newerFirst
  })
  return <div className="page library-page">
    <div className="library-feed">
      {query && <p className="helper search-caption">搜索“{query}”，仅包含已确认内容</p>}
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
      {!!shown.length && <div className="memo-list"><div className="memo-head" aria-hidden="true"><span>记忆 <span className="memo-count">{shown.length} 条</span></span><span>主题</span><span>来源</span><span>时间</span></div>{shown.map(item => <MemoCard key={`${item.id}-${item.revision}`} memory={item} layoutId={`memo-${item.id}-${item.revision}`} openLayout={openLayout} onOpen={onOpen}/>)}</div>}
    </div>
  </div>
}
