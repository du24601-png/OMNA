import { Dialog } from "@base-ui/react/dialog"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { ApiError, api, explain, type SettingsView } from "./api"
import { loadTheme, saveTheme, type Theme, type ThemeAccent, type ThemeMode } from "./theme"
import { Icon, Notice, ResourceNotice, useResource, useUnsaved } from "./ui"

type Section = "model" | "library" | "backup" | "reset" | "theme"
const GROUPS: [string, [Section, string][]][] = [
  ["智能", [["model", "提取模型"]]],
  ["记忆", [["library", "本机数据"], ["backup", "备份"], ["reset", "清空"]]],
  ["外观", [["theme", "主题"]]],
]
const ACCENTS: [ThemeAccent, string, string, string][] = [["orange", "橙", "#c2410c", "#ff8a4c"], ["blue", "蓝", "#1d4ed8", "#0a84ff"], ["green", "绿", "#15803d", "#30d158"], ["violet", "紫", "#6d28d9", "#bf5af2"]]

export function SettingsDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const reduce = useReducedMotion()
  const resource = useResource(() => api.settings(), [])
  const [section, setSection] = useState<Section>("model")
  return <Dialog.Root open modal onOpenChange={(open, details) => { if (!open) { details.cancel(); onClose() } }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="dialog-backdrop"/>
      <Dialog.Popup className="settings-dialog material" aria-labelledby="settings-title" render={<motion.div initial={reduce ? false : { y: 12, opacity: 0.86 }} animate={{ y: 0, opacity: 1 }} transition={reduce ? { duration: 0.16 } : { type: "spring", bounce: 0, duration: 0.38 }}/>}>
        <h2 id="settings-title" className="settings-name">设置</h2>
        <button className="icon-button settings-close" type="button" aria-label="关闭设置" onClick={onClose}><Icon name="close"/></button>
        <div className="settings-body">
          <div className="settings-nav" role="tablist" aria-label="设置分区">{GROUPS.map(([group, items]) => <div key={group} className="settings-group"><p className="settings-nav-label">{group}</p>{items.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={section === id} onClick={() => setSection(id)}>{label}</button>)}</div>)}</div>
          <div className="settings-pane" role="tabpanel">
            {section !== "theme" && <ResourceNotice resource={resource}/>}
            {resource.data && <SettingsForm section={section} initial={resource.data} onChanged={() => { resource.reload(); onChanged() }}/>}
            {section === "theme" && <ThemePane/>}
          </div>
        </div>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}

