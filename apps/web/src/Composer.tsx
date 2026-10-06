import { t, useMessage } from "./i18n"
import { Dialog } from "@base-ui/react/dialog"
import { motion, useReducedMotion } from "motion/react"
import { useRef, useState } from "react"
import { api, explain, importMessage, type ImportJob } from "./api"
import { CATEGORIES } from "./format"
import { canLeave, Icon, Notice, useUnsaved } from "./ui"
import type { Service } from "./App"

export function Composer({ kind, service, onClose, onRefresh, onSaved, onReview }: { kind: "add" | "import"; service: Service; onClose: () => void; onRefresh: () => void; onSaved: (id?: string) => void; onReview: () => void }) {
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("preference")
  const [scope, setScope] = useState("")
  const [fileName, setFileName] = useState("")
  const [onlySelf, setOnlySelf] = useState(false)
  const [error, setError] = useMessage()
  const [busy, setBusy] = useState(false)
  const [reading, setReading] = useState(false)
  const [job, setJob] = useState<{ value: ImportJob; signature: string } | null>(null)
  const attempts = useRef(new Map<string, string>())
  const submitting = useRef(false), readId = useRef(0)
  const reduce = useReducedMotion()
  const importBody = fileName ? { kind: "file", name: fileName, text: content } : { kind: "paste", text: content }
  const signature = JSON.stringify(importBody)
  const sameJob = job?.signature === signature
  const online = service.status === "online"
  useUnsaved(!!content.trim() && !(kind === "import" && sameJob), busy || reading)
  const close = () => { if (canLeave()) onClose() }
  function keyFor(sig: string) { let key = attempts.current.get(sig); if (!key) { key = crypto.randomUUID(); attempts.current.set(sig, key) }; return key }
  async function selectFile(file?: File) {
    if (!file) return
    const id = ++readId.current
    setReading(true); setError("")
    try {
      if (!/\.(txt|md|markdown)$/i.test(file.name)) throw new Error(t("请选择 TXT 或 Markdown 文件，原来的草稿仍保留。"))
      if (file.size > 1024 * 1024) throw new Error(t("文件超过 1 MiB，请拆分后导入。原来的草稿仍保留。"))
      let text: string
      try { text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()) }
      catch { throw new Error(t("文件不是有效的 UTF-8 文本，请转换编码后重试。原来的草稿仍保留。")) }
      if (!text.trim()) throw new Error(t("文件内容为空，请选择有内容的文件。"))
      if (id === readId.current) { setContent(text); setFileName(file.name); setJob(null) }
    } catch (err) { if (id === readId.current) setError(() => (explain(err))) }
    finally { if (id === readId.current) setReading(false) }
  }
  async function submit(retry = false) {
    if (submitting.current || !online) return
    if (!content.trim()) { setError(() => (t("请先填写内容。"))); return }
    if (kind === "import" && new TextEncoder().encode(content).byteLength > 1024 * 1024) { setError(() => (t("文本超过 1 MiB，请拆分后导入。"))); return }
    submitting.current = true; setBusy(true); setError("")
    try {
      if (kind === "add") {
        const body = { content, kind: category === "event" ? "event" : "fact", category, scope: scope.trim() || null, share_enabled: !onlySelf }
        const saved = await api.createMemory(body, keyFor(JSON.stringify(body))) as { status?: string; memory_id?: string }
        if (saved.status !== "accepted" || !saved.memory_id) throw new Error(t("服务没有确认保存结果，请重试核对。"))
        onSaved(saved.memory_id)
      } else {
        const result = retry && sameJob && job ? await api.retryImport(job.value.job_id) : await api.importSource(importBody, keyFor(signature))
        setJob({ value: result, signature }); onRefresh()
      }
    } catch (err) { setError(() => (explain(err))) }
    finally { submitting.current = false; setBusy(false) }
  }
  return <Dialog.Root open modal onOpenChange={(open, details) => { if (!open) { if (!canLeave()) details.cancel(); else onClose() } }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="dialog-backdrop"/>
      <Dialog.Popup className="modal material" render={<motion.div initial={reduce ? false : { y: 16, opacity: 0.7 }} animate={{ y: 0, opacity: 1 }} transition={reduce ? { duration: 0.2 } : { type: "spring", bounce: 0, duration: 0.4 }}/>}>
        <form aria-labelledby="composer-title" onSubmit={e => { e.preventDefault(); void submit() }}>
    <header className="panel-heading"><div><span className="eyebrow">{kind === "add" ? t("由你确认，即刻生效") : t("先保存来源，再由你确认")}</span><h2 id="composer-title">{kind === "add" ? t("添加记忆") : t("导入文本")}</h2></div><button className="icon-button" aria-label={t("关闭添加或导入")} type="button" disabled={busy || reading} onClick={close}><Icon name="close"/></button></header>
    <div className="modal-body">
      {!online && <Notice tone="warning">{t("本地服务不可用。草稿已保留，请恢复连接后重试。")}</Notice>}
      {kind === "add" ? <div className="form-grid"><label className="field">{t("主题")}<select aria-label={t("添加主题")} value={category} onChange={e => setCategory(e.target.value)} disabled={busy}>{CATEGORIES().map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label className="field">{t("适用场景")} <span className="helper">{t("可选")}</span><input value={scope} onChange={e => setScope(e.target.value)} placeholder={t("例如：日常沟通")} disabled={busy}/></label></div> : <>
        <p className="helper">{t("粘贴一段对话，或选择 UTF-8 TXT / Markdown 文件。单次最多 1 MiB。")}</p>
        <label className="file-picker">{t("选择文本文件")}<input aria-label={t("文本或 Markdown 文件")} accept=".txt,.md,.markdown" type="file" disabled={busy || reading} onChange={e => { void selectFile(e.target.files?.[0]); e.target.value = "" }}/></label>
        {reading && <p role="status">{t("正在读取文件…")}</p>}{fileName && <p className="helper">{t("已选择：")}{fileName}</p>}
        <div className="privacy-note">{service.extractor === true && online ? t("导入后，会将这次选择的文本发送给已配置的提取模型。生成的建议仍需你确认。") : service.extractor === false && online ? t("提取模型未配置。带标题或列表的文本会按结构拆成候选，不调用模型；其他文本只保存来源。") : t("暂时无法确认提取模型配置。恢复服务后再导入。")}{service.testMode && t(" 当前为合成数据测试环境。")}</div>
      </>}
      <label className="field">{kind === "add" ? t("记忆内容") : t("来源文本")}<textarea aria-label={kind === "add" ? t("记忆内容") : t("来源文本")} value={content} onChange={e => setContent(e.target.value)} rows={6} maxLength={kind === "add" ? 2000 : undefined} placeholder={kind === "add" ? t("例如：日常沟通时，我更喜欢简洁、直接的回答。") : t("在这里粘贴需要整理的文本…")} disabled={busy || reading}/></label>
      {kind === "add" && <><label className="check-row"><input type="checkbox" checked={onlySelf} disabled={busy} onChange={e => setOnlySelf(e.target.checked)}/>{t("仅自己可见")}</label><p className="helper">{onlySelf ? t("所有 Agent 都无法读取这条记忆。") : t("允许已授权 Agent 读取；新建连接仍默认无权限。")}</p></>}
      {error && <Notice tone="error">{error}</Notice>}
      {sameJob && job && <Notice tone={job.value.status === "extracted" ? "success" : "warning"}>{importMessage(job.value, service.testMode)}</Notice>}
    </div>
    <footer className="modal-footer"><button className="button secondary" type="button" onClick={close} disabled={busy || reading}>{sameJob ? t("完成") : t("取消")}</button><div className="actions">{sameJob && job?.value.status === "extracted" ? <button className="button primary" type="button" onClick={onReview}>{t("前往待确认")}</button> : <button className="button primary" type={sameJob ? "button" : "submit"} onClick={sameJob ? () => void submit(true) : undefined} disabled={!online || busy || reading || !content.trim()}>{busy ? (kind === "add" ? t("保存中…") : t("正在保存与提取…")) : kind === "add" ? t("确认保存") : sameJob ? t("重试提取") : t("保存来源并提取")}</button>}</div></footer>
  </form>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}
