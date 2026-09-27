import { AlertDialog } from "@base-ui/react/alert-dialog"
import { Menu } from "@base-ui/react/menu"
import { Switch } from "@base-ui/react/switch"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"
import { ApiError, api, explain, type Memory, type Source, type Version } from "./api"
import { CATEGORIES, categoryLabel, dateLabel, lifecycleLabel, sourceLabel } from "./format"
import { canLeave, Icon, Notice, ResourceNotice, SourceMark, usePanel, useResource, useUnsaved } from "./ui"

const morphSpring = { type: "spring" as const, stiffness: 200, damping: 24 }
const fadeSpring = { type: "spring" as const, bounce: 0, duration: 0.38 }

type Fields = { content: string; scope: string; onlySelf: boolean; category: string; expiry: string }
type Preview = { version_count: number; sources: { id: string; kind: string; name: string | null }[] }

function toLocal(value?: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ""
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

function fromMemory(memory: Memory, over: Partial<Fields> = {}): Fields {
  return {
    content: memory.content || "",
    scope: memory.scope || "",
    onlySelf: memory.share_enabled === false,
    category: memory.category,
    expiry: toLocal(memory.valid_until),
    ...over,
  }
}

function payload(memory: Memory, fields: Fields) {
  const expiry = fields.expiry.trim()
  return {
    content: fields.content.trim(),
    kind: fields.category === "event" ? "event" : "fact",
    category: fields.category,
    scope: fields.scope.trim() || null,
    valid_until: expiry ? new Date(expiry).toISOString() : null,
    share_enabled: !fields.onlySelf,
    source_refs: memory.source_ids || [],
    base_revision: memory.revision,
  }
}

export function Detail({ memoryId, layoutId, seed, origin, onClose, onSaved, online }: { memoryId: string; layoutId?: string; seed?: string; origin?: { client: string; name: string }; onClose: () => void; onSaved: () => void; online: boolean }) {
  const resource = useResource(async () => {
    const [memory, history] = await Promise.all([api.memory(memoryId), api.versions(memoryId)])
    return { memory, versions: history.versions }
  }, [memoryId])
  const [memory, setMemory] = useState<Memory | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const [scope, setScope] = useState("")
  const [onlySelf, setOnlySelf] = useState(false)
  const [category, setCategory] = useState("")
  const [expiry, setExpiry] = useState("")
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const attempt = useRef<{ sig: string; key: string } | null>(null)
  const editingRef = useRef(false)
  const previewRef = useRef<Preview | null>(null)
  const busyRef = useRef(false)
  const contentRef = useRef<HTMLTextAreaElement>(null)
  const scopeRef = useRef<HTMLInputElement>(null)
  const reduce = useReducedMotion()
  const morph = !reduce && !!layoutId
  const panel = useRef<HTMLDivElement>(null)
  const [present, setPresent] = useState(true)
  const finished = useRef(false)
  editingRef.current = editing
  previewRef.current = preview
  busyRef.current = busy
  const fields = { content: draft, scope, onlySelf, category, expiry }
  const dirty = editing && !!memory && (
    draft !== (memory.content || "") ||
    scope !== (memory.scope || "") ||
    onlySelf !== (memory.share_enabled === false) ||
    category !== memory.category ||
    expiry !== toLocal(memory.valid_until)
  )
  useUnsaved(dirty, busy)
  const close = () => {
    if (panel.current?.querySelector("[data-popup-open]")) return
    if (previewRef.current) { setPreview(null); return }
    if (canLeave()) setPresent(false)
  }
  const finish = () => { if (!finished.current) { finished.current = true; onClose() } }
  usePanel(panel, close, true)
  function apply(next: Memory) {
    setDraft(next.content || "")
    setScope(next.scope || "")
    setOnlySelf(next.share_enabled === false)
    setCategory(next.category)
    setExpiry(toLocal(next.valid_until))
  }
  useEffect(() => {
    if (!resource.data) return
    setMemory(resource.data.memory)
    setVersions(resource.data.versions)
    if (!editingRef.current) apply(resource.data.memory)
  }, [resource.data])
  function beginEdit(target: "content" | "scope") {
    if (!memory || !online || resource.error || busy) return
    if (!editingRef.current) {
      apply(memory)
      editingRef.current = true
      setEditing(true)
      setNotice("")
      setError("")
    }
    requestAnimationFrame(() => (target === "scope" ? scopeRef : contentRef).current?.focus())
  }
  function cancelEdit() {
    if (!memory || !canLeave()) return
    editingRef.current = false
    setEditing(false)
    apply(memory)
    setError("")
    setConflict(false)
  }
  async function latest() {
    setBusy(true)
    try {
      const next = await api.memory(memoryId)
      setMemory(next)
      setConflict(false)
      setError("")
      if (editingRef.current) setNotice("已载入最新版本。你的输入已保留，请比较后再保存。")
      else { apply(next); setNotice("已载入最新版本。") }
    } catch (err) { setError(explain(err)) } finally { setBusy(false) }
  }
  async function persist(next: Fields, keepDraft: boolean) {
    if (!memory || busyRef.current) return
    if (next.expiry.trim() && !Number.isFinite(new Date(next.expiry).getTime())) return
    const body = payload(memory, next)
    if (!body.content) {
      setError("记忆内容不能为空。")
      if (!keepDraft) apply(memory)
      return
    }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    busyRef.current = true
    setBusy(true); setError(""); setNotice("")
    try {
      const saved = await api.updateMemory(memory.id, body, attempt.current.key) as { status?: string; revision?: number }
      if (saved.status !== "accepted" || !saved.revision) throw new Error("服务未确认保存结果，请重试核对。")
      attempt.current = null
      editingRef.current = false
      setEditing(false)
      setConflict(false)
      setMemory(current => current ? { ...current, content: body.content, category: body.category, scope: body.scope, share_enabled: body.share_enabled, valid_until: body.valid_until, kind: body.kind, revision: saved.revision ?? current.revision } : current)
      setDraft(body.content)
      setScope(body.scope || "")
      setOnlySelf(!body.share_enabled)
      setCategory(body.category)
      setExpiry(toLocal(body.valid_until))
      setNotice("已保存，关于我和记忆列表已同步更新。")
      onSaved()
      resource.reload()
    } catch (err) {
      setError(explain(err))
      setConflict(err instanceof ApiError && err.code === "CONFLICT")
      if (!keepDraft) apply(memory)
    } finally { busyRef.current = false; setBusy(false) }
  }
  function onShare(checked: boolean) {
    if (!memory || busyRef.current || conflict) return
    const next = !checked
    setOnlySelf(next)
    if (editingRef.current) return
    void persist(fromMemory(memory, { onlySelf: next }), false)
  }
  function onCategory(value: unknown) {
    if (!memory || busyRef.current || conflict || typeof value !== "string" || !value) return
    if (editingRef.current) { setCategory(value); return }
    if (value === memory.category) return
    setCategory(value)
    void persist(fromMemory(memory, { category: value }), false)
  }
  function commitExpiry() {
    if (!memory || editingRef.current || busyRef.current || conflict) return
    if (expiry === toLocal(memory.valid_until)) return
    if (expiry && !Number.isFinite(new Date(expiry).getTime())) return
    void persist(fromMemory(memory, { expiry }), false)
  }
  function clearExpiry() {
    if (!memory || busy || conflict) return
    setExpiry("")
    if (editingRef.current) return
    if (!memory.valid_until) return
    void persist(fromMemory(memory, { expiry: "" }), false)
  }
  async function openDelete() {
    if (!memory || busy || editing) return
    setBusy(true); setError(""); setNotice("")
    try { setPreview(await api.deletionPreview(memory.id)) }
    catch (err) { setError(explain(err)) }
    finally { setBusy(false) }
  }
  async function confirmDelete() {
    if (!memory || busy) return
    setBusy(true); setError("")
    try {
      const result = await api.deleteMemory(memory.id, crypto.randomUUID())
      if (result.status !== "deleted") throw new Error("服务未确认删除结果，请重试核对。")
      onSaved()
      setPreview(null)
      setPresent(false)
    } catch (err) { setError(explain(err)); setBusy(false) }
  }
  const copy = memory?.content || seed || ""
  const shown = editing ? draft : copy
  const long = shown.length > 80 || shown.includes("\n")
  const shownCategory = category || memory?.category || ""
  const fade = reduce ? { duration: 0.16 } : fadeSpring
  return <>
  <AnimatePresence onExitComplete={finish}>
    {present && <motion.button key="detail-backdrop" type="button" className="dialog-backdrop" aria-label="关闭详情" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduce ? 0.01 : 0.22 }} onClick={close}/>}
    {present && <motion.div key="detail-panel" ref={panel} tabIndex={-1} className="detail-pane material" layoutId={morph ? layoutId : undefined} role="dialog" aria-modal="true" aria-label="记忆详情" style={{ borderRadius: 24 }} initial={morph ? false : { y: reduce ? 0 : 12, opacity: reduce ? 1 : 0.86 }} animate={morph ? undefined : { y: 0, opacity: 1 }} exit={morph ? { opacity: 1, transition: { duration: 0.5 } } : { opacity: 0, y: reduce ? 0 : 8, transition: { duration: reduce ? 0.01 : 0.2 } }} transition={morph ? { layout: morphSpring } : fade}>
    <motion.div className="detail-reveal" initial={morph ? { opacity: 0 } : false} animate={{ opacity: 1 }} exit={morph ? { opacity: 0, transition: { duration: 0.12 } } : undefined} transition={{ duration: reduce ? 0.01 : 0.22, delay: morph ? 0.08 : 0 }}>
    <header className="detail-top">
      <div className="detail-kicker">
        {memory && <Menu.Root>
          <Menu.Trigger className="tag detail-category" disabled={!online || busy || conflict || !!resource.error}>{categoryLabel(shownCategory)}</Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className="detail-positioner" side="bottom" align="start" sideOffset={6}>
              <Menu.Popup className="select-popup">
                <Menu.RadioGroup value={shownCategory} onValueChange={onCategory}>
                  {CATEGORIES.map(([id, label]) => <Menu.RadioItem key={id} className="tool-option" value={id} closeOnClick>{label}</Menu.RadioItem>)}
                </Menu.RadioGroup>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>}
        {memory && memory.revision > 1 && <span className="detail-version">版本 {memory.revision}</span>}
      </div>
      <button className="icon-button" type="button" aria-label="关闭详情" onClick={close}><Icon name="close"/></button>
    </header>
    <div className="panel-body detail-body">
      <ResourceNotice resource={resource}/>
      {!online && <Notice tone="warning">本地服务不可用。正在展示上次加载的数据；你的修改会保留。</Notice>}
      {error && !preview && <Notice tone="error"><div>{error}{conflict && <button className="button secondary" type="button" onClick={latest} disabled={busy || !online}>查看最新版本并保留输入</button>}</div></Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      {(copy || memory) && <>
        {!editing && <p className={`detail-content${long ? " long" : ""}`} role="button" tabIndex={0} onClick={() => { if (window.getSelection()?.toString()) return; beginEdit("content") }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); beginEdit("content") } }}>{copy || "正文暂时无法读取。"}</p>}
        {editing && <div className="detail-editor">
          <textarea ref={contentRef} aria-label="纠正后的内容" className={long ? "long" : ""} value={draft} onChange={event => setDraft(event.target.value)} maxLength={2000} rows={long ? 8 : 4}/>
          <div className="actions"><button className="button primary" type="button" disabled={busy || !draft.trim() || !online || conflict || !dirty} onClick={() => void persist(fields, true)}>{busy ? "保存中…" : "保存修改"}</button><button className="button secondary" type="button" disabled={busy} onClick={cancelEdit}>取消</button></div>
          {conflict && <details className="disclosure" open><summary>正在比较的当前版本 {memory?.revision}</summary><p className="prose">{memory?.content}</p></details>}
        </div>}
        {memory && <div className="detail-props">
          <div className="detail-prop">
            <span className="detail-prop-label" id="detail-share-label">谁能读取</span>
            <span className="detail-prop-value">
              <span className="share-control">
                <Switch.Root className="share-switch" checked={!onlySelf} disabled={!online || busy || conflict || !!resource.error} onCheckedChange={onShare} aria-labelledby="detail-share-label">
                  <Switch.Thumb className="share-switch-thumb"/>
                </Switch.Root>
              </span>
              <span>{onlySelf ? "仅自己可见" : "已授权的 Agent"}</span>
            </span>
          </div>
          <div className="detail-prop">
            <span className="detail-prop-label">适用场景</span>
            <span className="detail-prop-value">
              {editing
                ? <input ref={scopeRef} aria-label="适用场景" value={scope} onChange={event => setScope(event.target.value)} placeholder="例如：正式报告"/>
                : <button type="button" className={`detail-scope${memory.scope ? "" : " empty"}`} onClick={() => beginEdit("scope")}>{memory.scope || "添加场景"}</button>}
            </span>
          </div>
        </div>}
        {memory && <div className="detail-more">
          <section className="detail-block">
            <h3>来源</h3>
            {(memory.source_ids || []).length
              ? memory.source_ids!.map(id => <SourceLine key={id} id={id} origin={(memory.source_ids || []).length === 1 ? origin : undefined} time={memory.created_at}/>)
              : <p className="helper">没有关联来源。</p>}
          </section>
          {versions.length > 1 && <section className="detail-block"><h3>历史版本 <span className="count">{versions.length}</span></h3><p className="helper">旧版本仅供回看，不作为当前记忆提供给 Agent。</p>{versions.map(version => <details className="version-item" key={version.revision}><summary>版本 {version.revision}<span>{lifecycleLabel(version.lifecycle)}</span></summary><p className="prose">{version.content || "正文暂时无法读取。"}</p>{version.scope && <p className="helper">适用场景：{version.scope}</p>}</details>)}</section>}
          <section className="detail-block detail-extra">
            <details>
              <summary>更多</summary>
              <label className="field">有效期至<input type="datetime-local" aria-label="有效期" value={expiry} disabled={!online || busy || conflict} onChange={event => setExpiry(event.target.value)} onBlur={commitExpiry}/></label>
              <p className="helper">留空为不限。{(expiry || memory.valid_until) && <button type="button" className="text-button" disabled={!online || busy || conflict} onMouseDown={event => event.preventDefault()} onClick={clearExpiry}>清除有效期</button>}</p>
              <button className="button danger" type="button" disabled={!online || busy || editing} onClick={openDelete}>{busy && !preview ? "正在准备…" : "永久删除"}</button>
            </details>
          </section>
        </div>}
      </>}
    </div>
    </motion.div>
    </motion.div>}
  </AnimatePresence>
  <AlertDialog.Root open={present && !!preview} onOpenChange={open => { if (!open && !busyRef.current) setPreview(null) }}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className="dialog-backdrop detail-alert-backdrop"/>
        <AlertDialog.Popup className="detail-alert material">
          <AlertDialog.Title className="detail-alert-title">永久删除这条记忆</AlertDialog.Title>
          {preview && <AlertDialog.Description className="detail-alert-copy">删除后无法从 OMNA 里恢复这条记忆。若来源仍包含这段内容，确认后会整份删除这些来源；其他记忆会保留，并显示来源已删除。将删除 {preview.version_count} 个版本。</AlertDialog.Description>}
          {preview && (preview.sources.length ? <ul className="detail-alert-list">{preview.sources.map(item => <li key={item.id}>{sourceLabel(item.kind)}{item.name ? ` · ${item.name}` : ""}</li>)}</ul> : <p className="helper">没有仍包含这段内容的来源。</p>)}
          {error && <Notice tone="error">{error}</Notice>}
          <div className="actions detail-alert-actions">
            <AlertDialog.Close className="button secondary" disabled={busy}>取消</AlertDialog.Close>
            <button className="button danger" type="button" disabled={busy || !online} onClick={confirmDelete}>{busy ? "删除中…" : "确认永久删除"}</button>
          </div>
        </AlertDialog.Popup>
      </AlertDialog.Portal>
  </AlertDialog.Root>
  </>
}

