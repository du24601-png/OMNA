import { Menu } from "@base-ui/react/menu"
import { Popover } from "@base-ui/react/popover"
import { useEffect, useRef, useState } from "react"
import { api, explain, type Memory, type Proposal } from "./api"
import { Detail, type DetailCommand } from "./Detail"
import { CATEGORIES, categoryLabel, dateLabel, listTime } from "./format"
import { ReviewPage } from "./Review"
import { canLeave, Empty, Icon, ResourceNotice, Skeleton, SourceMark, useResource } from "./ui"

export type Filter = string

const SOURCE_FILTERS: [string, string][] = [
  ["omna", "你"],
  ["workbuddy", "WorkBuddy"],
  ["zcode", "ZCode"],
  ["opencode", "OpenCode"],
  ["codex", "ChatGPT"],
  ["claude", "Claude"],
  ["claude-code", "Claude Code"],
]
const STATES: [string, string][] = [["current", "当前"], ["all", "全部"], ["expired", "过期"], ["history", "历史"]]
const ORDER = CATEGORIES.map(([id]) => id)

export function MemoriesPage({ tick, online, paused, filter, onFilter, onCompose, onSaved }: {
  tick: number
  online: boolean
  paused: boolean
  filter: Filter
  onFilter: (next: Filter) => void
  onCompose: (kind: "add" | "import") => void
  onSaved: () => void
}) {
  const [draft, setDraft] = useState("")
  const [query, setQuery] = useState("")
  const [state, setState] = useState("current")
  const [origin, setOrigin] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [command, setCommand] = useState<DetailCommand | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const feedRef = useRef<HTMLDivElement>(null)
  const pendingView = filter === "pending"
  const category = !pendingView && filter !== "all" ? filter : ""
  const searching = !!query && state === "current"
  const list = useResource(() => pendingView ? Promise.resolve(null) : api.memories({ state, category, origin, query, limit: searching ? 20 : 50 }), [filter, state, origin, query, tick])
  const proposals = useResource(() => api.proposals(), [tick])
  const totals = useResource(() => api.memories({ state: "current", limit: 1 }), [tick])
  const agents = useResource(() => api.agents(), [tick])
  const clients = useResource(() => api.agentClients(), [])
  const [extra, setExtra] = useState<Memory[]>([])
  const [moreCursor, setMoreCursor] = useState<string | null>(null)
  const [moreLoading, setMoreLoading] = useState(false)
  const [moreError, setMoreError] = useState("")
  const flight = useRef(0)
  const activeKey = useRef("")
  const pageKey = JSON.stringify([filter, state, origin, query, tick])
  activeKey.current = pageKey
  const [seenKey, setSeenKey] = useState(pageKey)
  if (seenKey !== pageKey) {
    setSeenKey(pageKey)
    setExtra([])
    setMoreCursor(null)
    setMoreError("")
    setMoreLoading(false)
    flight.current += 1
  }
  useEffect(() => {
    setExtra([])
    setMoreError("")
    setMoreCursor(list.data && !searching ? list.data.next_cursor ?? null : null)
  }, [list.data, searching])
  const first = list.data?.items ?? []
  const shown = [...first, ...extra]
  const grouped = filter === "all" && !searching
  const groups = grouped
    ? ORDER.map(id => ({ id, title: categoryLabel(id), items: shown.filter(item => item.category === id) })).filter(group => group.items.length)
    : [{ id: "flat", title: "", items: shown }]
  const ordered = groups.flatMap(group => group.items)
  const orderedKey = ordered.map(item => item.id).join("\n")
  const pending = proposals.data?.proposals ?? []
  const total = typeof totals.data?.total === "number" ? totals.data.total : null
  const agentCount = (agents.data?.agents ?? []).filter(agent => agent.enabled).length
  const selectedItem = ordered.find(item => item.id === selected)
  useEffect(() => {
    if (pendingView || !list.data) return
    if (!selected || !ordered.some(item => item.id === selected)) setSelected(ordered[0]?.id ?? null)
  }, [orderedKey, pendingView, list.data])
  function choose(id: string) {
    if (id === selected || canLeave()) setSelected(id)
  }
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && (event.key === "k" || event.key === "K")) {
        event.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select()
        return
      }
      const target = event.target as HTMLElement | null
      if (pendingView || target?.closest("input, textarea, select, [role=menu], [role=dialog]")) return
      if ((event.ctrlKey || event.metaKey) && (event.key === "d" || event.key === "D")) {
        if (!selected) return
        event.preventDefault()
        setCommand({ type: "share", n: Date.now() })
        return
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const index = ordered.findIndex(item => item.id === selected)
        const next = ordered[event.key === "ArrowDown" ? index + 1 : Math.max(0, index - 1)]
        if (!next) return
        event.preventDefault()
        choose(next.id)
        document.getElementById(`mem-${next.id}`)?.scrollIntoView({ block: "nearest" })
        return
      }
      if (event.key === "Enter" && selected && !target?.closest("button, a, summary")) {
        event.preventDefault()
        setCommand({ type: "edit", n: Date.now() })
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [orderedKey, selected, pendingView])
  const canMore = !searching && !list.loading && !!moreCursor
  function loadMore() {
    const cursor = moreCursor
    const key = pageKey
    if (!cursor || !canMore) return
    const token = ++flight.current
    setMoreLoading(true)
    setMoreError("")
    api.memories({ state, category, origin, query, limit: 50, cursor }).then(page => {
      if (token !== flight.current || activeKey.current !== key) return
      setExtra(rows => [...rows, ...page.items])
      setMoreCursor(page.next_cursor ?? null)
    }).catch(err => {
      if (token === flight.current) setMoreError(explain(err))
    }).finally(() => {
      if (token === flight.current) setMoreLoading(false)
    })
  }
  const narrowed = state !== "current" || !!origin
  const knownOrigins = new Set(SOURCE_FILTERS.map(([id]) => id))
  const extraOrigins = (list.data?.origins ?? []).filter(item => !knownOrigins.has(item.id))
  const narrowLabel = [state !== "current" ? STATES.find(([id]) => id === state)?.[1] : "", origin ? (SOURCE_FILTERS.find(([id]) => id === origin)?.[1] || extraOrigins.find(item => item.id === origin)?.name || "") : ""].filter(Boolean).join(" · ")
  const filteredOut = !!query || !!category || narrowed
  function clearAll() { setState("current"); setOrigin(""); setQuery(""); setDraft(""); onFilter("all") }
  function pick(next: Filter) { if (next === filter || canLeave()) onFilter(next) }
  return <div className={`mem-page${pendingView ? " pending-view" : ""}`}>
    <div className="mem-main">
      <div className="mem-search-row">
        <form className="mem-search" role="search" onSubmit={event => { event.preventDefault(); if (canLeave()) { setQuery(draft.trim()); if (pendingView) onFilter("all") } }}>
          <Icon name="search"/>
          <input ref={searchRef} aria-label="搜索记忆" placeholder="搜一下 AI 记得你什么" value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Escape") { setDraft(""); setQuery(""); event.currentTarget.blur() } }}/>
          {(draft || query) ? <button type="button" className="search-clear" aria-label="清除搜索" onClick={() => { setDraft(""); setQuery("") }}><Icon name="close"/></button> : <span className="kbd-hint" aria-hidden="true"><kbd>Ctrl</kbd><kbd>K</kbd></span>}
        </form>
        <Menu.Root>
          <Menu.Trigger className="button icon-only mem-add" aria-label="添加或导入"><Icon name="plus"/></Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className="tool-positioner" side="bottom" align="end" sideOffset={6}>
              <Menu.Popup className="select-popup">
                <Menu.Item className="tool-option" onClick={() => onCompose("add")}>添加记忆</Menu.Item>
                <Menu.Item className="tool-option" onClick={() => onCompose("import")}>导入文本或文件</Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <div className="mem-chips" role="group" aria-label="筛选">
        <button type="button" className="mem-chip" aria-pressed={filter === "all"} onClick={() => pick("all")}>全部{total !== null && <span>{total}</span>}</button>
        <button type="button" className="mem-chip" aria-pressed={pendingView} onClick={() => pick("pending")}>待确认{!!pending.length && <span className="hot">{pending.length}</span>}</button>
        {CATEGORIES.map(([id, label]) => <button key={id} type="button" className="mem-chip" aria-pressed={filter === id} onClick={() => pick(id)}>{label}</button>)}
        {!pendingView && <Popover.Root>
          <Popover.Trigger className={`mem-chip mem-narrow${narrowed ? " on" : ""}`} aria-label={narrowLabel ? `更多筛选，${narrowLabel}` : "更多筛选"}><Icon name="filter"/>{narrowLabel}</Popover.Trigger>
          <Popover.Portal>
            <Popover.Positioner className="tool-positioner" side="bottom" align="end" sideOffset={8}>
              <Popover.Popup className="select-popup tool-popup">
                <p className="filter-label">状态</p>
                <div className="filter-list" role="group" aria-label="记忆状态">{STATES.map(([id, label]) => <button key={id} type="button" className="filter-item" aria-pressed={state === id} onClick={() => setState(id)}>{label}</button>)}</div>
                <p className="filter-label">来源</p>
                <div className="filter-list" role="group" aria-label="来源筛选"><button type="button" className="filter-item" aria-pressed={!origin} onClick={() => setOrigin("")}>全部</button>{SOURCE_FILTERS.map(([id, label]) => <button key={id} type="button" className="filter-item" aria-pressed={origin === id} onClick={() => setOrigin(id)}>{label}</button>)}{extraOrigins.map(item => <button key={item.id} type="button" className="filter-item" aria-pressed={origin === item.id} onClick={() => setOrigin(item.id)}>{item.name}</button>)}</div>
                {narrowed && <button className="text-button filter-clear" type="button" onClick={() => { setState("current"); setOrigin("") }}>清除</button>}
              </Popover.Popup>
            </Popover.Positioner>
          </Popover.Portal>
        </Popover.Root>}
      </div>
      {pendingView
        ? <div className="mem-scroll"><ReviewPage tick={tick} online={online} onOpen={id => { onFilter("all"); setSelected(id) }} onSaved={onSaved}/></div>
        : <div className="mem-scroll" ref={feedRef}>
          {searching && <p className="helper mem-caption">搜索“{query}”，只含已确认的记忆{list.data?.truncated ? "，只列出最相关的 20 条" : ""}</p>}
          <ResourceNotice resource={list} pending={false}/>
          {list.loading && !list.data && !list.error && <ListSkeleton/>}
          {grouped && !!pending.length && <section className="mem-group" aria-label="待确认">
            <h2>待确认<span>{pending.length}</span></h2>
            {pending.slice(0, 5).map(item => <PendingRow key={item.id} proposal={item} onOpen={() => pick("pending")}/>)}
            {pending.length > 5 && <button type="button" className="mem-more" onClick={() => pick("pending")}>查看全部 {pending.length} 条</button>}
          </section>}
          {groups.map(group => !!group.items.length && <section className="mem-group" key={group.id} aria-label={group.title || "记忆"}>
            {group.title && <h2>{group.title}<span>{group.items.length}</span></h2>}
            {group.items.map(item => <MemoryRow key={`${item.id}-${item.revision}`} memory={item} selected={item.id === selected} onSelect={() => choose(item.id)}/>)}
          </section>)}
          {list.data && !shown.length && !list.loading && !list.error && <Empty title={filteredOut ? "没找到相关的记忆" : "还没有已确认的记忆"} action={<button className="button" onClick={filteredOut ? clearAll : () => onCompose("add")}>{filteredOut ? "清除筛选" : "添加记忆"}</button>}>{filteredOut ? "换个说法试试，或者直接告诉你的 Agent。" : "从一条偏好、目标或正在做的事开始。"}</Empty>}
          {canMore && <button type="button" className="mem-more" disabled={moreLoading} aria-busy={moreLoading} onClick={loadMore}>{moreLoading ? "正在查看…" : "查看更早的"}</button>}
          {moreError && <p className="helper mem-caption" role="alert">{moreError}</p>}
        </div>}
    </div>
    {!pendingView && (selectedItem
      ? <Detail key={selectedItem.id} memoryId={selectedItem.id} origin={selectedItem.origin} online={online} agents={agents.data?.agents ?? []} clients={clients.data?.clients ?? []} paused={paused} command={command} onSaved={onSaved} onDeleted={() => setSelected(null)}/>
      : <aside className="mem-detail mem-detail-empty"><p className="helper">{list.loading ? "" : "选一条记忆查看详情"}</p></aside>)}
    <footer className="mem-foot">
      <span>{total ?? 0} 条记忆 · {agentCount} 个 Agent · 都保存在这台电脑上</span>
      <span className="mem-keys">{pendingView
        ? <><span>选择 <kbd>↑</kbd><kbd>↓</kbd></span><span>记住 <kbd>Y</kbd></span><span>忽略 <kbd>N</kbd></span><span>编辑 <kbd>E</kbd></span></>
        : <><span>编辑 <kbd>Enter</kbd></span><span>停止共享 <kbd>Ctrl D</kbd></span><span>搜索 <kbd>Ctrl K</kbd></span></>}</span>
    </footer>
  </div>
}