function SettingsForm({ section, initial, onChanged }: { section: Section; initial: SettingsView; onChanged: () => void }) {
  const [baseUrl, setBaseUrl] = useState(initial.extractor.base_url)
  const [model, setModel] = useState(initial.extractor.model)
  const [apiKey, setApiKey] = useState("")
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState("")
  const [restoreWord, setRestoreWord] = useState("")
  const [resetWord, setResetWord] = useState("")
  const file = useRef<HTMLInputElement>(null)
  const savedKey = initial.extractor.key_saved
  const dirty = baseUrl !== initial.extractor.base_url || model !== initial.extractor.model || apiKey !== ""
  useUnsaved(dirty, !!busy)
  useEffect(() => { setNotice(""); setError("") }, [section])
  if (section === "theme") return null

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy("save"); setError(""); setNotice("")
    const body: { extractor_base_url: string; extractor_model: string; extractor_api_key?: string } = {
      extractor_base_url: baseUrl.trim(),
      extractor_model: model.trim(),
    }
    if (apiKey) body.extractor_api_key = apiKey
    try {
      await api.saveSettings(body, crypto.randomUUID())
      setApiKey("")
      setNotice(apiKey ? "已保存。密钥只留在本机，页面不会再显示它。" : "已保存。密钥没有改动。")
      onChanged()
    } catch (err) { setError(settingsMessage(err)) } finally { setBusy("") }
  }

  async function test() {
    if (busy) return
    setBusy("test"); setError(""); setNotice("")
    try {
      await api.testModel(crypto.randomUUID())
      setNotice("已连上提取服务。这次没有发送任何记忆内容。")
    } catch (err) { setError(settingsMessage(err)) } finally { setBusy("") }
  }

  async function exportLibrary() {
    if (busy) return
    setBusy("export"); setError(""); setNotice("")
    try { await api.downloadExport(); setNotice("导出文件已开始下载。里面没有密钥和凭证。") }
    catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  async function backup() {
    if (busy) return
    setBusy("backup"); setError(""); setNotice("")
    try { await api.downloadBackup(); setNotice("备份已开始下载。请把它保存到你自己选择的位置。提取密钥不在备份里。") }
    catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  async function restore() {
    const chosen = file.current?.files?.[0]
    if (!chosen || busy || restoreWord !== "恢复备份") return
    setBusy("restore"); setError(""); setNotice("")
    try {
      await api.restoreBackup(await chosen.arrayBuffer(), crypto.randomUUID())
      setRestoreWord("")
      if (file.current) file.current.value = ""
      setNotice("已恢复。所有 Agent 已停用，旧凭证失效。请重新填写提取密钥。")
      onChanged()
    } catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  async function reset() {
    if (busy || resetWord !== "清空数据") return
    setBusy("reset"); setError(""); setNotice("")
    try {
      await api.resetData(crypto.randomUUID())
      setResetWord("")
      setNotice("已清空。你另外保存的备份文件还在。")
      onChanged()
    } catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  return <>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {section === "model" && <form onSubmit={save}>
      <div className="settings-title-row"><h2>提取模型</h2><span className={`settings-status ${initial.extractor.configured ? "ready" : ""}`}>{initial.extractor.configured ? "可以提取" : "还不能提取"}</span></div>
      <p className="helper">导入时，只有你这次提交的文字会发给这个模型。</p>
      <label className="field">服务地址<input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} autoComplete="off" placeholder="https://example.com/v1"/></label>
      <label className="field">模型名<input value={model} onChange={e => setModel(e.target.value)} autoComplete="off"/></label>
      <label className="field">密钥
        <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="new-password" placeholder={savedKey ? "已保存，留空表示不修改" : "尚未保存"}/>
      </label>
      <p className="helper">{savedKey ? "密钥已保存在本机，页面不会显示它。" : "还没有在这里保存过密钥。"}</p>
      <div className="actions">
        <button className="button primary" disabled={!!busy}>{busy === "save" ? "保存中…" : "保存"}</button>
        <button className="button secondary" type="button" disabled={!!busy || !initial.extractor.configured} onClick={test}>{busy === "test" ? "测试中…" : "测试连接"}</button>
      </div>
    </form>}
    {section === "library" && <div>
      <h2>本机数据</h2>
      <p className="helper">记忆库存放在这个目录。这里只展示位置，不会搬动正在使用的库。</p>
      <p className="path-line">{initial.data_dir}</p>
      <div className="settings-block">
        <h3>隐私</h3>
        <p className="helper">记忆库、来源、提案、权限和访问记录都保存在本机。默认在本机检索。选择云端模型提取时，只发送你这次提交的文字，并说明接收方。授权某个 Agent 后，返回给它的记忆可能离开这台设备。知我不会宣传无论怎样配置都全程离线，也不会监控剪贴板、聊天窗口或系统活动，不会默认上传使用记录。备份保存在你选择的位置；旧备份和外部 Agent 里的副本需要你另行管理。</p>
      </div>
    </div>}
    {section === "backup" && <div>
      <h2>备份</h2>
      <p className="helper">导出包含已确认的记忆、版本和来源关联，不含密钥、凭证和已删除的正文。备份是整份记忆库的副本，同样不含提取密钥。</p>
      <div className="actions">
        <button className="button secondary" disabled={!!busy} onClick={exportLibrary}>{busy === "export" ? "导出中…" : "导出"}</button>
        <button className="button secondary" disabled={!!busy} onClick={backup}>{busy === "backup" ? "备份中…" : "备份"}</button>
      </div>
      <div className="settings-block">
        <h3>从备份恢复</h3>
        <p className="helper">只接受同一版本的知我备份。校验失败时，当前库不会被替换。恢复成功后，全部 Agent 停用，旧凭证失效，提取密钥需要重新填写。</p>
        <label className="field">备份文件<input ref={file} type="file" accept=".zip,application/zip"/></label>
        <label className="field">输入「恢复备份」以确认<input value={restoreWord} onChange={e => setRestoreWord(e.target.value)} autoComplete="off"/></label>
        <button className="button danger" disabled={!!busy || restoreWord !== "恢复备份"} onClick={restore}>{busy === "restore" ? "恢复中…" : "恢复备份"}</button>
      </div>
    </div>}
    {section === "reset" && <div>
      <h2>清空数据</h2>
      <ul className="helper settings-impact">
        <li>已确认的记忆、版本、来源和待确认内容会被删除。</li>
        <li>Agent 连接和访问记录会被删除。</li>
        <li>在这里保存过的提取配置会被清除。</li>
        <li>你另外保存的备份文件不会删除。</li>
      </ul>
      <label className="field">输入「清空数据」以确认<input value={resetWord} onChange={e => setResetWord(e.target.value)} autoComplete="off"/></label>
      <button className="button danger" disabled={!!busy || resetWord !== "清空数据"} onClick={reset}>{busy === "reset" ? "清空中…" : "清空数据"}</button>
    </div>}
  </>
}

function ThemePane() {
  const [theme, setTheme] = useState<Theme>(() => loadTheme())
  const choose = (next: Theme) => { setTheme(next); saveTheme(next) }
  const setMode = (mode: ThemeMode) => choose({ ...theme, mode })
  const setAccent = (accent: ThemeAccent) => choose({ ...theme, accent })
  return <div>
    <h2>主题</h2>
    <p className="helper">只改变这台设备上的界面，不影响记忆内容。</p>
    <h3>明暗</h3>
    <div className="segmented" role="group" aria-label="明暗">
      <button type="button" aria-pressed={theme.mode === "light"} onClick={() => setMode("light")}>浅色</button>
      <button type="button" aria-pressed={theme.mode === "dark"} onClick={() => setMode("dark")}>深色</button>
    </div>
    <div className="settings-block">
      <h3>主色</h3>
      <div className="theme-swatches" role="radiogroup" aria-label="主色">{ACCENTS.map(([id, label, light, dark]) => <button key={id} type="button" role="radio" className="theme-swatch" aria-checked={theme.accent === id} onClick={() => setAccent(id)}><i style={{ background: theme.mode === "dark" ? dark : light }}/>{label}</button>)}</div>
    </div>
  </div>
}

function settingsMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "MODEL_UNAVAILABLE") return "提取服务没有连上。请检查地址、模型名和密钥。密钥没有显示在页面上。"
  if (error instanceof ApiError && error.code === "TIMEOUT") return "提取服务没有在时间内响应。"
  return explain(error)
}
