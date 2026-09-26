import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"
import { ApiError, api, explain, type Memory, type Source, type Version } from "./api"
import { categoryLabel, dateLabel, lifecycleLabel, sharingLabel, sourceLabel } from "./format"
import { canLeave, Icon, Notice, ResourceNotice, usePanel, useResource, useUnsaved } from "./ui"

const morphSpring = { type: "spring" as const, stiffness: 200, damping: 24 }

export function Detail({ memoryId, layoutId, seed, onClose, onSaved, online }: { memoryId: string; layoutId?: string; seed?: string; onClose: () => void; onSaved: () => void; online: boolean }) {
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
  const [preview, setPreview] = useState<{ version_count: number; sources: { id: string; kind: string; name: string | null }[] } | null>(null)
  const attempt = useRef<{ sig: string; key: string } | null>(null)
  const reduce = useReducedMotion()
  const morph = !reduce && !!layoutId
  const panel = useRef<HTMLDivElement>(null)
  const [present, setPresent] = useState(true)
  const finished = useRef(false)
  const dirty = editing && !!memory && (draft !== memory.content || scope !== (memory.scope || "") || onlySelf !== (memory.share_enabled === false))
  useUnsaved(dirty, busy)
  const close = () => { if (canLeave()) setPresent(false) }
  const finish = () => { if (!finished.current) { finished.current = true; onClose() } }
  usePanel(panel, close, true)
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
      onSaved(); close()
    } catch (err) { setError(explain(err)); setBusy(false) }
  }
  const copy = memory?.content || seed
  return <AnimatePresence onExitComplete={finish}>
    {present && <motion.button key="detail-backdrop" type="button" className="dialog-backdrop" aria-label="关闭详情" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduce ? 0.01 : 0.22 }} onClick={close}/>}
    {present && <motion.div key="detail-panel" ref={panel} className="detail-pane material" layoutId={morph ? layoutId : undefined} role="dialog" aria-modal="true" aria-label="记忆详情" style={{ borderRadius: 24 }} initial={morph ? false : { opacity: reduce ? 1 : 0 }} animate={morph ? undefined : { opacity: 1 }} exit={morph ? { opacity: 1, transition: { duration: 0.5 } } : { opacity: 0, transition: { duration: reduce ? 0.01 : 0.2 } }} transition={morph ? { layout: morphSpring } : { duration: reduce ? 0.01 : 0.22 }}>
    <motion.div className="detail-reveal" initial={morph ? { opacity: 0 } : false} animate={{ opacity: 1 }} exit={morph ? { opacity: 0, transition: { duration: 0.12 } } : undefined} transition={{ duration: reduce ? 0.01 : 0.22, delay: morph ? 0.08 : 0 }}>
    <header className="detail-top"><div className="detail-kicker">{memory && <span className="tag">{categoryLabel(memory.category)}</span>}{memory && <span className="detail-version">版本 {memory.revision}</span>}</div><button className="icon-button" aria-label="关闭详情" onClick={close}><Icon name="close"/></button></header>
    <div className="panel-body detail-body">
      <ResourceNotice resource={resource}/>
      {!online && <Notice tone="warning">本地服务不可用。正在展示上次加载的数据；你的修改会保留。</Notice>}
      {error && <Notice tone="error"><div>{error}{conflict && <button className="button secondary" onClick={latest} disabled={busy || !online}>查看最新版本并保留输入</button>}</div></Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      {(copy || memory) && <>
        {!editing && <p className="detail-content">{copy || "正文暂时无法读取。"}</p>}
        {memory && !editing && <p className="detail-caption">{[memory.scope || "未限定场景", sharingLabel(memory), dateLabel(memory.created_at), memory.valid_until ? `有效期至 ${dateLabel(memory.valid_until)}` : ""].filter(Boolean).join(" · ")}</p>}
        {memory && !editing && <button className="button secondary detail-edit" disabled={!online || !!resource.error} onClick={() => { setDraft(memory.content || ""); setScope(memory.scope || ""); setOnlySelf(memory.share_enabled === false); setEditing(true); setNotice("") }}>编辑</button>}
        {memory && editing && <div className="detail-editor">
          <label className="field">记忆内容<textarea aria-label="纠正后的内容" value={draft} onChange={e => setDraft(e.target.value)} maxLength={2000} rows={5}/></label>
          <label className="field">适用场景<input aria-label="详情适用场景" value={scope} onChange={e => setScope(e.target.value)} placeholder="例如：正式报告"/></label>
          <label className="check-row"><input type="checkbox" checked={onlySelf} onChange={e => setOnlySelf(e.target.checked)}/>仅自己可见</label>
          <p className="helper">{onlySelf ? "这条记忆不会提供给任何 Agent。" : "允许已授权 Agent 读取；仍受类别、工具和有效期限制。"}</p>
          <div className="actions"><button className="button primary" disabled={busy || !draft.trim() || !online || conflict} onClick={save}>{busy ? "保存中…" : "保存修改"}</button><button className="button secondary" disabled={busy} onClick={() => { if (canLeave()) { setEditing(false); setDraft(memory.content || ""); setScope(memory.scope || ""); setOnlySelf(memory.share_enabled === false); setError("") } }}>取消编辑</button></div>
          <details className="disclosure"><summary>正在比较的当前版本 {memory.revision}</summary><p className="prose">{memory.content}</p></details>
        </div>}
        {memory && !editing && <div className="detail-more">
          <section className="detail-block"><h3>来源</h3>{(memory.source_ids || []).length ? memory.source_ids!.map(id => <SourceContent key={id} id={id}/>) : <p className="helper">没有关联来源。</p>}</section>
          <section className="detail-block"><h3>历史版本 <span className="count">{versions.length}</span></h3>{versions.length > 1 && <p className="helper">旧版本仅供回看，不作为当前记忆提供给 Agent。</p>}{versions.map(v => <details className="version-item" key={v.revision}><summary>版本 {v.revision}<span>{lifecycleLabel(v.lifecycle)}</span></summary><p className="prose">{v.content || "正文暂时无法读取。"}</p>{v.scope && <p className="helper">适用场景：{v.scope}</p>}</details>)}</section>
          <div className="detail-foot">{preview && <div className="notice warning"><p>删除后无法从知我里恢复这条记忆。若来源仍包含这段内容，确认后会整份删除这些来源；其他记忆会保留，并显示来源已删除。将删除 {preview.version_count} 个版本。</p>{preview.sources.length ? <ul>{preview.sources.map(item => <li key={item.id}>{sourceLabel(item.kind)}{item.name ? ` · ${item.name}` : ""}</li>)}</ul> : <p>没有仍包含这段内容的来源。</p>}<div className="actions"><button className="button danger" disabled={busy || !online} onClick={confirmDelete}>{busy ? "删除中…" : "确认永久删除"}</button><button className="button secondary" disabled={busy} onClick={() => setPreview(null)}>取消</button></div></div>}{!preview && <button className="button danger" disabled={!online || busy} onClick={openDelete}>永久删除</button>}</div>
        </div>}
      </>}
    </div>
    </motion.div>
    </motion.div>}
  </AnimatePresence>
}
export function SourceContent({ id }: { id: string }) {
  const source = useResource<Source>(() => api.source(id), [id])
  if (source.error === "source not found") return <p className="helper">来源已删除</p>
  return <div className="source-block"><ResourceNotice resource={source}/>{source.data && <details className="disclosure"><summary>{sourceLabel(source.data.kind)}{source.data.name ? ` · ${source.data.name}` : " · 查看来源文本"}</summary><p className="prose">{source.data.content}</p><p className="helper">{dateLabel(source.data.imported_at)}</p></details>}</div>
}
