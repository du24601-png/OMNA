import { useEffect, useMemo, useRef, useState } from "react"
import { ApiError, api, explain, type Proposal } from "./api"
import { SourceContent } from "./Detail"
import { categoryLabel, dateLabel, sharingLabel, sourceLabel } from "./format"
import { canLeave, Empty, Notice, PageTitle, ResourceNotice, useResource, useUnsaved } from "./ui"

export function ReviewPage({ tick, online, onOpen, onSaved }: { tick: number; online: boolean; onOpen: (id: string) => void; onSaved: () => void }) {
  const resource = useResource(() => api.proposals(), [tick])
  const [selected, setSelected] = useState<string | null>(null)
  const [finished, setFinished] = useState<string[]>([])
  const [notice, setNotice] = useState<{ text: string; id?: string } | null>(null)
  const items = (resource.data?.proposals || []).filter(p => !finished.includes(p.id))
  const proposal = items.find(p => p.id === selected) || items[0]
  return <div className="page"><PageTitle title="待确认" description="先看清变化，再决定要记住什么。"/><ResourceNotice resource={resource}/>
    {notice && <Notice tone="success"><span>{notice.text}</span>{notice.id && <button className="text-button" onClick={() => onOpen(notice.id!)}>查看正式记忆</button>}</Notice>}
    {resource.data && !items.length && !resource.loading && !resource.error && <Empty title="当前没有待确认的记忆">新的导入内容或 Agent 建议，会先来到这里。未经确认，不会改变你的记忆。</Empty>}
    {!!items.length && <div className="review-layout"><div className="proposal-list"><p className="eyebrow">等待你的决定 · {items.length}</p>{items.map(item => <button key={item.id} className={`proposal-item ${proposal?.id === item.id ? "selected" : ""}`} onClick={() => { if (item.id !== proposal?.id && canLeave()) setSelected(item.id) }}><span className="metadata"><span className="tag">{item.target_id ? "更新建议" : "新增建议"}</span><span>{categoryLabel(item.payload.category)}</span></span><p>{item.payload.content}</p><small>{sourceLabel(item.source.kind)}{item.demo ? " · 合成演示数据" : ""}</small></button>)}</div>
      {proposal && <ReviewCard key={proposal.id} proposal={proposal} online={online && !resource.error} onDone={(id, result) => { setFinished(old => [...old, id]); setNotice(result); resource.reload(); onSaved() }}/>}</div>}
  </div>
}

