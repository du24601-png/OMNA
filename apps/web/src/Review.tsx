import { t, useMessage } from "./i18n"
import { useEffect, useMemo, useRef, useState } from "react"
import { ApiError, api, explain, type Memory, type Proposal } from "./api"
import { BatchBar } from "./BatchBar"
import { categoryLabel } from "./format"
import { canLeave, Empty, Icon, ResourceNotice, Skeleton, SourceMark, useResource, useUnsaved } from "./ui"

type Lane = "quick" | "update" | "similar" | "long" | "evidence" | "unchecked"
type Command = { type: "edit" | "ignore" | "remember"; n: number; id: string }

export function ReviewPage({ tick, online, onOpen, onSaved }: { tick: number; online: boolean; onOpen: (id: string) => void; onSaved: () => void }) {
  const resource = useResource(() => api.proposals(), [tick])
  const memories = useResource(() => api.memories({ state: "current", limit: 50 }), [tick])
  const [selected, setSelected] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [finished, setFinished] = useState<string[]>([])
  const [notice, setNotice] = useState<{ text: string; id?: string } | null>(null)
  const [command, setCommand] = useState<Command | null>(null)
  const live = online && !resource.error
  const library = memories.data?.items ?? []
  const libraryReady = memories.data !== null && !memories.error
  const pending = (resource.data?.proposals ?? []).filter(item => !finished.includes(item.id))
  const empty = !!resource.data && !pending.length && !resource.error
  const shownKey = pending.map(item => item.id).join("\n")
  const currentId = pending.some(item => item.id === selected) ? selected : pending[0]?.id ?? null
  useEffect(() => {
    if (currentId !== selected) setSelected(currentId)
  }, [shownKey, currentId])
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target?.closest("input, textarea, select")) return
      if (event.ctrlKey || event.metaKey || event.altKey) return
      const index = pending.findIndex(item => item.id === currentId)
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const next = pending[event.key === "ArrowDown" ? index + 1 : index - 1]
        if (!next || !canLeave()) return
        event.preventDefault()
        setSelected(next.id)
        setOpenId(current => current === next.id ? current : null)
        return
      }
      if (target?.closest("button")) return
      if (!currentId || !live) return
      if (event.key === "e" || event.key === "E") { setOpenId(currentId); issue("edit") }
      else if (event.key === "Backspace" || event.key === "x" || event.key === "X" || event.key === "n" || event.key === "N") issue("ignore")
      else if (event.key === "Enter" || event.key === "y" || event.key === "Y") issue("remember")
      function issue(type: Command["type"]) {
        event.preventDefault()
        setCommand({ type, n: Date.now(), id: currentId! })
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [shownKey, currentId, live])
  function choose(id: string, toggle = false) {
    if ((id !== currentId || (toggle && openId === id)) && !canLeave()) return false
    setSelected(id)
    setOpenId(toggle && openId === id ? null : id)
    return true
  }
  function finish(id: string, result: { text: string; id?: string }) {
    const index = pending.findIndex(item => item.id === id)
    const next = pending[index + 1] || pending[index - 1]
    setFinished(old => old.includes(id) ? old : [...old, id])
    setSelected(next && next.id !== id ? next.id : null)
    setOpenId(next && next.id !== id ? next.id : null)
    setNotice(result)
    resource.reload()
    onSaved()
  }
  if (resource.loading && !resource.data && !resource.error) return <InboxSkeleton />
  return <div className={`page inbox-page${empty ? " inbox-empty" : ""}`}>
    <ResourceNotice resource={resource} pending={false} />
    {memories.error && <div className="notice error inbox-note" role="alert"><div><strong>{t("还没核对已有记忆")}</strong><p>{memories.error}</p></div><button className="button secondary" onClick={memories.reload}>{t("重试")}</button></div>}
    {notice && <div className="notice success inbox-note" role="status"><span>{notice.text}</span>{notice.id && <button className="text-button" onClick={() => onOpen(notice.id!)}>{t("查看正式记忆")}</button>}</div>}
    <div className="inbox">
      <header className="inbox-head"><h1>{t("待确认")}</h1>{resource.data && <span>{pending.length}</span>}</header>
      <BatchBar proposals={pending} tick={tick} online={live} onChanged={() => { resource.reload(); onSaved() }} />
      {empty && <Empty title={t("当前没有待确认的记忆")} />}
      {!!pending.length && <ol className="inbox-rows">
        {pending.map(item => <InboxRow key={item.id} proposal={item} library={library} libraryReady={libraryReady} focused={item.id === currentId} open={item.id === openId} online={live} command={command} onSelect={toggle => choose(item.id, toggle)} onDone={finish} />)}
      </ol>}
    </div>
  </div>
}

