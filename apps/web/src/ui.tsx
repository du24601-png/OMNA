import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from "react"
import { explain } from "./api"

export function useResource<T>(load: () => Promise<T>, keys: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [retry, setRetry] = useState(0)
  const sequence = useRef(0)
  useEffect(() => {
    const current = ++sequence.current
    setLoading(true); setError("")
    load().then(value => { if (current === sequence.current) setData(value) })
      .catch(err => { if (current === sequence.current) setError(explain(err)) })
      .finally(() => { if (current === sequence.current) setLoading(false) })
    return () => { sequence.current++ }
  }, [...keys, retry])
  return { data, loading, error, reload: useCallback(() => setRetry(n => n + 1), []) }
}
export function ResourceNotice({ resource }: { resource: { data: unknown; loading: boolean; error: string; reload: () => void } }) {
  const { data, loading, error, reload } = resource
  if (error) return <div className="notice error" role="alert"><div><strong>加载未完成</strong><p>{error}</p>{data !== null && <p>以下为上次加载的数据，操作前请重试。</p>}</div><button className="button secondary" onClick={reload}>重试加载</button></div>
  if (loading) return <div className="loading-line" role="status"><span className="spinner" />{data === null ? "正在加载…" : "正在更新，暂时展示上次加载的数据。"}</div>
  return null
}
export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "error" | "success" | "warning" }) {
  return <div className={`notice ${tone}`} role={tone === "error" ? "alert" : "status"}>{children}</div>
}
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-symbol" aria-hidden="true">◎</span><h2>{title}</h2>{children && <p>{children}</p>}{action && <div className="actions">{action}</div>}</div>
}
export function PageTitle({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <div className="page-heading"><div><h1>{title}</h1><p>{description}</p></div>{children}</div>
}
export function canLeave() { return window.dispatchEvent(new Event("zhiwo:leave", { cancelable: true })) }
export function useUnsaved(dirty: boolean, busy = false) {
  useEffect(() => {
    const check = (event: Event) => { if (busy || (dirty && !window.confirm("有尚未保存的修改。离开将放弃这些修改，确定离开吗？"))) event.preventDefault() }
    const unload = (event: BeforeUnloadEvent) => { if (dirty || busy) { event.preventDefault(); event.returnValue = "" } }
    window.addEventListener("zhiwo:leave", check); window.addEventListener("beforeunload", unload)
    return () => { window.removeEventListener("zhiwo:leave", check); window.removeEventListener("beforeunload", unload) }
  }, [dirty, busy])
}
export function usePanel(ref: RefObject<HTMLElement | null>, close: () => void, modal = true) {
  const closeRef = useRef(close); closeRef.current = close
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>("button, input, textarea, select, [tabindex]")?.focus()
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return }
      if (event.key !== "Tab" || (!modal && window.innerWidth > 1200)) return
      const nodes = Array.from(ref.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex='0']") || []).filter(el => el.getClientRects().length)
      if (!nodes.length) return
      const first = nodes[0], last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener("keydown", keyboard)
    return () => { document.removeEventListener("keydown", keyboard); if (previous?.isConnected) previous.focus() }
  }, [ref, modal])
}
// No icon library is installed in the existing app; use a small consistent line set.
export function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    profile: <><circle cx="12" cy="8" r="3.5"/><path d="M5 20v-2a7 7 0 0 1 14 0v2"/></>,
    memories: <><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/></>,
    review: <><rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 5-6"/></>,
    agents: <><rect x="3" y="7" width="18" height="13" rx="3"/><path d="M12 3v4M7 12h.01M17 12h.01M8 16h8"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    plus: <path d="M12 5v14M5 12h14"/>, close: <path d="m6 6 12 12M6 18 18 6"/>,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></>,
  }
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.memories}</svg>
}
