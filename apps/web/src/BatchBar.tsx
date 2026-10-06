import { t, useMessage } from "./i18n"
// Top of the pending list: a batch (one import, or one agent organizing its
// instruction file) with two or more plain additions can be remembered in
// one go; right after, the same place offers to undo that batch.
import { AlertDialog } from "@base-ui/react/alert-dialog"
import { useEffect, useRef, useState } from "react"
import { explain, type Memory, type Proposal } from "./api"
import { carryOut, loadLibrary, sortCandidates, undoBatches } from "./batch"

type Done = { batch: string; label: string; count: number }

// Kept outside the component so switching filters doesn't lose the undo offer.
let recent: Done[] = []

function labelOf(proposal: Proposal) {
  if (proposal.source.kind === "agent_claim") return t("{0} 整理的", [proposal.requester?.name || "Agent"])
  return proposal.source.name ? t("「{0}」里的", [proposal.source.name]) : t("这次导入的")
}

export function BatchBar({ proposals, tick, online, onChanged }: { proposals: Proposal[]; tick: number; online: boolean; onChanged: () => void }) {
  const [library, setLibrary] = useState<Memory[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useMessage()
  const [done, setDone] = useState<Done[]>(recent)
  const [confirm, setConfirm] = useState<Done | null>(null)
  const keys = useRef(new Map<string, string>())
  const keyFor = (sig: string) => { let key = keys.current.get(sig); if (!key) { key = crypto.randomUUID(); keys.current.set(sig, key) } return key }
  useEffect(() => {
    let alive = true
    loadLibrary().then(value => { if (alive) setLibrary(value) }).catch(() => { if (alive) setLibrary(null) })
    return () => { alive = false }
  }, [tick])
  const update = (next: Done[]) => { recent = next; setDone(next) }

  const ids = [...new Set(proposals.map(item => item.batch_id).filter((id): id is string => !!id))]
  const groups = library ? ids.map(id => {
    const plain = sortCandidates(proposals, new Set([id]), library).filter(item => item.lane === "plain")
    return { id, plain, label: plain[0] ? labelOf(plain[0].proposal) : "", closed: plain.some(item => item.proposal.source.kind !== "agent_claim") }
  }).filter(group => group.plain.length >= 2 && !done.some(item => item.batch === group.id)) : []

  async function rememberAll(group: (typeof groups)[number]) {
    if (busy) return
    setBusy(group.id); setError("")
    try {
      // Only the suggestions on screen; an agent may still be adding more.
      const outcome = await carryOut({ keep: group.plain, drop: [], categories: {}, choices: [], closed: group.closed ? new Set([group.id]) : new Set() }, keyFor)
      if (outcome.accepted) update([{ batch: group.id, label: group.label, count: outcome.accepted }, ...done.filter(item => item.batch !== group.id)])
      if (outcome.errors.length) setError(() => (t("{0} 条没有完成：{1} 再点一次会接着做，不会重复记住。", [outcome.errors.length, outcome.errors[0]])))
      onChanged()
    } catch (err) { setError(() => (explain(err))) }
    finally { setBusy(null) }
  }
  async function undo(item: Done) {
    setBusy(item.batch); setError("")
    try {
      await undoBatches([item.batch])
      update(done.filter(other => other.batch !== item.batch))
      setConfirm(null)
      onChanged()
    } catch (err) { setError(() => (explain(err))); setConfirm(null) }
    finally { setBusy(null) }
  }

  if (!groups.length && !done.length && !error) return null
  return <div className="batch-bars">
    {groups.map(group => <div key={group.id} className="batch-bar">
      <span className="batch-grow">{group.label} {group.plain.length} {t("条新增可以一起记住，很像的和修改仍要逐条看。")}</span>
      <button type="button" className="button primary" disabled={!online || !!busy} onClick={() => void rememberAll(group)}>{busy === group.id ? t("正在记住…") : t("记住 {0} 条", [group.plain.length])}</button>
    </div>)}
    {done.map(item => <div key={item.batch} className="batch-bar quiet">
      <span className="batch-grow">{t("已记住")}{item.label} {item.count} {t("条。")}</span>
      <button type="button" className="text-button" disabled={!online || !!busy} onClick={() => setConfirm(item)}>{t("撤销这次导入")}</button>
      <button type="button" className="text-button batch-dismiss" onClick={() => update(done.filter(other => other.batch !== item.batch))}>{t("知道了")}</button>
    </div>)}
    {error && <p className="inbox-error" role="alert">{error}</p>}
    <AlertDialog.Root open={!!confirm} onOpenChange={open => { if (!open && !busy) setConfirm(null) }}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className="dialog-backdrop detail-alert-backdrop"/>
        <AlertDialog.Popup className="detail-alert material">
          <AlertDialog.Title className="detail-alert-title">{t("撤销这次导入")}</AlertDialog.Title>
          <AlertDialog.Description className="detail-alert-copy">{t("会永久删除这批记住的")} {confirm?.count} {t("条记忆，删除后不能恢复。导入的原文和这批还没处理的建议也会一起删除。其中有之后又改过的，整批撤销会被拒绝，不会删一半。")}</AlertDialog.Description>
          <div className="actions detail-alert-actions">
            <AlertDialog.Close className="button secondary" disabled={!!busy}>{t("取消")}</AlertDialog.Close>
            <button type="button" className="button danger" disabled={!!busy} onClick={() => confirm && void undo(confirm)}>{busy ? t("正在撤销…") : t("删除这 {0} 条", [confirm?.count])}</button>
          </div>
        </AlertDialog.Popup>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  </div>
}