function InboxSkeleton() {
  return <div className="page inbox-page" role="status" aria-label={t("正在加载待确认")}><div className="inbox"><header className="inbox-head"><h1>{t("待确认")}</h1></header><div className="inbox-rows">{Array.from({ length: 6 }, (_, index) => <div className="inbox-row" key={index}><Skeleton className="skeleton-mark" style={{ width: 28, height: 28, borderRadius: 8 }} /><Skeleton className="skeleton-sentence" style={{ width: `${52 + (index % 3) * 12}%` }} /></div>)}</div></div></div>
}

function InboxRow({ proposal, library, libraryReady, focused, open, online, command, onSelect, onDone }: {
  proposal: Proposal
  library: Memory[]
  libraryReady: boolean
  focused: boolean
  open: boolean
  online: boolean
  command: Command | null
  onSelect: (toggle?: boolean) => boolean
  onDone: (id: string, result: { text: string; id?: string }) => void
}) {
  const requester = proposal.requester || (proposal.source.kind === "agent_claim"
    ? { client: "agent", name: proposal.source.name || t("Agent 提案") }
    : { client: "omna", name: "OMNA" })
  const [mode, setMode] = useState<"idle" | "edit" | "choice">("idle")
  const [draft, setDraft] = useState(proposal.payload.content)
  const [onlySelf, setOnlySelf] = useState(proposal.payload.share_enabled === false)
  const [shareTouched, setShareTouched] = useState(false)
  const [scope, setScope] = useState("")
  const [keeping, setKeeping] = useState(false)
  const [error, setError] = useMessage()
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const attempt = useRef<{ sig: string; key: string } | null>(null)
  const running = useRef(false)
  const seen = useRef(0)
  const editor = useRef<HTMLTextAreaElement>(null)
  const target = useResource(() => proposal.target_id ? api.memory(proposal.target_id) : Promise.resolve(null), [proposal.target_id])
  const current = target.data && target.data.id === proposal.target_id ? target.data : null
  const text = draft.trim()
  const lane = useMemo(() => classify(proposal, text, library, libraryReady), [proposal, text, library, libraryReady])
  const similar = useMemo(() => proposal.target_id ? null : findSimilar(text, library) || proposal.payload.similar_to || null, [proposal.target_id, proposal.payload.similar_to, text, library])
  const stale = !!current && proposal.base_revision != null && current.revision !== proposal.base_revision
  const blocked = stale || conflict
  const dirty = draft !== proposal.payload.content || shareTouched
  useUnsaved(dirty && focused, busy)
  useEffect(() => { if (mode === "edit") editor.current?.focus() }, [mode])
  useEffect(() => {
    if (mode !== "choice") return
    if (lane === "long") setMode("edit")
    else if (lane === "quick" || lane === "unchecked") setMode("idle")
  }, [lane, mode])
  useEffect(() => {
    if ((focused && open) || busy) return
    setMode("idle")
    setDraft(proposal.payload.content)
    setOnlySelf(proposal.payload.share_enabled === false)
    setShareTouched(false)
    setScope("")
    setKeeping(false)
    setError("")
    setConflict(false)
  }, [focused, open, proposal.payload.content, proposal.payload.share_enabled, busy])
  async function send(body: Record<string, unknown>) {
    if (running.current) return
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    running.current = true
    setBusy(true)
    setError("")
    try {
      const result = await api.decide(proposal.id, body, attempt.current.key)
      if (result.status !== "accepted" && result.status !== "rejected") throw new Error(t("服务未确认审核结果，请重试核对。"))
      onDone(proposal.id, {
        text: result.status === "rejected" ? t("已忽略，正式记忆没有改变。") : t("已记住，记忆列表已更新。"),
        id: result.memory_id,
      })
    } catch (err) {
      setError(() => (explain(err)))
      if (err instanceof ApiError && err.code === "CONFLICT") {
        setConflict(true)
        setMode("choice")
        target.reload()
      }
    } finally {
      running.current = false
      setBusy(false)
    }
  }
  function bodyFor(decision: string, extra: Record<string, unknown> = {}) {
    const body: Record<string, unknown> = { decision, content: text, ...extra }
    const inherited = (proposal.payload.scope || "").trim()
    if (decision !== "keep_both" && inherited) body.scope = inherited
    if (shareTouched) body.share_enabled = !onlySelf
    return body
  }
  async function saveNew() {
    const decision = text !== proposal.payload.content.trim() ? "edit" : "accept"
    await send(bodyFor(decision))
  }
  async function updateOriginal() {
    if (!proposal.target_id || blocked) return
    if (!current) { setError(() => (t("当前记忆还未加载成功，请重试。"))); return }
    if (proposal.base_revision == null || current.revision !== proposal.base_revision) return
    await send(bodyFor("update", { target_id: proposal.target_id, base_revision: proposal.base_revision }))
  }
  async function keepBoth() {
    if (!scope.trim()) { setError(() => (t("另存一条需要填写适用场景。"))); return }
    await send(bodyFor("keep_both", { scope: scope.trim() }))
  }
  async function ignore() {
    if (!online) return
    await send({ decision: "reject" })
  }
  async function remember() {
    if (!online || running.current || mode === "choice") return
    if (!text) { setError(() => (t("记忆内容不能为空。"))); if (!open) onSelect(); setMode("edit"); return }
    if (lane === "long") { setError(() => (t("这条太长，先改短再保存。"))); if (!open) onSelect(); setMode("edit"); return }
    if (lane === "unchecked") { setError(() => (t("还在核对已有记忆，稍后再保存。"))); return }
    if (lane === "quick") { setMode("idle"); await saveNew(); return }
    setError("")
    if (!open) onSelect()
    setMode("choice")
  }
  const actions = useRef({ edit() {}, ignore() {}, remember() {} })
  actions.current.edit = () => { if (mode !== "edit") setMode("edit") }
  actions.current.ignore = () => { void ignore() }
  actions.current.remember = () => { void remember() }
  useEffect(() => {
    if (!command || command.id !== proposal.id || command.n === seen.current) return
    seen.current = command.n
    if (command.type === "edit") actions.current.edit()
    if (command.type === "ignore") actions.current.ignore()
    if (command.type === "remember") actions.current.remember()
  }, [command, proposal.id])
  function press(run: () => void) {
    return (event: { stopPropagation: () => void }) => {
      event.stopPropagation()
      if (!open && !onSelect()) return
      run()
    }
  }
  const verb = proposal.target_id ? t("修改") : t("新增")
  const was = current?.content?.trim()
  const showChoice = open && (mode === "choice" || lane === "update" || lane === "similar" || lane === "evidence")
  return <li className={`inbox-row${focused ? " selected" : ""}${open ? " open" : ""}`} aria-current={focused ? "true" : undefined} aria-expanded={open} onClick={() => onSelect(true)}>
    <span className="inbox-avatar" title={requester.name}><SourceMark origin={requester} /></span>
    <div className="inbox-copy">
      <p className="inbox-kicker"><span className="agent">{requester.name}</span><span>· {verb}</span><CategoryTag category={proposal.payload.category} /></p>
      {open && mode === "edit" ? <textarea ref={editor} className="inbox-editor" aria-label={t("修改后的内容")} rows={Math.min(6, Math.max(2, draft.split("\n").length))} maxLength={4000} value={draft} disabled={busy} onClick={event => event.stopPropagation()} onChange={event => setDraft(event.target.value)} onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setDraft(proposal.payload.content); setOnlySelf(proposal.payload.share_enabled === false); setShareTouched(false); setMode("idle"); setError("") }
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void remember() }
      }} /> : <p className="inbox-sentence">{draft}</p>}
      {open && mode === "edit" && <label className="inbox-share"><input type="checkbox" checked={onlySelf} disabled={busy} onChange={event => { setOnlySelf(event.target.checked); setShareTouched(true) }} />{t("仅自己可见")}</label>}
      {open && mode !== "edit" && lane === "update" && !blocked && <p className="inbox-hint">{was ? t("替换「{0}」", [was]) : t("替换一条已有记忆")}</p>}
      {open && mode !== "edit" && lane === "similar" && <p className="inbox-hint">{similar && !("id" in similar) && !similar.memory_id ? t("和这次导入里的另一条很像") : t("已有一条几乎一样的记忆")}</p>}
      {open && mode !== "edit" && lane === "long" && <p className="inbox-hint">{t("这条太长，先改短再保存。")}</p>}
      {open && mode !== "edit" && lane === "evidence" && <p className="inbox-hint">{t("缺少依据，不能直接保存。")}</p>}
      {showChoice && lane === "update" && <div className="inbox-choices">
        {blocked && <p className="inbox-hint">{t("当前记忆已经更新，这条建议不能再改它。")}</p>}
        {!blocked && <button type="button" className="primary" disabled={busy || !online || !current} onClick={press(() => void updateOriginal())}>{t("更新原记忆")}</button>}
        <button type="button" disabled={busy} onClick={press(() => setKeeping(true))}>{t("另存一条")}</button>
      </div>}
      {showChoice && keeping && <input className="inbox-scope" aria-label={t("适用场景")} placeholder={t("适用场景，例如：正式报告")} value={scope} disabled={busy} onClick={event => event.stopPropagation()} onChange={event => setScope(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void keepBoth() } }} />}
      {showChoice && keeping && <div className="inbox-choices"><button type="button" className="primary" disabled={busy || !online || !scope.trim()} onClick={press(() => void keepBoth())}>{t("保存为新记忆")}</button></div>}
      {showChoice && (lane === "similar" || lane === "evidence") && <div className="inbox-choices">
        {lane === "similar" && similar?.content && <p className="inbox-hint">{similar.content}</p>}
        <button type="button" className="primary" disabled={busy || !online} onClick={press(() => void saveNew())}>{t("仍要记住")}</button>
        <button type="button" disabled={busy || !online} onClick={press(() => void ignore())}>{t("不用了")}</button>
      </div>}
      {proposal.target_id && target.error && <p className="inbox-error">{target.error} <button type="button" className="text-button" onClick={event => { event.stopPropagation(); target.reload() }}>{t("重试")}</button></p>}
      {error && <p className="inbox-error" role="alert">{error}</p>}
    </div>
    <div className="inbox-actions">
      <button type="button" className="inbox-icon edit" aria-label={t("编辑")} disabled={busy} onClick={press(() => setMode("edit"))}><Icon name="edit" /></button>
      <button type="button" className="inbox-icon ignore" aria-label={t("忽略")} disabled={busy || !online} onClick={press(() => void ignore())}><Icon name="close" /></button>
      <button type="button" className="inbox-icon remember" aria-label={t("记住")} disabled={busy || !online} onClick={press(() => void remember())}><Icon name="check" /></button>
    </div>
  </li>
}

