import { t, useMessage, useLocale, formatLocale } from "./i18n"
import { useCallback, useEffect, useRef, useState } from "react"
import { api, explain, type Memory, type Proposal, type TrayStatus } from "./api"
import { categoryLabel } from "./format"
import { classify } from "./Review"
import { SourceMark } from "./ui"
import { BrandMark } from "./Brand"

const POLL_MS = 4000

function requesterOf(item: Proposal) {
  return item.requester || (item.source.kind === "agent_claim" ? { client: "agent", name: item.source.name || "Agent" } : { client: "omna", name: "OMNA" })
}

function ago(value: string) {
  const time = new Date(value).getTime()
  const minutes = Math.floor((Date.now() - time) / 60000)
  if (!Number.isFinite(time)) return ""
  if (minutes < 1) return t("刚刚")
  if (minutes < 60) return t("{0} 分钟前", [minutes])
  const date = new Date(time)
  return new Intl.DateTimeFormat(formatLocale(), { hour: "numeric", minute: "2-digit" }).format(date)
}

function minutesLeft(until: string | null) {
  if (!until) return 0
  return Math.max(1, Math.ceil((new Date(until).getTime() - Date.now()) / 60000))
}

// Three rows; a suggestion a notification pointed at takes the last row when
// it is further down, so the flyout shows and selects the one it was opened for.
export function visibleRows<T extends { id: string }>(shown: T[], pinned: string | null) {
  const first = shown.slice(0, 3)
  const target = pinned ? shown.find(item => item.id === pinned) : undefined
  return target && !first.includes(target) ? [...first.slice(0, 2), target] : first
}