function ReviewCard({ proposal, online, onDone }: { proposal: Proposal; online: boolean; onDone: (id: string, result: { text: string; id?: string }) => void }) {
  const [targetId, setTargetId] = useState(proposal.target_id || "")
  const [baseRevision, setBaseRevision] = useState<number | null>(proposal.base_revision)
  const [mode, setMode] = useState<"" | "update" | "keep_both">("")
  const [edited, setEdited] = useState(proposal.payload.content)
  const [editing, setEditing] = useState(false)
  const [scope, setScope] = useState(proposal.payload.scope || "")
  const [onlySelf, setOnlySelf] = useState(proposal.payload.share_enabled === false)
  const [shareTouched, setShareTouched] = useState(false)
  const [expiry, setExpiry] = useState("")
  const [expiryTouched, setExpiryTouched] = useState(false)
  const [error, setError] = useState("")
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const attempt = useRef<{ sig: string; key: string } | null>(null)
  const running = useRef(false)
  const targets = useResource(() => api.memories({ state: "current", limit: 50 }), [])
  const target = useResource(() => targetId ? api.memory(targetId) : Promise.resolve(null), [targetId])
  const current = target.data?.id === targetId ? target.data : null
  const inherits = !!targetId && mode !== "keep_both"
  const dirty = edited !== proposal.payload.content || scope !== (proposal.payload.scope || "") || shareTouched || expiryTouched
  useUnsaved(dirty, busy)
  useEffect(() => {
    if (!current) return
    // An existing proposal retains its original base revision until the user explicitly reloads it.
    if (baseRevision === null) setBaseRevision(current.revision)
  }, [current])
  useEffect(() => {
    if (!shareTouched) setOnlySelf(inherits && current ? current.share_enabled === false : proposal.payload.share_enabled === false)
    if (!expiryTouched) setExpiry(toLocal(inherits && current ? current.valid_until : proposal.payload.valid_until))
  }, [current, inherits, shareTouched, expiryTouched])
  async function latest() {
    setBusy(true)
    try { const value = await api.memory(targetId); setBaseRevision(value.revision); target.reload(); setConflict(false); setError(""); setNotice("已重新读取当前版本。你的修改仍保留，请核对差异后再决定。") }
    catch (err) { setError(explain(err)) } finally { setBusy(false) }
  }
  async function submit(reject = false) {
    if (running.current) return
    let body: Record<string, unknown> = { decision: "reject" }
    if (!reject) {
      if (!edited.trim()) { setError("记忆内容不能为空。"); return }
      if (targetId && !mode) { setError("请先选择更新原记忆或两者保留。"); return }
      if (mode === "keep_both" && !scope.trim()) { setError("两者保留需要填写适用场景。"); return }
      const decision = targetId ? mode : (edited !== proposal.payload.content || scope !== (proposal.payload.scope || "") ? "edit" : "accept")
      body = { decision, content: edited.trim() }
      if (scope.trim()) body.scope = scope.trim()
      if (targetId && mode === "update") {
        if (!current || baseRevision === null) { setError("当前记忆还未加载成功，请重试。"); return }
        body.target_id = targetId; body.base_revision = baseRevision
      }
      if (shareTouched) body.share_enabled = !onlySelf
      if (expiryTouched) body.valid_until = expiry ? new Date(expiry).toISOString() : null
    }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    running.current = true; setBusy(true); setError("")
    try {
      const result = await api.decide(proposal.id, body, attempt.current.key)
      if (result.status !== "accepted" && result.status !== "rejected") throw new Error("服务未确认审核结果，请重试核对。")
      onDone(proposal.id, { text: result.status === "rejected" ? "已拒绝，正式记忆没有改变。" : "已确认保存，画像与记忆列表已更新。", id: result.memory_id })
    } catch (err) { setError(explain(err)); setConflict(err instanceof ApiError && err.code === "CONFLICT") }
    finally { running.current = false; setBusy(false) }
  }
  return <section className="review-card surface"><header className="review-heading"><div><span className="eyebrow">{proposal.target_id ? "已有记忆的新建议" : "值得留下的新内容"}</span><h2>{targetId ? "这次要如何更新？" : "要把这条内容记住吗？"}</h2></div><span className="tag">{categoryLabel(proposal.payload.category)}</span></header>
    {proposal.demo && <p className="demo-note">合成演示数据，候选不代表真实云模型提取。</p>}
    {targetId && <ResourceNotice resource={target}/>}
    {targetId && current ? <Diff before={current.content || ""} after={edited}/> : !targetId && <div className="suggestion"><span className="eyebrow">建议内容</span><p className="prose">{edited}</p></div>}
    {current && baseRevision !== current.revision && <Notice tone="warning"><span>建议基于版本 {baseRevision}，当前已是版本 {current.revision}。请先核对最新版本。</span><button className="text-button" onClick={latest} disabled={busy || !online}>查看最新差异</button></Notice>}
    <div className="evidence-box"><h3>依据与来源</h3><blockquote>{proposal.evidence.text || "没有可用证据片段，请谨慎核对。"}</blockquote><SourceContent id={proposal.source.id}/></div>
    <details className="disclosure"><summary>{targetId ? "已关联当前记忆 · 更换关联" : "这条建议与已有记忆有关？"}</summary><ResourceNotice resource={targets}/><label className="field">关联的当前记忆<select aria-label="关联的当前记忆" disabled={busy || !!targets.error} value={targetId} onChange={e => { setTargetId(e.target.value); setBaseRevision(null); setMode(""); setShareTouched(false); setExpiryTouched(false); setConflict(false); setError("") }}><option value="">作为新增内容</option>{proposal.target_id && !targets.data?.items.some(m => m.id === proposal.target_id) && <option value={proposal.target_id}>建议关联的记忆</option>}{targets.data?.items.map(m => <option key={m.id} value={m.id}>{m.content?.slice(0, 48)} · 版本 {m.revision}</option>)}</select></label></details>
    {targetId && <fieldset className="decision-options"><legend>选择处理方式</legend><label className={mode === "update" ? "chosen" : ""}><input type="radio" name={`mode-${proposal.id}`} checked={mode === "update"} disabled={busy} onChange={() => setMode("update")}/><span><strong>更新原记忆</strong><small>建议成为当前版本，旧版本仍可回看。</small></span></label><label className={mode === "keep_both" ? "chosen" : ""}><input type="radio" name={`mode-${proposal.id}`} checked={mode === "keep_both"} disabled={busy} onChange={() => setMode("keep_both")}/><span><strong>两者保留</strong><small>另存一条记忆，为新内容注明适用场景。</small></span></label></fieldset>}
    {(mode === "keep_both" || editing) && <label className="field">适用场景{mode === "keep_both" ? "（必填）" : "（可选）"}<input aria-label="审核适用场景" value={scope} onChange={e => setScope(e.target.value)} disabled={busy} placeholder="例如：正式报告"/></label>}
    {!editing && scope && mode !== "keep_both" && <p className="helper">适用场景：{scope}</p>}
    {editing && <label className="field">修改后的内容<textarea aria-label="修改后的内容" rows={4} maxLength={2000} value={edited} disabled={busy} onChange={e => setEdited(e.target.value)}/></label>}
    <details className="disclosure sharing-options"><summary>读取范围与有效期 · {sharingLabel({ share_enabled: !onlySelf })}</summary><label className="check-row"><input type="checkbox" checked={onlySelf} disabled={busy} onChange={e => { setOnlySelf(e.target.checked); setShareTouched(true) }}/>仅自己可见</label><label className="field">有效期至（留空为不限）<input type="datetime-local" value={expiry} disabled={busy} onChange={e => { setExpiry(e.target.value); setExpiryTouched(true) }}/></label>{inherits && <p className="helper">未修改时，沿用当前记忆的读取范围和有效期{current?.valid_until ? `（${dateLabel(current.valid_until)}）` : ""}。</p>}</details>
    {notice && <Notice>{notice}</Notice>}{error && <Notice tone="error"><div>{error}{conflict && targetId && <button className="button secondary" disabled={busy || !online} onClick={latest}>查看最新版本并保留输入</button>}</div></Notice>}
    <footer className="review-actions"><button className="button danger" disabled={busy || !online} onClick={() => void submit(true)}>拒绝</button><div className="actions"><button className="button secondary" disabled={busy} onClick={() => setEditing(!editing)}>{editing ? "收起编辑" : "编辑内容"}</button><button className="button primary" disabled={busy || !online || (!!targetId && (!mode || !current || target.loading || !!target.error || current.revision !== baseRevision)) || conflict} onClick={() => void submit()}>{busy ? "保存中…" : targetId ? mode === "keep_both" ? "确认两者保留" : "确认更新" : "确认保存"}</button></div></footer>
  </section>
}
function toLocal(value?: string | null) { if (!value) return ""; const date = new Date(value); if (!Number.isFinite(date.getTime())) return ""; return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0,16) }