function CategoryTag({ category }: { category: string }) {
  const icon = category === "identity" || category === "goal" || category === "preference" || category === "project" || category === "event" ? category : "other"
  return <span className="inbox-tag"><Icon name={icon} />{categoryLabel(category)}</span>
}

export function classify(item: Proposal, content: string, memories: Memory[], ready: boolean): Lane {
  if (item.target_id) return "update"
  if (content.length > 2000) return "long"
  if (!ready) return "unchecked"
  if (!item.evidence?.text) return "evidence"
  if (findSimilar(content, memories) || item.payload.similar_to) return "similar"
  return "quick"
}

function findSimilar(content: string, memories: Memory[]) {
  let best: { memory: Memory; score: number } | null = null
  for (const memory of memories) {
    const value = memory.content || ""
    if (value.length < 8) continue
    const score = overlap(content, value)
    if (score >= 0.62 && (!best || score > best.score)) best = { memory, score }
  }
  return best?.memory ?? null
}

function overlap(left: string, right: string) {
  const a = grams(left)
  const b = grams(right)
  if (a.size < 4 || b.size < 4) return 0
  let hit = 0
  for (const gram of a) if (b.has(gram)) hit++
  const shorter = Math.min(a.size, b.size)
  const contained = left.length >= 12 && right.length >= 12 && (left.includes(right) || right.includes(left))
  return contained ? 1 : hit / shorter
}

function grams(value: string) {
  const text = value.replace(/\s+/g, "")
  const out = new Set<string>()
  for (let index = 0; index < text.length - 1; index++) out.add(text.slice(index, index + 2))
  return out
}
