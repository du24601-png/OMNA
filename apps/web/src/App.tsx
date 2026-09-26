import { useEffect, useRef, useState } from "react"
import { ApiError, api, explain, getCredential, setCredential, type Memory } from "./api"
import { Detail } from "./Detail"
import { CATEGORIES, categoryLabel, dateLabel, sharingLabel } from "./format"
import { canLeave, Empty, Icon, Notice, ResourceNotice, useResource } from "./ui"
import { Composer } from "./Composer"
import { ReviewPage } from "./Review"
import { AgentPage } from "./Agents"

type Page = "profile" | "memories" | "review" | "agents"
export type Service = { status: "checking" | "online" | "offline" | "error"; extractor: boolean | null; testMode: boolean; runtime?: { command: string[]; environment: Record<string, string> } }
const NAV: [Page, string][] = [["profile", "关于我"], ["memories", "记忆"], ["review", "待确认"], ["agents", "我的 Agent"]]
function pageFromHash(): Page { const page = location.hash.replace(/^#\/?/, ""); return ["memories", "review", "agents"].includes(page) ? page as Page : "profile" }

export function App() {
  const [authed, setAuthed] = useState(Boolean(getCredential()))
  const [page, setPage] = useState<Page>(pageFromHash)
  const [service, setService] = useState<Service>({ status: "checking", extractor: null, testMode: false })
  const [query, setQuery] = useState("")
  const [activeQuery, setActiveQuery] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [composer, setComposer] = useState<"add" | "import" | null>(null)
  const [tick, setTick] = useState(0)
  const [healthTick, setHealthTick] = useState(0)
  const previousHealth = useRef("")
  const main = useRef<HTMLElement>(null)
  const refresh = () => setTick(n => n + 1)
  useEffect(() => {
    const hash = () => { if (canLeave()) { setPage(pageFromHash()); setSelected(null) } else history.replaceState(null, "", page === "profile" ? "#/" : `#/${page}`) }
    window.addEventListener("hashchange", hash)
    return () => window.removeEventListener("hashchange", hash)
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
    if (next === page) return
    if (!canLeave()) return
    history.pushState(null, "", next === "profile" ? "#/" : `#/${next}`)
    setPage(next); setSelected(null); main.current?.scrollTo(0, 0)
  }
  const openMemory = (id: string) => { if (!selected || selected === id || canLeave()) setSelected(id) }
  const compose = (kind: "add" | "import") => { if (canLeave()) { setSelected(null); setComposer(kind) } }
  const retry = () => { setHealthTick(n => n + 1); refresh() }
  const online = service.status === "online"
  if (!authed) return <Gate onReady={() => setAuthed(true)}/>
  const serviceLabel = online ? "本地服务正常" : service.status === "checking" ? "正在检查本地服务" : service.status === "offline" ? "本地服务未运行" : "无法读取服务配置"
  return <div className="app-shell">
    <nav className="sidebar" aria-label="主导航">
      <a className="brand" href="#/" aria-label="知我" onClick={e => { e.preventDefault(); navigate("profile") }}><span className="brand-mark">知</span></a>
      <div className="nav-items">{NAV.map(([id, label]) => <button key={id} className={`nav-item ${page === id ? "active" : ""}`} aria-current={page === id ? "page" : undefined} onClick={() => navigate(id)}><Icon name={id}/><span>{label}</span></button>)}</div>
      <div className="sidebar-foot">
        <span className={`service-status ${online ? "online" : ""}`} title={serviceLabel}><i/>{service.status === "offline" ? "未运行" : service.status === "error" ? "异常" : ""}</span>
        {service.testMode && <span className="demo-label" title="合成演示数据 · 独立测试库">演示</span>}
        {!online && service.status !== "checking" && <button className="text-button" onClick={retry}>重试</button>}
      </div>
    </nav>
    <div className="app-body">
      {service.status === "offline" && <div className="global-notice" role="alert">本地服务未运行。已有内容为上次加载的数据，草稿仍保留；恢复连接后可继续操作。</div>}
      {service.status === "error" && <div className="global-notice" role="alert">无法读取本地服务配置，请重试或检查本机凭证。提取模型配置尚未确认。</div>}
      {online && service.extractor === false && <div className="model-notice">提取模型未配置，仍可手动添加和查看已有记忆。</div>}
      <div className={`workspace ${selected ? "has-detail" : ""}`}>
        <div className="workspace-scroll">
          <header className="topbar"><div className="top-actions"><button className="button secondary" onClick={() => compose("import")}>导入</button><button className="button primary" onClick={() => compose("add")}><Icon name="plus"/>添加记忆</button></div></header>
          <main className="main-content" ref={main} id="main-content">
            {page === "profile" && <ProfilePage tick={tick} onOpen={openMemory} onAdd={() => compose("add")} onImport={() => compose("import")}/>}
            {page === "memories" && <MemoryPage tick={tick} query={activeQuery} draft={query} onDraft={setQuery} onSearch={() => { if (canLeave()) { setActiveQuery(query.trim()); setSelected(null) } }} onClear={() => { setQuery(""); setActiveQuery("") }} onOpen={openMemory} onAdd={() => compose("add")}/>}
            {page === "review" && <ReviewPage tick={tick} online={online} onOpen={openMemory} onSaved={refresh}/>}
            {page === "agents" && <AgentPage tick={tick} online={online} runtime={service.runtime}/>}
          </main>
        </div>
        {selected && <Detail key={selected} memoryId={selected} online={online} onClose={() => setSelected(null)} onSaved={refresh}/>}
      </div>
    </div>
    {composer && <Composer kind={composer} service={service} onClose={() => setComposer(null)} onRefresh={refresh} onSaved={id => { setComposer(null); refresh(); if (id) setSelected(id) }} onReview={() => { setComposer(null); navigate("review"); refresh() }}/>} 
  </div>
}
function Gate({ onReady }: { onReady: () => void }) {
  const [value, setValue] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false)
  return <main className="gate"><form className="surface gate-card" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setCredential(value.trim()); try { await api.ownerHealth(); onReady() } catch (err) { setCredential(""); setError(explain(err)) } finally { setBusy(false) } }}><div className="brand-mark">知</div><h1>欢迎回到知我</h1><p className="helper">用本机凭证打开你的个人记忆。凭证仅保存在当前浏览器会话。</p><label className="field">本机凭证<input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} required/></label>{error && <Notice tone="error">{error}</Notice>}<button className="button primary" disabled={busy || !value.trim()}>{busy ? "正在验证…" : "进入我的空间"}</button></form></main>
}
function ProfilePage({ tick, onOpen, onAdd, onImport }: { tick: number; onOpen: (id: string) => void; onAdd: () => void; onImport: () => void }) {
  const resource = useResource(() => api.profile(), [tick])
  const data = resource.data
  const count = data?.groups.reduce((n, g) => n + g.cards.length, 0) || 0
  const empty = data && !count && !data.recent.length
  const filled = data?.groups.flatMap(group => group.cards) || []
  const vacant = data?.groups.filter(group => !group.cards.length) || []
  return <div className="page profile-page"><ResourceNotice resource={resource}/>
    {empty && !resource.loading && !resource.error ? <Empty title="从一条真实的记忆开始" action={<><button className="button primary" onClick={onAdd}>添加第一条记忆</button><button className="button secondary" onClick={onImport}>导入已有文本</button></>}>记录你的偏好、目标或正在做的事。只有你确认过的内容，才会出现在这里。</Empty> : data && <>
      <h1 className="display-line">此刻的你，<br/>由你自己定义。</h1>
      <p className="quiet-label profile-count">{count} 条已确认事实</p>
      <div className="card-wall">{filled.map(card => <MemoryCard key={card.id} memory={card} onOpen={onOpen}/>)}</div>
      {vacant.length > 0 && <div className="empty-categories">{vacant.map(group => <button key={group.id} className="empty-line" onClick={onAdd}>{group.title}还是空的</button>)}</div>}
      {data.recent.length > 0 && <section className="recent-section"><h2>近期变化</h2><div className="card-wall">{data.recent.map(card => <MemoryCard key={card.id} memory={card} onOpen={onOpen}/>)}</div></section>}
    </>}
  </div>
}
function MemoryCard({ memory, onOpen }: { memory: Memory; onOpen: (id: string) => void }) {
  return <button className="memory-card" onClick={() => onOpen(memory.id)}><span className="card-chip">{categoryLabel(memory.category)}</span><p>{memory.content || "正文暂时无法读取。"}</p><span className="card-caption">{memory.scope && <span>{memory.scope}</span>}<span>{sharingLabel(memory)}</span><span>版本 {memory.revision}</span><time>{dateLabel(memory.created_at)}</time></span></button>
}
function MemoryPage({ tick, query, draft, onDraft, onSearch, onClear, onOpen, onAdd }: { tick: number; query: string; draft: string; onDraft: (value: string) => void; onSearch: () => void; onClear: () => void; onOpen: (id: string) => void; onAdd: () => void }) {
  const [state, setState] = useState("current"), [category, setCategory] = useState("")
  const resource = useResource(() => api.memories({ state, category, query, limit: 50 }), [state, category, query, tick])
  const filtered = !!query || !!category || state !== "current"
  const clear = () => { setState("current"); setCategory(""); onClear() }
  return <div className="page library-page"><form className="library-search" onSubmit={e => { e.preventDefault(); onSearch() }}><Icon name="search"/><input aria-label="搜索记忆" placeholder="搜索已确认的记忆" value={draft} onChange={e => onDraft(e.target.value)}/></form><div className="filter-bar"><div className="segmented" aria-label="记忆状态">{[["current", "当前"], ["expired", "过期"], ["history", "历史"]].map(([id, label]) => <button key={id} aria-pressed={state === id} onClick={() => setState(id)}>{label}</button>)}</div><select aria-label="主题筛选" value={category} onChange={e => setCategory(e.target.value)}><option value="">全部主题</option>{CATEGORIES.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select>{filtered && <button className="text-button" onClick={clear}>清除筛选</button>}<span className="quiet-label">{resource.data ? `${resource.data.items.length} 条` : ""}</span></div>{query && <p className="helper search-caption">搜索“{query}”，仅包含已确认内容</p>}<ResourceNotice resource={resource}/>
    {resource.data && !resource.data.items.length && !resource.loading && !resource.error && <Empty title={filtered ? "没有符合筛选条件的记忆" : "还没有已确认的记忆"} action={<button className="button secondary" onClick={filtered ? clear : onAdd}>{filtered ? "清除筛选" : "添加记忆"}</button>}>{filtered ? "换个关键词或类别再试试。" : "从一条偏好、目标或近期事件开始。"}</Empty>}
    {!!resource.data?.items.length && <div className="card-wall">{resource.data.items.map(item => <MemoryCard key={`${item.id}-${item.revision}`} memory={item} onOpen={onOpen}/>)}</div>}
  </div>
}