function Diff({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => diff(before, after), [before, after])
  return <div className="diff"><div><h3>当前记忆 <span>移除处有删除线</span></h3><p className="prose">{parts.old.map((p,i) => p.changed ? <del key={i}>{p.text}</del> : <span key={i}>{p.text}</span>)}</p></div><div><h3>建议内容 <span>新增处有下划线</span></h3><p className="prose">{parts.next.map((p,i) => p.changed ? <ins key={i}>{p.text}</ins> : <span key={i}>{p.text}</span>)}</p></div><p className="diff-caption">变化点：仅标出移除与新增的词句。其余内容保持原样。</p></div>
}
function diff(before: string, after: string) {
  const a = Array.from(before), b = Array.from(after), width = b.length + 1
  const matrix = new Uint16Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) matrix[i*width+j] = a[i] === b[j] ? matrix[(i+1)*width+j+1]+1 : Math.max(matrix[(i+1)*width+j], matrix[i*width+j+1])
  const old: { text: string; changed: boolean }[] = [], next: typeof old = []
  const add = (arr: typeof old, text: string, changed: boolean) => { if (arr.length && arr[arr.length-1].changed === changed) arr[arr.length-1].text += text; else arr.push({text,changed}) }
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { add(old,a[i],false); add(next,b[j],false); i++;j++ }
    else if (i < a.length && (j === b.length || matrix[(i+1)*width+j] >= matrix[i*width+j+1])) add(old,a[i++],true)
    else add(next,b[j++],true)
  }
  return {old,next}
}