export function Flyout() {
  useLocale()
  const [status, setStatus] = useState<TrayStatus | null>(null)
  const [pending, setPending] = useState<Proposal[] | null>(null)
  const [library, setLibrary] = useState<Memory[] | null>(null)
  const [hidden, setHidden] = useState<string[]>([])
  const [focus, setFocus] = useState<string | null>(null)
  // The suggestion a notification pointed at; shown even when it is not among the first three.
  const [pinned, setPinned] = useState<string | null>(null)
  const [locating, setLocating] = useState(false)
  const requestedTarget = useRef<string | null>(null)
  const loadVersion = useRef(0)
  const loading = useRef(false)
  const listRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useMessage()
  const [offline, setOffline] = useState(false)
  const [busy, setBusy] = useState(false)
  const keys = useRef(new Map<string, string>())
  const load = useCallback(async (force = false) => {
    if (loading.current && !force) return
    const version = ++loadVersion.current
    loading.current = true
    try {
      const [nextStatus, proposals, memories] = await Promise.all([api.status(), api.proposals(), api.memories({ state: "current", limit: 50 })])
      if (version !== loadVersion.current) return
      setStatus(nextStatus)
      setPending(proposals.proposals)
      setLibrary(memories.items)
      setHidden(old => old.filter(id => proposals.proposals.some(item => item.id === id)))
      setOffline(false)
      const target = requestedTarget.current
      if (target) {
        requestedTarget.current = null
        setLocating(false)
        setFocus(target)
        if (proposals.proposals.some(item => item.id === target)) setError("")
        else {
          setPinned(null)
          setError(() => (t("这条建议已处理或不存在。请选择其他建议，或打开主窗口核对。")))
        }
      }
    } catch (err) {
      if (version !== loadVersion.current) return
      setOffline(true)
      setError(() => (explain(err)))
    } finally {
      if (version === loadVersion.current) loading.current = false
    }
  }, [])
  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load() }, POLL_MS)
    const stop = window.omna?.onFlyoutShown?.(id => {
      setError("")
      requestedTarget.current = id || null
      setLocating(!!id)
      setFocus(id || null)
      setPinned(id || null)
      // Keys act only while the list has focus, so give it focus on every show.
      listRef.current?.focus()
      void load(true)
    })
    return () => { ++loadVersion.current; loading.current = false; clearInterval(timer); stop?.() }
  }, [load])
  const shown = (pending ?? []).filter(item => !hidden.includes(item.id))
  const visible = visibleRows(shown, pinned)
  const current = locating ? undefined : focus ? visible.find(item => item.id === focus) : visible[0]
  const ready = library !== null
  const lane = (item: Proposal) => classify(item, item.payload.content.trim(), library ?? [], ready)
  async function decide(item: Proposal, decision: "accept" | "reject") {
    if (busy || locating || requestedTarget.current) return
    setError("")
    setHidden(old => [...old, item.id])
    setFocus(null)
    setBusy(true)
    const keyName = `${item.id}:${decision}`
    let key = keys.current.get(keyName)
    if (!key) { key = crypto.randomUUID(); keys.current.set(keyName, key) }
    try {
      const body = decision === "accept" ? { decision, content: item.payload.content } : { decision }
      const result = await api.decide(item.id, body, key)
      if (result.status !== "accepted" && result.status !== "rejected") throw new Error(t("服务没有确认结果，请到主窗口核对。"))
      void load(true)
    } catch (err) {
      setHidden(old => old.filter(id => id !== item.id))
      setFocus(item.id)
      setError(() => (explain(err)))
    } finally {
      setBusy(false)
    }
  }
  async function togglePause() {
    if (!status) return
    setError("")
    try {
      const sharing = status.sharing.paused ? await api.resumeSharing() : await api.pauseSharing()
      setStatus({ ...status, sharing })
    } catch (err) {
      setError(() => (explain(err)))
    }
  }
  useEffect(() => {
    // Escape closes from anywhere; the list keys live on the list itself.
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.ctrlKey && !event.metaKey && !event.altKey) window.omna?.flyout?.hide()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  function onListKey(event: React.KeyboardEvent) {
    if (event.target !== event.currentTarget || event.ctrlKey || event.metaKey || event.altKey || locating || requestedTarget.current) return
    const index = visible.findIndex(item => item.id === current?.id)
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const next = visible[event.key === "ArrowDown" ? index + 1 : index - 1]
      if (next) { event.preventDefault(); setFocus(next.id) }
      return
    }
    if (!current) return
    if ((event.key === "y" || event.key === "Y") && lane(current) === "quick") { event.preventDefault(); void decide(current, "accept") }
    if (event.key === "n" || event.key === "N") { event.preventDefault(); void decide(current, "reject") }
  }
  const openMain = (route = "") => window.omna?.flyout?.openMain(route)
  const paused = !!status?.sharing.paused
  const reads = status?.reads_today
  return <div className="flyout" role="dialog" aria-label="OMNA">
    <header className="flyout-head">
      <span className="flyout-brand"><BrandMark className="flyout-mark"/><b>OMNA</b></span>
      <span className={`flyout-state ${offline ? "off" : paused ? "paused" : ""}`}><i/>{offline ? t("没连上本机服务") : paused ? t("已暂停共享") : t("运行中")}</span>
      <span className="flyout-fill"/>
      <button type="button" className="flyout-icon" onClick={togglePause} disabled={!status} aria-label={paused ? t("恢复共享") : t("暂停共享 1 小时")} title={paused ? t("恢复共享") : t("暂停共享 1 小时")}>{paused
        ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z"/></svg>
        : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M9 6v12M15 6v12"/></svg>}</button>
      <button type="button" className="flyout-icon" onClick={() => openMain()} aria-label={t("打开主窗口")} title={t("打开主窗口")}><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-8 8M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg></button>
    </header>
    {paused
      ? <div className="flyout-card warn"><strong>{t("已暂停共享")}</strong><p>{t("所有 Agent 暂时读不到你的记忆，也不能提议，")}{minutesLeft(status?.sharing.paused_until ?? null)} {t("分钟后自动恢复。")}</p></div>
      : <div className="flyout-card"><div><strong>{reads ? t("今天被读取 {0} 次", [reads.total]) : t("今天被读取 — 次")}</strong><p>{reads && reads.agents.length ? reads.agents.map(agent => `${agent.name} ${agent.count}`).join(" · ") : t("今天还没有 Agent 读取")}</p></div></div>}
    <div className="flyout-section"><span>{t("待确认")} {shown.length || ""}</span>{shown.length > 0 && <button type="button" className="flyout-link" onClick={() => openMain("review")}>{t("全部")}</button>}</div>
    <div className="flyout-list" ref={listRef} tabIndex={0} role="group" aria-label={t("待确认：↑↓ 选择，Y 记住，N 忽略")} onKeyDown={onListKey}>
      {locating && <p className="flyout-empty" role="status">{t("正在定位这条建议…")}</p>}
      {pending === null && !offline && <p className="flyout-empty">{t("正在载入…")}</p>}
      {pending !== null && !shown.length && <p className="flyout-empty"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>{t("没有要你确认的")}</p>}
      {visible.map(item => {
        const quick = lane(item) === "quick"
        const update = !!item.target_id
        return <div key={item.id} className={`flyout-row${item.id === current?.id ? " focused" : ""}`} onMouseEnter={() => { if (!locating) setFocus(item.id) }}>
          <SourceMark origin={requesterOf(item)} compact/>
          <div className="flyout-copy"><p>{item.payload.content}</p><small>{requesterOf(item).name} · {update ? t("修改") : t("新增")}{categoryLabel(item.payload.category)}</small></div>
          {quick
            ? <><button type="button" className="flyout-no" disabled={busy || locating} aria-label={t("忽略")} title={t("忽略 (N)")} onClick={() => void decide(item, "reject")}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button><button type="button" className="flyout-ok" disabled={busy || locating} aria-label={t("记住")} title={t("记住 (Y)")} onClick={() => void decide(item, "accept")}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></button></>
            : <button type="button" className="flyout-more" onClick={() => openMain("review")}>{update ? t("看差异") : t("去确认")}</button>}
        </div>
      })}
    </div>
    {shown.length > 0 && <div className="flyout-keys"><span><kbd>↑↓</kbd> {t("选择")}</span><span><kbd>Y</kbd> {t("记住")}</span><span><kbd>N</kbd> {t("忽略")}</span></div>}
    {error && <p className="flyout-error" role="alert">{error}</p>}
    <div className="flyout-section"><span>{t("最近")}</span></div>
    <ul className="flyout-recent">
      {(status?.recent_reads ?? []).map((read, index) => <li key={read.event_id}><i className={index === 0 && Date.now() - new Date(read.at).getTime() < 60000 ? "live" : ""}/><span><b>{read.agent_name}</b> {t("读了")}{Object.entries(read.categories).map(([key, count]) => `${categoryLabel(key)} ${count}`).join(" · ") || t(" {0} 条", [read.count])}</span><time>{ago(read.at)}</time></li>)}
      {status && !status.recent_reads.length && <li className="quiet">{t("还没有 Agent 读取过")}</li>}
    </ul>
    <footer className="flyout-foot">
      <button type="button" className="flyout-open" onClick={() => openMain()}>{t("打开 OMNA")}<span><kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>M</kbd></span></button>
      <button type="button" className="flyout-icon" aria-label={t("设置")} title={t("设置")} onClick={() => openMain("settings")}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/></svg></button>
    </footer>
  </div>
}
