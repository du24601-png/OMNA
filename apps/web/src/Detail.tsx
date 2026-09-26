import { Dialog } from "@base-ui/react/dialog"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"
import { ApiError, api, explain, type Memory, type Source, type Version } from "./api"
import { categoryLabel, dateLabel, lifecycleLabel, sharingLabel, sourceLabel } from "./format"
import { canLeave, Icon, Notice, ResourceNotice, useResource, useUnsaved } from "./ui"

export function Detail({ memoryId, onClose, onSaved, online }: { memoryId: string; onClose: () => void; onSaved: () => void; online: boolean }) {
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
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const attempt = useRef<{ sig: string; key: string } | null>(null)
  const reduce = useReducedMotion()
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1200px)").matches)
  const dirty = editing && !!memory && (draft !== memory.content || scope !== (memory.scope || "") || onlySelf !== (memory.share_enabled === false))
  useUnsaved(dirty, busy)
  const close = () => { if (canLeave()) onClose() }
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1200px)")
    const sync = () => setNarrow(media.matches)
    media.addEventListener("change", sync)
    return () => media.removeEventListener("change", sync)
  }, [])
  useEffect(() => {
    if (!resource.data) return
    setMemory(resource.data.memory); setVersions(resource.data.versions)
    if (!editing) { setDraft(resource.data.memory.content || ""); setScope(resource.data.memory.scope || ""); setOnlySelf(resource.data.memory.share_enabled === false) }
  }, [resource.data])
  async function latest() {
    setBusy(true)
    try {
      const next = await api.memory(memoryId)
      setMemory(next); setConflict(false); setError("")
      setNotice("已载入最新版本。你的输入已保留，请比较后再保存。")
    } catch (err) { setError(explain(err)) } finally { setBusy(false) }
  }
  async function save() {
    if (!memory || busy) return
    const body = { content: draft, kind: memory.kind, category: memory.category, scope: scope.trim() || null, valid_until: memory.valid_until ?? null, share_enabled: !onlySelf, source_refs: memory.source_ids || [], base_revision: memory.revision }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    setBusy(true); setError(""); setNotice("")
    try {
      const saved = await api.updateMemory(memory.id, body, attempt.current.key) as { status?: string; revision?: number }
      if (saved.status !== "accepted" || !saved.revision) throw new Error("服务未确认保存结果，请重试核对。")
      attempt.current = null; setEditing(false); setConflict(false)
      setNotice("已保存，关于我和记忆列表已同步更新。")
      onSaved(); resource.reload()
    } catch (err) { setError(explain(err)); setConflict(err instanceof ApiError && err.code === "CONFLICT") }
    finally { setBusy(false) }
  }
  return <Dialog.Root open modal={narrow} disablePointerDismissal={!narrow} onOpenChange={(open, details) => { if (!open) { if (!canLeave()) details.cancel(); else onClose() } }}>
    <Dialog.Portal>
      {narrow && <Dialog.Backdrop className="dialog-backdrop"/>}
      <Dialog.Popup className="detail-pane material" aria-label="记忆详情" render={<motion.div initial={reduce ? false : { x: 28, opacity: 0.7 }} animate={{ x: 0, opacity: 1 }} transition={reduce ? { duration: 0.2 } : { type: "spring", bounce: 0, duration: 0.4 }}/>}>
    <header className="panel-heading"><div><span className="eyebrow">每一条，都有依据</span><h2>记忆详情</h2></div><button className="icon-button" aria-label="关闭详情" onClick={close}><Icon name="close"/></button></header>
    <div className="panel-body">
      <ResourceNotice resource={resource}/>
      {!online && <Notice tone="warning">本地服务不可用。正在展示上次加载的数据；你的修改会保留。</Notice>}
      {error && <Notice tone="error"><div>{error}{conflict && <button className="button secondary" onClick={latest} disabled={busy || !online}>查看最新版本并保留输入</button>}</div></Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      {memory && <>
        <div className="metadata"><span className="tag">{categoryLabel(memory.category)}</span><span>版本 {memory.revision}</span></div>
        {editing ? <>
          <label className="field">记忆内容<textarea aria-label="纠正后的内容" value={draft} onChange={e => setDraft(e.target.value)} maxLength={2000} rows={5}/></label>
          <label className="field">适用场景<input aria-label="详情适用场景" value={scope} onChange={e => setScope(e.target.value)} placeholder="例如：正式报告"/></label>
          <label className="check-row"><input type="checkbox" checked={onlySelf} onChange={e => setOnlySelf(e.target.checked)}/>仅自己可见</label>
          <p className="helper">{onlySelf ? "这条记忆不会提供给任何 Agent。" : "允许已授权 Agent 读取；仍受类别、工具和有效期限制。"}</p>
          <div className="actions"><button className="button primary" disabled={busy || !draft.trim() || !online || conflict} onClick={save}>{busy ? "保存中…" : "保存修改"}</button><button className="button secondary" disabled={busy} onClick={() => { if (canLeave()) { setEditing(false); setDraft(memory.content || ""); setScope(memory.scope || ""); setOnlySelf(memory.share_enabled === false); setError("") } }}>取消编辑</button></div>
          <details className="disclosure"><summary>正在比较的当前版本 {memory.revision}</summary><p className="prose">{memory.content}</p></details>
        </> : <><p className="detail-content">{memory.content || "正文暂时无法读取。"}</p><dl className="detail-facts"><div><dt>适用场景</dt><dd>{memory.scope || "未限定场景"}</dd></div><div><dt>读取范围</dt><dd>{sharingLabel(memory)}</dd></div><div><dt>更新时间</dt><dd>{dateLabel(memory.created_at)}</dd></div>{memory.valid_until && <div><dt>有效期至</dt><dd>{dateLabel(memory.valid_until)}</dd></div>}</dl><button className="button primary" disabled={!online || !!resource.error} onClick={() => { setDraft(memory.content || ""); setScope(memory.scope || ""); setOnlySelf(memory.share_enabled === false); setEditing(true); setNotice("") }}>编辑这条记忆</button></>}
        <section className="detail-section"><h3>来源与证据</h3>{(memory.source_ids || []).length ? memory.source_ids!.map(id => <SourceContent key={id} id={id}/>) : <p className="helper">这条记忆没有关联来源。</p>}</section>
        <section className="detail-section"><h3>历史版本 <span className="count">{versions.length}</span></h3><p className="helper">旧版本仅供回看，不作为当前记忆提供给 Agent。</p>{versions.map(v => <details className="version-item" key={v.revision}><summary>版本 {v.revision}<span>{lifecycleLabel(v.lifecycle)}</span></summary><p className="prose">{v.content || "正文暂时无法读取。"}</p>{v.scope && <p className="helper">适用场景：{v.scope}</p>}</details>)}</section>
      </>}
    </div>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}
export function SourceContent({ id }: { id: string }) {
  const source = useResource<Source>(() => api.source(id), [id])
  return <div className="source-block"><ResourceNotice resource={source}/>{source.data && <details className="disclosure"><summary>{sourceLabel(source.data.kind)}{source.data.name ? ` · ${source.data.name}` : " · 查看来源文本"}</summary><p className="prose">{source.data.content}</p><p className="helper">{dateLabel(source.data.imported_at)}</p></details>}</div>
}