function originFromSource(source: Source) {
  if (source.kind === "agent_claim") return { client: "agent", name: source.name || "Agent 提案" }
  return { client: "omna", name: "OMNA" }
}

function SourceLine({ id, origin, time }: { id: string; origin?: { client: string; name: string }; time?: string }) {
  const source = useResource<Source>(() => api.source(id), [id])
  if (source.error === "source not found") return <p className="helper">来源已删除</p>
  const data = source.data
  const mark = origin || (data ? originFromSource(data) : undefined)
  return <div className="source-block detail-source"><ResourceNotice resource={source}/>{data && <details className="disclosure"><summary>{mark && <SourceMark origin={mark}/>}<time>{dateLabel(time || data.imported_at)}</time></summary><p className="prose">{data.content}</p></details>}</div>
}

export function SourceContent({ id }: { id: string }) {
  const source = useResource<Source>(() => api.source(id), [id])
  if (source.error === "source not found") return <p className="helper">来源已删除</p>
  return <div className="source-block"><ResourceNotice resource={source}/>{source.data && <details className="disclosure"><summary>{sourceLabel(source.data.kind)}{source.data.name ? ` · ${source.data.name}` : " · 查看来源文本"}</summary><p className="prose">{source.data.content}</p><p className="helper">{dateLabel(source.data.imported_at)}</p></details>}</div>
}
