import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react"
import { explain } from "./api"

export function useResource<T>(load: () => Promise<T>, keys: unknown[]) {
  const key = JSON.stringify(keys)
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [retry, setRetry] = useState(0)
  const [seen, setSeen] = useState(key)
  const sequence = useRef(0)
  if (seen !== key) {
    setSeen(key)
    setLoading(true)
    setError("")
  }
  useEffect(() => {
    const current = ++sequence.current
    setLoading(true); setError("")
    load().then(value => { if (current === sequence.current) setData(value) })
      .catch(err => { if (current === sequence.current) setError(explain(err)) })
      .finally(() => { if (current === sequence.current) setLoading(false) })
    return () => { sequence.current++ }
  }, [key, retry])
  return { data, loading, error, reload: useCallback(() => setRetry(n => n + 1), []) }
}
export function Skeleton({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return <span className={`skeleton ${className}`} style={style} />
}
export function ResourceNotice({ resource, pending = true }: { resource: { data: unknown; loading: boolean; error: string; reload: () => void }; pending?: boolean }) {
  const { data, loading, error, reload } = resource
  if (error) return <div className="notice error" role="alert"><div><strong>加载未完成</strong><p>{error}</p>{data !== null && <p>以下为上次加载的数据，操作前请重试。</p>}</div><button className="button secondary" onClick={reload}>重试加载</button></div>
  if (loading && (data !== null || pending)) return <div className="loading-line" role="status"><span className="spinner" />{data === null ? "正在加载…" : "正在更新，暂时展示上次加载的数据。"}</div>
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
    memories: <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></>,
    review: <><rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 5-6"/></>,
    agents: <><rect x="3" y="7" width="18" height="13" rx="3"/><path d="M12 3v4M7 12h.01M17 12h.01M8 16h8"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    filter: <path d="M4 6h16l-6.2 7.1V19l-3.6 1.8v-7.7Z"/>,
    sort: <><path d="M8 19V5M8 5 5 8M8 5l3 3"/><path d="M16 5v14M16 19l-3-3M16 19l3-3"/></>,
    chevron: <path d="m6 9 6 6 6-6"/>,
    plus: <path d="M12 5v14M5 12h14"/>, close: <path d="m6 6 12 12M6 18 18 6"/>,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></>,
    settings: <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></>,
  }
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.memories}</svg>
}
