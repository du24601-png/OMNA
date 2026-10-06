import { t, useMessage, useLocale, setLocale, type Locale } from "./i18n"
import { Bell, Database, FolderArchive, Monitor, Moon, Palette, Sparkles, Sun, Trash2 } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { ApiError, api, explain, type SettingsView } from "./api"
import { loadTheme, saveTheme, type ThemeMode } from "./theme"
import { canLeave, Notice, ResourceNotice, useResource, useUnsaved } from "./ui"

type Section = "appearance" | "desktop" | "model" | "library" | "backup" | "reset"
const NAV = (): [Section, string, typeof Sun][] => ([
  ["appearance", t("外观"), Palette],
  ...(window.omna?.prefs ? [["desktop", t("通知与快捷键"), Bell] as [Section, string, typeof Sun]] : []),
  ["model", t("提取模型"), Sparkles],
  ["library", t("本机数据"), Database],
  ["backup", t("备份"), FolderArchive],
  ["reset", t("清空"), Trash2],
])
const MODES = (): [ThemeMode, string, typeof Sun][] => ([["light", t("浅色"), Sun], ["dark", t("深色"), Moon], ["system", t("遵循系统"), Monitor]])

export function SettingsPage({ onChanged }: { onChanged: () => void }) {
  const resource = useResource(() => api.settings(), [])
  const [section, setSection] = useState<Section>("appearance")
  return <div className="settings-dialog settings-page" aria-labelledby="settings-title">
    <h1 id="settings-title" className="settings-name">{t("设置")}</h1>
    <div className="settings-body">
      <div className="settings-nav">
        <div role="tablist" aria-label={t("设置分区")}>{NAV().map(([id, label, Glyph]) => <button key={id} type="button" role="tab" aria-selected={section === id} onClick={() => { if (section === id || canLeave()) setSection(id) }}><Glyph size={16} strokeWidth={1.75} aria-hidden="true"/>{label}</button>)}</div>
      </div>
      <div className="settings-pane" role="tabpanel">
        {section === "appearance" ? <Appearance/> : section === "desktop" ? <DesktopPrefsPane/> : <>
          <ResourceNotice resource={resource}/>
          {resource.data && <SettingsForm section={section} initial={resource.data} onChanged={() => { resource.reload(); onChanged() }}/>}
        </>}
      </div>
    </div>
  </div>
}

