import { Dialog } from "@base-ui/react/dialog"
import { Database, FolderArchive, Monitor, Moon, Palette, Sparkles, Sun, Trash2 } from "lucide-react"
import { motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { ApiError, api, explain, type SettingsView } from "./api"
import { loadTheme, saveTheme, type ThemeMode } from "./theme"
import { Icon, Notice, ResourceNotice, useResource, useUnsaved } from "./ui"

type Section = "appearance" | "model" | "library" | "backup" | "reset"
const NAV: [Section, string, typeof Sun][] = [
  ["appearance", "外观", Palette],
  ["model", "提取模型", Sparkles],
  ["library", "本机数据", Database],
  ["backup", "备份", FolderArchive],
  ["reset", "清空", Trash2],
]
const MODES: [ThemeMode, string, typeof Sun][] = [["light", "浅色", Sun], ["dark", "深色", Moon], ["system", "遵循系统", Monitor]]

export function SettingsDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const reduce = useReducedMotion()
  const resource = useResource(() => api.settings(), [])
  const [section, setSection] = useState<Section>("appearance")
  return <Dialog.Root open modal onOpenChange={(open, details) => { if (!open) { details.cancel(); onClose() } }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="dialog-backdrop"/>
      <Dialog.Popup className="settings-dialog material" aria-labelledby="settings-title" render={<motion.div initial={reduce ? false : { y: 8, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={reduce ? { duration: 0 } : { duration: 0.16 }}/>}>
        <h2 id="settings-title" className="settings-name">设置</h2>
        <button className="icon-button settings-close" type="button" aria-label="关闭设置" onClick={onClose}><Icon name="close"/></button>
        <div className="settings-body">
          <div className="settings-nav">
            <div role="tablist" aria-label="设置分区">{NAV.map(([id, label, Glyph]) => <button key={id} type="button" role="tab" aria-selected={section === id} onClick={() => setSection(id)}><Glyph size={16} strokeWidth={1.75} aria-hidden="true"/>{label}</button>)}</div>
          </div>
          <div className="settings-pane" role="tabpanel">
            {section === "appearance" ? <Appearance/> : <>
              <ResourceNotice resource={resource}/>
              {resource.data && <SettingsForm section={section} initial={resource.data} onChanged={() => { resource.reload(); onChanged() }}/>}
            </>}
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

  async function chooseDataDir() {
    if (busy) return
    setBusy("pick"); setError(""); setNotice("")
    try {
      const result = await api.pickDataDir()
      if (result.cancelled || !result.path) return
      await api.openDataDir(result.path)
      setNotice("已在资源管理器中打开这个文件夹。记忆库位置没有改。")
    } catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  async function openDataDir() {
    if (busy) return
    setBusy("open"); setError(""); setNotice("")
    try {
      await api.openDataDir()
      setNotice("已在资源管理器中打开这个文件夹。")
    } catch (err) { setError(explain(err)) } finally { setBusy("") }
  }

  return <>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {section === "model" && <form onSubmit={save}>
      <h2>提取模型</h2>
      <section className="settings-section">
        <p className="settings-section-label">连接</p>
        <div className="settings-card">
          <div className="settings-row">
            <span className="settings-row-label">状态</span>
            <span className={`settings-status ${initial.extractor.configured ? "ready" : ""}`}>{initial.extractor.configured ? "可以提取" : "还不能提取"}</span>
          </div>
          <label className="settings-row"><span className="settings-row-label">服务地址</span><input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} autoComplete="off" placeholder="https://example.com/v1"/></label>
          <label className="settings-row"><span className="settings-row-label">模型名</span><input value={model} onChange={e => setModel(e.target.value)} autoComplete="off"/></label>
          <label className="settings-row"><span className="settings-row-label">密钥</span><input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="new-password" placeholder={savedKey ? "已保存，留空表示不修改" : "尚未保存"}/></label>
        </div>
      </section>
      <div className="settings-actions">
        <button className="button" type="button" disabled={!!busy || !initial.extractor.configured} onClick={test}>{busy === "test" ? "测试中…" : "测试连接"}</button>
        <button className="button" disabled={!!busy}>{busy === "save" ? "保存中…" : "保存"}</button>
      </div>
    </form>}
    {section === "library" && <div>
      <h2>本机数据</h2>
      <section className="settings-section">
        <p className="settings-section-label">位置</p>
        <div className="settings-card">
          <div className="settings-row">
            <button className="settings-path" type="button" disabled={!!busy} onClick={openDataDir} title="在资源管理器中打开">{initial.data_dir}</button>
            <div className="settings-row-side">
              <button className="button" type="button" disabled={!!busy} onClick={openDataDir}>{busy === "open" ? "打开中…" : "打开"}</button>
              <button className="button" type="button" disabled={!!busy} onClick={chooseDataDir}>{busy === "pick" ? "选择中…" : "选择文件夹"}</button>
            </div>
          </div>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">隐私</p>
        <div className="settings-card settings-note">
          <p className="helper">记忆库、来源、提案、权限和访问记录都保存在本机。默认在本机检索。选择云端模型提取时，只发送你这次提交的文字，并说明接收方。授权某个 Agent 后，返回给它的记忆可能离开这台设备。OMNA 不会宣传无论怎样配置都全程离线，也不会监控剪贴板、聊天窗口或系统活动，不会默认上传使用记录。备份保存在你选择的位置；旧备份和外部 Agent 里的副本需要你另行管理。</p>
        </div>
      </section>
    </div>}
    {section === "backup" && <div>
      <h2>备份</h2>
      <section className="settings-section">
        <p className="settings-section-label">导出</p>
        <div className="settings-card">
          <div className="settings-row">
            <span className="settings-row-label">导出记忆</span>
            <button className="button" type="button" disabled={!!busy} onClick={exportLibrary}>{busy === "export" ? "导出中…" : "导出"}</button>
          </div>
          <div className="settings-row">
            <span className="settings-row-label">下载备份</span>
            <button className="button" type="button" disabled={!!busy} onClick={backup}>{busy === "backup" ? "备份中…" : "备份"}</button>
          </div>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">从备份恢复</p>
        <div className="settings-card">
          <label className="settings-row"><span className="settings-row-label">备份文件</span><input ref={file} type="file" accept=".zip,application/zip"/></label>
          <label className="settings-row"><span className="settings-row-label">输入「恢复备份」以确认</span><input value={restoreWord} onChange={e => setRestoreWord(e.target.value)} autoComplete="off"/></label>
        </div>
        <div className="settings-actions">
          <button className="button danger" type="button" disabled={!!busy || restoreWord !== "恢复备份"} onClick={restore}>{busy === "restore" ? "恢复中…" : "恢复备份"}</button>
        </div>
      </section>
    </div>}
    {section === "reset" && <div>
      <h2>清空数据</h2>
      <section className="settings-section">
        <p className="settings-section-label">会影响这些内容</p>
        <div className="settings-card settings-note">
          <ul className="helper settings-impact">
            <li>已确认的记忆、版本、来源和待确认内容会被删除。</li>
            <li>Agent 连接和访问记录会被删除。</li>
            <li>在这里保存过的提取配置会被清除。</li>
            <li>你另外保存的备份文件不会删除。</li>
          </ul>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">确认</p>
        <div className="settings-card">
          <label className="settings-row"><span className="settings-row-label">输入「清空数据」以确认</span><input value={resetWord} onChange={e => setResetWord(e.target.value)} autoComplete="off"/></label>
        </div>
        <div className="settings-actions">
          <button className="button danger" type="button" disabled={!!busy || resetWord !== "清空数据"} onClick={reset}>{busy === "reset" ? "清空中…" : "清空数据"}</button>
        </div>
      </section>
    </div>}
  </>
}

function Appearance() {
  return <div>
    <h2>外观</h2>
    <section className="settings-section">
      <p className="settings-section-label">界面</p>
      <div className="settings-card">
        <div className="settings-row">
          <span className="settings-row-label">明暗</span>
          <ModeSwitch/>
        </div>
        </div>
      </section>
  </div>
}

function ModeSwitch() {
  const [mode, setMode] = useState<ThemeMode>(() => loadTheme().mode)
  return <div className="mode-switch" role="group" aria-label="明暗">{MODES.map(([id, label, Glyph]) => <button key={id} type="button" aria-label={label} aria-pressed={mode === id} title={label} onClick={() => { setMode(id); saveTheme({ mode: id }) }}><Glyph size={16} strokeWidth={1.75} aria-hidden="true"/></button>)}</div>
}

function settingsMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "MODEL_UNAVAILABLE") return "提取服务没有连上。请检查地址、模型名和密钥。密钥没有显示在页面上。"
  if (error instanceof ApiError && error.code === "TIMEOUT") return "提取服务没有在时间内响应。"
  return explain(error)
}