function MemoryRow({ memory, selected, onSelect }: { memory: Memory; selected: boolean; onSelect: () => void }) {
  return <button id={`mem-${memory.id}`} type="button" className={`mem-row${selected ? " selected" : ""}`} aria-current={selected ? "true" : undefined} onClick={onSelect}>
    <span className="mem-row-text">{memory.content || "正文暂时无法读取。"}</span>
    {memory.share_enabled === false && <span className="mem-row-lock" title="仅自己可见" aria-label="仅自己可见"><Icon name="lock"/></span>}
    <SourceMark origin={memory.origin} compact/>
    <time dateTime={memory.created_at} title={dateLabel(memory.created_at)}>{listTime(memory.created_at)}</time>
  </button>
}

function PendingRow({ proposal, onOpen }: { proposal: Proposal; onOpen: () => void }) {
  const requester = proposal.requester || (proposal.source.kind === "agent_claim" ? { client: "agent", name: proposal.source.name || "Agent" } : { client: "omna", name: "OMNA" })
  return <button type="button" className="mem-row" onClick={onOpen}>
    <span className="mem-row-text">{proposal.payload.content}</span>
    <span className="mem-kind">{proposal.target_id ? "修改" : "新增"}</span>
    <SourceMark origin={requester} compact/>
  </button>
}

function ListSkeleton() {
  return <div role="status" aria-label="正在加载记忆">{["68%", "42%", "76%", "55%", "61%", "34%"].map((width, index) => <div className="mem-row skeleton-row" key={index}><Skeleton className="skeleton-sentence" style={{ width }}/></div>)}</div>
}