function SettingsForm({ section, initial, onChanged }: { section: Section; initial: SettingsView; onChanged: () => void }) {
  const [baseUrl, setBaseUrl] = useState(initial.extractor.base_url)
  const [model, setModel] = useState(initial.extractor.model)
  const [apiKey, setApiKey] = useState("")
  const [notice, setNotice] = useMessage()
  const [error, setError] = useMessage()
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
      setNotice(() => (apiKey ? t("已保存。密钥只留在本机，页面不会再显示它。") : t("已保存。密钥没有改动。")))
      onChanged()
    } catch (err) { setError(() => (settingsMessage(err))) } finally { setBusy("") }
  }

  async function test() {
    if (busy) return
    setBusy("test"); setError(""); setNotice("")
    try {
      await api.testModel(crypto.randomUUID())
      setNotice(() => (t("已连上提取服务。这次没有发送任何记忆内容。")))
    } catch (err) { setError(() => (settingsMessage(err))) } finally { setBusy("") }
  }

  async function exportLibrary() {
    if (busy) return
    setBusy("export"); setError(""); setNotice("")
    try { await api.downloadExport(); setNotice(() => (t("导出文件已开始下载。里面没有密钥和凭证。"))) }
    catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  async function backup() {
    if (busy) return
    setBusy("backup"); setError(""); setNotice("")
    try { await api.downloadBackup(); setNotice(() => (t("备份已开始下载。请把它保存到你自己选择的位置。提取密钥不在备份里。"))) }
    catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  async function restore() {
    const chosen = file.current?.files?.[0]
    if (!chosen || busy || restoreWord !== t("恢复备份")) return
    setBusy("restore"); setError(""); setNotice("")
    try {
      await api.restoreBackup(await chosen.arrayBuffer(), crypto.randomUUID())
      setRestoreWord("")
      if (file.current) file.current.value = ""
      setNotice(() => (t("已恢复。所有 Agent 已停用，旧凭证失效。请重新填写提取密钥。")))
      onChanged()
    } catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  async function reset() {
    if (busy || resetWord !== t("清空数据")) return
    setBusy("reset"); setError(""); setNotice("")
    try {
      await api.resetData(crypto.randomUUID())
      setResetWord("")
      setNotice(() => (t("已清空。你另外保存的备份文件还在。")))
      onChanged()
    } catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  async function chooseDataDir() {
    if (busy) return
    setBusy("pick"); setError(""); setNotice("")
    try {
      const result = await api.pickDataDir()
      if (result.cancelled || !result.path) return
      await api.openDataDir(result.path)
      setNotice(() => (t("已在资源管理器中打开这个文件夹。记忆库位置没有改。")))
    } catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  async function openDataDir() {
    if (busy) return
    setBusy("open"); setError(""); setNotice("")
    try {
      await api.openDataDir()
      setNotice(() => (t("已在资源管理器中打开这个文件夹。")))
    } catch (err) { setError(() => (explain(err))) } finally { setBusy("") }
  }

  return <>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {section === "model" && <form onSubmit={save}>
      <h2>{t("提取模型")}</h2>
      <section className="settings-section">
        <p className="settings-section-label">{t("连接")}</p>
        <div className="settings-card">
          <div className="settings-row">
            <span className="settings-row-label">{t("状态")}</span>
            <span className={`settings-status ${initial.extractor.configured ? "ready" : ""}`}>{initial.extractor.configured ? t("可以提取") : t("还不能提取")}</span>
          </div>
          <label className="settings-row"><span className="settings-row-label">{t("服务地址")}</span><input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} autoComplete="off" placeholder="https://example.com/v1"/></label>
          <label className="settings-row"><span className="settings-row-label">{t("模型名")}</span><input value={model} onChange={e => setModel(e.target.value)} autoComplete="off"/></label>
          <label className="settings-row"><span className="settings-row-label">{t("密钥")}</span><input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} autoComplete="new-password" placeholder={savedKey ? t("已保存，留空表示不修改") : t("尚未保存")}/></label>
        </div>
      </section>
      <div className="settings-actions">
        <button className="button" type="button" disabled={!!busy || !initial.extractor.configured} onClick={test}>{busy === "test" ? t("测试中…") : t("测试连接")}</button>
        <button className="button" disabled={!!busy}>{busy === "save" ? t("保存中…") : t("保存")}</button>
      </div>
    </form>}
    {section === "library" && <div>
      <h2>{t("本机数据")}</h2>
      <section className="settings-section">
        <p className="settings-section-label">{t("位置")}</p>
        <div className="settings-card">
          <div className="settings-row">
            <button className="settings-path" type="button" disabled={!!busy} onClick={openDataDir} title={t("在资源管理器中打开")}>{initial.data_dir}</button>
            <div className="settings-row-side">
              <button className="button" type="button" disabled={!!busy} onClick={openDataDir}>{busy === "open" ? t("打开中…") : t("打开")}</button>
              <button className="button" type="button" disabled={!!busy} onClick={chooseDataDir}>{busy === "pick" ? t("选择中…") : t("选择文件夹")}</button>
            </div>
          </div>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">{t("隐私")}</p>
        <div className="settings-card settings-note">
          <p className="helper">{t("记忆库、来源、提案、权限和访问记录都保存在本机。默认在本机检索。选择云端模型提取时，只发送你这次提交的文字，并说明接收方。授权某个 Agent 后，返回给它的记忆可能离开这台设备。OMNA 不会宣传无论怎样配置都全程离线，也不会监控剪贴板、聊天窗口或系统活动，不会默认上传使用记录。备份保存在你选择的位置；旧备份和外部 Agent 里的副本需要你另行管理。")}</p>
        </div>
      </section>
    </div>}
    {section === "backup" && <div>
      <h2>{t("备份")}</h2>
      <section className="settings-section">
        <p className="settings-section-label">{t("导出")}</p>
        <div className="settings-card">
          <div className="settings-row">
            <span className="settings-row-label">{t("导出记忆")}</span>
            <button className="button" type="button" disabled={!!busy} onClick={exportLibrary}>{busy === "export" ? t("导出中…") : t("导出")}</button>
          </div>
          <div className="settings-row">
            <span className="settings-row-label">{t("下载备份")}</span>
            <button className="button" type="button" disabled={!!busy} onClick={backup}>{busy === "backup" ? t("备份中…") : t("备份")}</button>
          </div>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">{t("从备份恢复")}</p>
        <div className="settings-card">
          <label className="settings-row"><span className="settings-row-label">{t("备份文件")}</span><input ref={file} type="file" accept=".zip,application/zip"/></label>
          <label className="settings-row"><span className="settings-row-label">{t("输入「恢复备份」以确认")}</span><input value={restoreWord} onChange={e => setRestoreWord(e.target.value)} autoComplete="off"/></label>
        </div>
        <div className="settings-actions">
          <button className="button danger" type="button" disabled={!!busy || restoreWord !== t("恢复备份")} onClick={restore}>{busy === "restore" ? t("恢复中…") : t("恢复备份")}</button>
        </div>
      </section>
    </div>}
    {section === "reset" && <div>
      <h2>{t("清空数据")}</h2>
      <section className="settings-section">
        <p className="settings-section-label">{t("会影响这些内容")}</p>
        <div className="settings-card settings-note">
          <ul className="helper settings-impact">
            <li>{t("已确认的记忆、版本、来源和待确认内容会被删除。")}</li>
            <li>{t("Agent 连接和访问记录会被删除。")}</li>
            <li>{t("在这里保存过的提取配置会被清除。")}</li>
            <li>{t("你另外保存的备份文件不会删除。")}</li>
          </ul>
        </div>
      </section>
      <section className="settings-section">
        <p className="settings-section-label">{t("确认")}</p>
        <div className="settings-card">
          <label className="settings-row"><span className="settings-row-label">{t("输入「清空数据」以确认")}</span><input value={resetWord} onChange={e => setResetWord(e.target.value)} autoComplete="off"/></label>
        </div>
        <div className="settings-actions">
          <button className="button danger" type="button" disabled={!!busy || resetWord !== t("清空数据")} onClick={reset}>{busy === "reset" ? t("清空中…") : t("清空数据")}</button>
        </div>
      </section>
    </div>}
  </>
}

function Appearance() {
  const locale = useLocale()
  const [languageError, setLanguageError] = useMessage()
  return <div>
    <h2>{t("外观")}</h2>
    <section className="settings-section">
      <p className="settings-section-label">{t("界面")}</p>
      <div className="settings-card">
        <div className="settings-row">
          <span className="settings-row-label">{t("明暗")}</span>
          <ModeSwitch/>
        </div>
        <label className="settings-row">
          <span className="settings-row-label">{t("语言")}</span>
          <select aria-label={t("语言")} value={locale} onChange={event => {
            try { setLocale(event.target.value as Locale); setLanguageError("") }
            catch (error) { setLanguageError(() => explain(error)) }
          }}>
            <option value="en">English</option>
            <option value="zh-CN">简体中文</option>
          </select>
        </label>
        {languageError && <p className="notice error" role="alert">{languageError}</p>}
        </div>
      </section>
  </div>
}

function DesktopPrefsPane() {
  const [prefs, setPrefs] = useState(() => window.omna?.prefs?.() ?? null)
  const [draft, setDraft] = useState(prefs?.shortcut ?? "")
  const [error, setError] = useMessage()
  if (!prefs) return <div><h2>{t("通知与快捷键")}</h2><p className="helper">{t("只有桌面版能设置。")}</p></div>
  async function save(patch: { notifications?: boolean; shortcut?: string }) {
    setError("")
    const next = await window.omna?.setPrefs?.(patch)
    if (!next) { setError(() => (t("没有保存成功，请重试。"))); return }
    setPrefs(next)
    setDraft(next.shortcut)
    if (patch.shortcut !== undefined && !next.shortcutRegistered) setError(() => (t("这个快捷键没能注册，可能被其他软件占用了。换一个试试。")))
  }
  const label = (value: string) => value.replace("CommandOrControl", "Ctrl").split("+").join(" ")
  return <div>
    <h2>{t("通知与快捷键")}</h2>
    {error && <Notice tone="error">{error}</Notice>}
    <section className="settings-section">
      <p className="settings-section-label">{t("通知")}</p>
      <div className="settings-card">
        <label className="settings-row"><span className="settings-row-label">{t("Agent 提出建议时弹出系统通知")}<br/><small className="helper">{t("30 秒内的多条合成一条；关掉后托盘的小绿点仍会提醒")}</small></span><input type="checkbox" checked={prefs.notifications} disabled={!prefs.notificationsSupported} onChange={event => void save({ notifications: event.target.checked })}/></label>
      </div>
    </section>
    <section className="settings-section">
      <p className="settings-section-label">{t("全局快捷键")}</p>
      <div className="settings-card">
        <div className="settings-row">
          <span className="settings-row-label">{t("打开主窗口并聚焦搜索")}<br/><small className="helper">{prefs.shortcutRegistered ? t("当前：{0}", [label(prefs.shortcut)]) : t("{0} 没能注册，可能被其他软件占用", [label(prefs.shortcut)])}</small></span>
          <div className="settings-row-side"><input aria-label={t("快捷键")} value={draft} onChange={event => setDraft(event.target.value)} placeholder="CommandOrControl+Shift+M"/><button className="button" type="button" disabled={!draft.trim() || draft === prefs.shortcut} onClick={() => void save({ shortcut: draft })}>{t("保存")}</button></div>
        </div>
      </div>
    </section>
  </div>
}

function ModeSwitch() {
  const [mode, setMode] = useState<ThemeMode>(() => loadTheme().mode)
  return <div className="mode-switch" role="group" aria-label={t("明暗")}>{MODES().map(([id, label, Glyph]) => <button key={id} type="button" aria-label={label} aria-pressed={mode === id} title={label} onClick={() => { setMode(id); saveTheme({ mode: id }) }}><Glyph size={16} strokeWidth={1.75} aria-hidden="true"/></button>)}</div>
}

function settingsMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "MODEL_UNAVAILABLE") return t("提取服务没有连上。请检查地址、模型名和密钥。密钥没有显示在页面上。")
  if (error instanceof ApiError && error.code === "TIMEOUT") return t("提取服务没有在时间内响应。")
  return explain(error)
}
