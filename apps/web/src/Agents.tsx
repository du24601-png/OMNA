import { AlertDialog } from "@base-ui/react/alert-dialog"
import { Collapsible } from "@base-ui/react/collapsible"
import { Menu } from "@base-ui/react/menu"
import { Switch } from "@base-ui/react/switch"
import { BookOpen, PencilLine, Quote, Search, type LucideIcon } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { api, explain, type AccessDetail, type AgentClient, type AgentConnection, type AgentFile } from "./api"
import { CATEGORIES, TOOLS, TOOL_DESCRIPTIONS, categoryLabel, dateLabel, deliveryLabel, groupAccess, listTime, outcomeLabel, toolLabel, type AccessGroup } from "./format"
import { ClientMark, ConnectPanel, PresetChoice, presetOf, type Preset } from "./Clients"
import { ConnectImport } from "./ConnectImport"
import { fileForClient } from "./batch"
import { canLeave, CopyButton, Icon, Notice, PageTitle, ResourceNotice, useResource, useUnsaved } from "./ui"
import type { Service } from "./App"

type State = "missing" | "idle" | "pending" | "verified" | "off"
type Row = { key: string; name: string; client?: AgentClient; agent?: AgentConnection; state: State }
export type Confirm = { title: string; body: string; action: string; danger?: boolean; run: () => Promise<void> }

const STATE_LABEL: Record<State, string> = { missing: "未安装", idle: "未连接", pending: "待验证", verified: "已验证", off: "已停用" }
const STATE_HINT: Record<State, string> = {
  missing: "本机没有检测到这个客户端",
  idle: "还没有写入配置",
  pending: "配置已写入，还没收到它的第一次调用",
  verified: "至少成功调用过一次，不代表此刻在线",
  off: "已停止访问，它的请求都会被拒绝",
}
const READ_TOOLS = ["get_context", "search_memory"]
const WAIT_MS = 180000

function agentState(agent: AgentConnection): State {
  if (!agent.enabled) return "off"
  return agent.client_status === "verified" ? "verified" : "pending"
}

function clientState(client: AgentClient, agent?: AgentConnection): State {
  if (!client.installed && !client.configured) return "missing"
  if (!client.configured || !agent) return "idle"
  return agentState(agent)
}

function buildRows(clients: AgentClient[], agents: AgentConnection[]): Row[] {
  const linked = new Set(clients.map(client => client.agent_id).filter(Boolean))
  const known = clients.map(client => {
    const agent = agents.find(item => item.id === client.agent_id)
    return { key: `client:${client.id}`, name: client.name, client, agent, state: clientState(client, agent) }
  })
  const custom = agents.filter(agent => !linked.has(agent.id)).map(agent => ({ key: `agent:${agent.id}`, name: agent.name, agent, state: agentState(agent) }))
  return [...known, ...custom]
}

function Mark({ row, large = false }: { row: Row; large?: boolean }) {
  return <span className={`conn-mark ${large ? "large" : ""}`} title={STATE_HINT[row.state]}>
    {row.client ? <ClientMark id={row.client.id} /> : <span className="conn-letter">{Array.from(row.name)[0]}</span>}
    {row.state !== "idle" && row.state !== "missing" && <i className={`conn-dot ${row.state}`} aria-hidden="true" />}
  </span>
}

export function AgentPage({ tick, online, runtime, extractor }: { tick: number; online: boolean; runtime?: Service["runtime"]; extractor: Service["extractor"] }) {
  const [localTick, setLocalTick] = useState(0)
  const agentsRes = useResource(() => api.agents(), [tick, localTick])
  const clientsRes = useResource(() => api.agentClients(), [tick, localTick])
  // Only drives the import card; a failure just means no card.
  const filesRes = useResource(() => api.agentFiles(), [tick, localTick])
  const [selected, setSelected] = useState<string | null>(null)
  const [issued, setIssued] = useState<AgentConnection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const requests = useRef(new Map<string, string>())
  const running = useRef(false)
  const agents = agentsRes.data?.agents ?? []
  const clients = clientsRes.data?.clients ?? []
  const ready = !!agentsRes.data && !!clientsRes.data
  const rows = ready ? buildRows(clients, agents) : []
  const listed = rows.filter(row => row.state !== "missing")
  const missing = rows.filter(row => row.state === "missing")
  const current = selected === "new" ? null : listed.find(row => row.key === selected) ?? listed.find(row => row.agent && row.state !== "idle") ?? listed[0]
  const live = online && !agentsRes.error && !clientsRes.error
  const counts = (["verified", "pending", "off"] as State[]).map(state => [state, listed.filter(row => row.state === state).length] as const).filter(([, n]) => n)
  const refresh = () => setLocalTick(n => n + 1)

  function keyFor(sig: string) {
    let key = requests.current.get(sig)
    if (!key) { key = crypto.randomUUID(); requests.current.set(sig, key) }
    return key
  }
  async function run<T>(sig: string, task: (key: string) => Promise<T>): Promise<T | null> {
    if (running.current) return null
    running.current = true; setBusy(true); setError(""); setNotice("")
    try { const value = await task(keyFor(sig)); requests.current.delete(sig); return value }
    catch (err) { setError(explain(err)); return null }
    finally { running.current = false; setBusy(false) }
  }
  function select(key: string) {
    if (key === (current?.key ?? selected) || !canLeave()) return
    setSelected(key); setError(""); setNotice("")
  }

  async function connect(client: AgentClient, preset: Preset) {
    const result = await run(`connect:${client.id}:${preset}`, key => api.connectClient(client.id, preset, key))
    if (!result) return
    setSelected(`client:${client.id}`); refresh()
  }
  function reconnect(client: AgentClient, agent: AgentConnection) {
    setConfirm({
      title: `重新写入 ${client.name} 的配置`,
      body: `会给 ${client.name} 换一份新凭证并写进它的配置文件，旧配置立即失效。权限保持不变。写入后需要重启 ${client.name}。`,
      action: "重新写入",
      run: async () => {
        const preset = presetOf(agent.allowed_tools)
        const done = await run(
          `reconnect:${client.id}:${agent.policy_version}:${agent.allowed_tools.join(",")}:${agent.allowed_categories.join(",")}:${agent.propose_categories.join(",")}`,
          key => api.connectClient(client.id, preset, key, {
            allowed_tools: agent.allowed_tools,
            allowed_categories: agent.allowed_categories,
            propose_categories: agent.propose_categories,
          }),
        )
        if (done) setNotice(`已重新写入。重启 ${client.name} 后生效。`)
        refresh()
      },
    })
  }
  function rotate(agent: AgentConnection) {
    setConfirm({
      title: "重置凭证",
      body: `${agent.name} 的旧凭证会立即失效。之后需要把新配置填进那个客户端，才能继续使用。`,
      action: "重置凭证",
      danger: true,
      run: async () => {
        const value = await run(`rotate:${agent.id}:${agent.policy_version}`, key => api.rotateAgent(agent.id, key))
        if (value) { setIssued(value); refresh() }
      },
    })
  }
  async function toggle(agent: AgentConnection, enabled: boolean) {
    const value = await run(`${agent.id}:enabled:${enabled}:${agent.policy_version}`, key => api.updateAgent(agent.id, { enabled }, key))
    if (!value) return
    setNotice(enabled ? `已恢复 ${agent.name} 的访问。` : `已停用。${agent.name} 之后的请求都会被拒绝。`)
    refresh()
  }
  async function save(agent: AgentConnection, body: Record<string, unknown>) {
    const value = await run(`${agent.id}:${JSON.stringify(body)}:${agent.policy_version}`, key => api.updateAgent(agent.id, body, key))
    if (value) { setNotice("授权已保存，下一次请求开始生效。"); refresh() }
    return value
  }
  async function create(name: string) {
    const value = await run(`create:${name}`, key => api.createAgent(name, key))
    if (!value) return false
    setIssued(value); setSelected(`agent:${value.id}`); refresh()
    return true
  }

  const panel = (() => {
    if (!ready) return null
    if (selected === "new") return <CreatePanel online={live} busy={busy} onCreate={create} />
    if (!current) return <div className="detail-empty"><h2>还没有可连接的客户端</h2><p className="helper">装好 WorkBuddy、ZCode、OpenCode、ChatGPT、Claude 或 Claude Code 后点 ↻ 重新检测，或从左下角连接其他 MCP 客户端。</p></div>
    if (current.client && (current.state === "idle" || !current.agent)) {
      return <ConnectPanel key={current.key} client={current.client} agent={current.agent} online={live} busy={busy} onConnect={preset => void connect(current.client!, preset)} />
    }
    const agent = current.agent!
    return <AgentDetail
      key={current.key}
      row={current}
      agent={agent}
      online={live}
      busy={busy}
      tick={tick + localTick}
      issued={issued?.id === agent.id ? issued : null}
      runtime={runtime}
      file={current.client && filesRes.data ? fileForClient(filesRes.data.files, current.client.id) : undefined}
      extractor={extractor}
      agents={agents}
      onChanged={refresh}
      onConfirm={setConfirm}
      onHideIssued={() => setIssued(null)}
      onToggle={enabled => void toggle(agent, enabled)}
      onSave={body => save(agent, body)}
      onReconnect={() => current.client && reconnect(current.client, agent)}
      onRotate={() => rotate(agent)}
      onVerified={() => { setNotice(`${current.name} 刚刚调用成功，已验证。`); refresh() }}
    />
  })()

  return <div className="page">
    <PageTitle title="我的 Agent" />
    {[agentsRes, clientsRes].map((resource, index) => (resource.error || !resource.data) && <ResourceNotice key={index} resource={resource} />)}
    <div className="agents-shell">
      <aside className="conn-list surface" aria-label="连接">
        <header>
          <div>
            <h2>连接</h2>
            <p className="conn-summary">{counts.length ? counts.map(([state, n]) => <span key={state}><i className={`conn-dot inline ${state}`} />{n} 个{STATE_LABEL[state]}</span>) : "还没有连接任何 Agent"}</p>
          </div>
          <button className="icon-button" type="button" title="重新检测本机客户端" aria-label="重新检测本机客户端" onClick={refresh}><Icon name="refresh" /></button>
        </header>
        <div className="conn-rows">
          {listed.map(row => <button
            key={row.key}
            type="button"
            className={`conn-row ${row.key === current?.key && selected !== "new" ? "selected" : ""}`}
            aria-current={row.key === current?.key && selected !== "new" ? "true" : undefined}
            onClick={() => select(row.key)}
          >
            <Mark row={row} />
            <span className="conn-name"><strong>{row.name}</strong><small className={`state-text ${row.state}`}>{row.state === "verified" && row.agent?.last_access_at ? `最近访问 ${listTime(row.agent.last_access_at)}` : STATE_LABEL[row.state]}{!row.client ? " · 自定义" : ""}</small></span>
            {row.state === "idle" ? <span className="conn-cta">连接</span> : <Icon name="right" />}
          </button>)}
        </div>
        {!!missing.length && <details className="conn-missing">
          <summary>未检测到 {missing.length} 个客户端</summary>
          <ul>{missing.map(row => <li key={row.key}><Mark row={row} />{row.name}</li>)}</ul>
          <p className="helper">装好后点上方 ↻ 重新检测。</p>
        </details>}
        <button type="button" className={`conn-add ${selected === "new" ? "selected" : ""}`} onClick={() => { if (selected !== "new" && canLeave()) { setSelected("new"); setError(""); setNotice("") } }}><Icon name="plus" />其他 MCP 客户端</button>
      </aside>
      <section className="conn-detail surface">
        {error && <Notice tone="error">{error}</Notice>}
        {notice && <Notice tone="success">{notice}</Notice>}
        {panel}
      </section>
    </div>
    <ConfirmDialog request={confirm} busy={busy} onClose={() => setConfirm(null)} />
  </div>
}

function ConfirmDialog({ request, busy, onClose }: { request: Confirm | null; busy: boolean; onClose: () => void }) {
  const running = useRef(false)
  const [submitting, setSubmitting] = useState(false)
  const locked = busy || submitting
  async function submit() {
    if (!request || busy || running.current) return
    running.current = true; setSubmitting(true)
    try { await request.run(); onClose() }
    finally { running.current = false; setSubmitting(false) }
  }
  return <AlertDialog.Root open={!!request} onOpenChange={open => { if (!open && !locked) onClose() }}>
    <AlertDialog.Portal>
      <AlertDialog.Backdrop className="dialog-backdrop detail-alert-backdrop" />
      <AlertDialog.Popup className="detail-alert material">
        <AlertDialog.Title className="detail-alert-title">{request?.title}</AlertDialog.Title>
        <AlertDialog.Description className="detail-alert-copy">{request?.body}</AlertDialog.Description>
        <div className="actions detail-alert-actions">
          <AlertDialog.Close className="button secondary" disabled={locked}>取消</AlertDialog.Close>
          <button className={`button ${request?.danger ? "danger" : "primary"}`} type="button" disabled={locked} onClick={() => void submit()}>{locked ? "处理中…" : request?.action}</button>
        </div>
      </AlertDialog.Popup>
    </AlertDialog.Portal>
  </AlertDialog.Root>
}

function CreatePanel({ online, busy, onCreate }: { online: boolean; busy: boolean; onCreate: (name: string) => Promise<boolean> }) {
  const [name, setName] = useState("")
  useUnsaved(!!name.trim(), busy)
  return <form className="connect-panel" onSubmit={async event => { event.preventDefault(); if (name.trim() && await onCreate(name.trim())) setName("") }}>
    <span className="hero-mark"><Icon name="plus" /></span>
    <h2>连接其他 MCP 客户端</h2>
    <h3 className="field-title">给它起个名字</h3>
    <input aria-label="连接名称" placeholder="例如：林舟的写作助手" value={name} maxLength={80} onChange={event => setName(event.target.value)} disabled={busy} />
    <button className="button primary connect-go" disabled={!online || busy || !name.trim()}>{busy ? "创建中…" : "创建并生成配置"}</button>
  </form>
}

function IssuedCredential({ agent, runtime, onDone }: { agent: AgentConnection; runtime?: Service["runtime"]; onDone: () => void }) {
  const [format, setFormat] = useState<"generic" | "opencode">("generic")
  const env = runtime ? { ...runtime.environment, ZHIWO_AGENT_CREDENTIAL: agent.credential } : null
  const config = runtime && env
    ? JSON.stringify(format === "opencode"
      ? { mcp: { zhiwo: { type: "local", command: runtime.command, environment: env } } }
      : { mcpServers: { zhiwo: { command: runtime.command[0], args: runtime.command.slice(1), env } } }, null, 2)
    : ""
  return <section className="credential-box">
    <div className="section-heading"><h3>把这份配置填进客户端</h3><div className="segmented" role="tablist" aria-label="配置格式">
      <button type="button" role="tab" aria-selected={format === "generic"} onClick={() => setFormat("generic")}>通用 mcpServers</button>
      <button type="button" role="tab" aria-selected={format === "opencode"} onClick={() => setFormat("opencode")}>OpenCode</button>
    </div></div>
    <p className="helper">里面有专用凭证，只显示这一次。收起后不能再查看，需要时可以重置凭证。</p>
    {runtime ? <pre className="code-block">{config}</pre> : <>
      <Notice tone="warning">读不到本地服务的启动信息，暂时只能给出凭证。恢复服务后可以重置凭证拿完整配置。</Notice>
      <pre className="code-block">{agent.credential}</pre>
    </>}
    <div className="actions">
      <CopyButton text={runtime ? config : agent.credential || ""} label={runtime ? "复制配置" : "复制凭证"} />
      <button className="button secondary" type="button" onClick={onDone}>已保存，收起</button>
    </div>
  </section>
}

function useVerifyWatch(agentId: string, active: boolean, onVerified: () => void) {
  const [waiting, setWaiting] = useState(active)
  const [round, setRound] = useState(0)
  const callback = useRef(onVerified)
  callback.current = onVerified
  useEffect(() => {
    if (!active) return
    setWaiting(true)
    const started = Date.now()
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== "visible") return
      if (Date.now() - started > WAIT_MS) { window.clearInterval(timer); setWaiting(false); return }
      try {
        const { agents } = await api.agents()
        const found = agents.find(item => item.id === agentId)
        if (found && found.client_status === "verified") { window.clearInterval(timer); callback.current() }
      } catch {
        window.clearInterval(timer); setWaiting(false)
      }
    }, 4000)
    return () => window.clearInterval(timer)
  }, [agentId, active, round])
  return { waiting, restart: () => setRound(n => n + 1) }
}

function AgentDetail({ row, agent, online, busy, tick, issued, runtime, file, extractor, agents, onChanged, onConfirm, onHideIssued, onToggle, onSave, onReconnect, onRotate, onVerified }: {
  row: Row
  agent: AgentConnection
  online: boolean
  busy: boolean
  tick: number
  issued: AgentConnection | null
  runtime?: Service["runtime"]
  file?: AgentFile
  extractor: Service["extractor"]
  agents: AgentConnection[]
  onChanged: () => void
  onConfirm: (request: Confirm) => void
  onHideIssued: () => void
  onToggle: (enabled: boolean) => void
  onSave: (body: Record<string, unknown>) => Promise<AgentConnection | null>
  onReconnect: () => void
  onRotate: () => void
  onVerified: () => void
}) {
  const [tab, setTab] = useState<"permissions" | "access">("permissions")
  const [covered, setCovered] = useState(false)
  const events = useResource(() => api.accessEvents(agent.id), [agent.id, tick])
  const count = events.data ? groupAccess(events.data.events).filter(group => !group.quiet).length : 0
  const watch = useVerifyWatch(agent.id, row.state === "pending", onVerified)
  const sub = row.state === "verified" ? (agent.last_access_at ? `最近访问 ${listTime(agent.last_access_at)}` : "") : row.state === "off" ? "它的请求都会被拒绝" : row.state === "pending" ? "等它第一次调用" : ""
  return <div className="agent-detail">
    <header className="detail-head">
      <Mark row={row} large />
      <div className="detail-title">
        <h2>{row.name}</h2>
        <p className={`state-text ${row.state}`} title={STATE_HINT[row.state]}>{STATE_LABEL[row.state]}{sub && <span> · {sub}</span>}</p>
      </div>
      <label className="access-switch">
        <span>允许访问</span>
        <Switch.Root className="share-switch" checked={agent.enabled} disabled={!online || busy} onCheckedChange={onToggle} aria-label="允许访问">
          <Switch.Thumb className="share-switch-thumb" />
        </Switch.Root>
      </label>
      <Menu.Root>
        <Menu.Trigger className="icon-button" aria-label="更多操作" disabled={!online || busy}><Icon name="more" /></Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner className="detail-positioner" side="bottom" align="end" sideOffset={6}>
            <Menu.Popup className="select-popup">
              {row.client
                ? <Menu.Item className="tool-option" onClick={onReconnect}>重新写入配置…</Menu.Item>
                : <Menu.Item className="tool-option danger-option" onClick={onRotate}>重置凭证…</Menu.Item>}
              {row.client && <Menu.Item className="tool-option" onClick={() => void navigator.clipboard.writeText(row.client!.config_path).catch(() => undefined)}>复制配置文件路径</Menu.Item>}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </header>
    {issued?.credential && <IssuedCredential agent={issued} runtime={runtime} onDone={onHideIssued} />}
    {row.state === "pending" && !(file && covered) && <VerifyGuide row={row} agent={agent} waiting={watch.waiting} onRestart={watch.restart} onOpenPermissions={() => setTab("permissions")} />}
    {file && <ConnectImport name={row.name} agent={agent} file={file} fresh={row.state === "pending"} online={online} extractor={extractor} agents={agents} onChanged={onChanged} onConfirm={onConfirm} onCovering={setCovered} />}
    <div className="segmented detail-tabs" role="tablist" aria-label="连接详情">
      <button type="button" role="tab" aria-selected={tab === "permissions"} onClick={() => setTab("permissions")}>权限</button>
      <button type="button" role="tab" aria-selected={tab === "access"} onClick={() => setTab("access")}>访问记录{count ? <span className="tab-count">{count}</span> : null}</button>
    </div>
    <div role="tabpanel" hidden={tab !== "permissions"}><PermissionEditor agent={agent} online={online} busy={busy} onSave={onSave} /></div>
    <div role="tabpanel" hidden={tab !== "access"}><AccessLog name={row.name} events={events} /></div>
  </div>
}

function VerifyGuide({ row, agent, waiting, onRestart, onOpenPermissions }: { row: Row; agent: AgentConnection; waiting: boolean; onRestart: () => void; onOpenPermissions: () => void }) {
  const readable = agent.allowed_categories.length > 0 && agent.allowed_tools.some(tool => READ_TOOLS.includes(tool))
  const prompt = `请查一下我的${categoryLabel(agent.allowed_categories[0] || "preference")}`
  const [helpOpen, setHelpOpen] = useState(false)
  useEffect(() => { if (!waiting) setHelpOpen(true) }, [waiting])
  return <section className="verify">
    {!readable ? <p className="verify-lead">它现在读不到记忆。先在<button type="button" className="text-button" onClick={onOpenPermissions}>权限</button>里选好它能读的内容。</p> : <>
      <p className="verify-lead">{row.client ? `复制这句，重启 ${row.name} 后发给它` : "把配置填进客户端，重启后发这句"}</p>
      <div className="prompt-chip"><span>{prompt}</span><CopyButton text={prompt} /></div>
      {!waiting && <p className="verify-wait">还没收到。<button type="button" className="text-button" onClick={onRestart}>继续等待</button></p>}
      <Collapsible.Root className="verify-help" open={helpOpen} onOpenChange={setHelpOpen}>
        <Collapsible.Trigger className="verify-help-trigger">没反应？</Collapsible.Trigger>
        <Collapsible.Panel>
          <ul className="verify-tips">
            <li>{row.client ? `先完全退出 ${row.name}，再重新打开。` : "确认配置已经填进去，然后完全退出再打开。"}</li>
            <li>在客户端里开启名为 zhiwo 的 MCP 工具。</li>
            {row.client && <li>配置文件：<code>{row.client.config_path}</code> <CopyButton text={row.client.config_path} /></li>}
            <li>{row.client ? "还不行，就用右上角 ⋯ 里的「重新写入配置」。" : "还不行，就用右上角 ⋯ 重置凭证，换一份新配置。"}</li>
          </ul>
        </Collapsible.Panel>
      </Collapsible.Root>
    </>}
  </section>
}

function sortedKey(tools: string[], categories: string[], propose: string[]) {
  return JSON.stringify([[...tools].sort(), [...categories].sort(), [...propose].sort()])
}

const ALL_CATEGORY_IDS = CATEGORIES.map(([id]) => id)

function PermissionEditor({ agent, online, busy, onSave }: { agent: AgentConnection; online: boolean; busy: boolean; onSave: (body: Record<string, unknown>) => Promise<AgentConnection | null> }) {
  const [tools, setTools] = useState(agent.allowed_tools)
  const [categories, setCategories] = useState(agent.allowed_categories)
  const [propose, setPropose] = useState(agent.propose_categories)
  const [baseline, setBaseline] = useState(sortedKey(agent.allowed_tools, agent.allowed_categories, agent.propose_categories))
  const dirty = sortedKey(tools, categories, propose) !== baseline
  const locked = busy || !online
  useUnsaved(dirty, busy)
  useEffect(() => {
    if (dirty) return
    setTools(agent.allowed_tools); setCategories(agent.allowed_categories); setPropose(agent.propose_categories)
    setBaseline(sortedKey(agent.allowed_tools, agent.allowed_categories, agent.propose_categories))
  }, [agent])
  const level: Preset | null = tools.includes("propose_memory") ? "propose" : tools.some(tool => READ_TOOLS.includes(tool)) ? "read" : null
  function flip(list: string[], value: string, set: (next: string[]) => void) { set(list.includes(value) ? list.filter(item => item !== value) : [...list, value]) }
  function setLevel(next: Preset) {
    const wanted = new Set([...tools.filter(tool => tool !== "propose_memory"), ...READ_TOOLS, ...(next === "propose" ? ["propose_memory"] : [])])
    setTools(TOOLS.map(([id]) => id).filter(id => wanted.has(id)))
    if (next === "propose" && propose.length === 0) setPropose(ALL_CATEGORY_IDS)
    if (next === "read") setPropose([])
  }
  function reset() { setTools(agent.allowed_tools); setCategories(agent.allowed_categories); setPropose(agent.propose_categories) }
  async function submit() {
    const proposing = tools.includes("propose_memory")
    const value = await onSave({ allowed_tools: tools, allowed_categories: categories, propose_categories: proposing ? propose : [] })
    if (value) {
      setTools(value.allowed_tools); setCategories(value.allowed_categories); setPropose(value.propose_categories)
      setBaseline(sortedKey(value.allowed_tools, value.allowed_categories, value.propose_categories))
    }
  }
  return <div className="permission-editor">
    <h3 className="field-title">它可以做什么</h3>
    <PresetChoice value={level} disabled={locked} onChange={setLevel} />
    <h3 className="field-title">它可以读哪些</h3>
    <div className="chip-group">{CATEGORIES.map(([id, label]) => {
      const on = categories.includes(id)
      return <button key={id} type="button" className={`chip ${on ? "on" : ""}`} aria-pressed={on} disabled={locked} onClick={() => flip(categories, id, setCategories)}>{on && <Icon name="check" />}{label}</button>
    })}</div>
    <p className="helper">只会提供已确认、当前有效、你没有设为「仅自己可见」的记忆。</p>
    {tools.includes("propose_memory") && <>
      <h3 className="field-title">它可以提哪些</h3>
      <div className="chip-group">{CATEGORIES.map(([id, label]) => {
        const on = propose.includes(id)
        return <button key={id} type="button" className={`chip ${on ? "on" : ""}`} aria-pressed={on} disabled={locked} onClick={() => flip(propose, id, setPropose)}>{on && <Icon name="check" />}{label}</button>
      })}</div>
      <p className="helper">它提的都是待确认建议，你确认后才生效。能提哪些和能读哪些分开设置。</p>
    </>}
    <details className="disclosure advanced">
      <summary>逐项设置工具</summary>
      <fieldset className="tool-permissions" disabled={locked}>
        {TOOLS.map(([id, label]) => <label key={id}><input type="checkbox" checked={tools.includes(id)} onChange={() => flip(tools, id, setTools)} /><span><strong>{label}</strong><small>{TOOL_DESCRIPTIONS[id]}</small></span></label>)}
      </fieldset>
    </details>
    {dirty && <div className="save-bar" role="status">
      <span>改动还没保存</span>
      <div className="actions">
        <button type="button" className="button secondary" disabled={busy} onClick={reset}>还原</button>
        <button type="button" className="button primary" disabled={locked} onClick={() => void submit()}>{busy ? "保存中…" : "保存"}</button>
      </div>
    </div>}
  </div>
}

const TOOL_ICONS: Record<string, LucideIcon> = {
  search_memory: Search,
  get_context: BookOpen,
  propose_memory: PencilLine,
  explain_memory: Quote,
}

function ToolMark({ tool }: { tool: string }) {
  const Mark = TOOL_ICONS[tool] ?? Search
  return <Mark size={13} strokeWidth={1.75} aria-hidden="true" />
}

function headlines(group: AccessGroup) {
  if (group.lines.length) return group.lines.slice(0, 3)
  if (group.events.some(event => event.outcome === "rejected")) return ["这次被拒绝"]
  if (group.tool === "propose_memory") return ["提出了修改建议，等你确认"]
  if (group.events.some(event => event.delivery_state === "failed")) return ["这次没有发出去"]
  if (group.events.some(event => event.delivery_state === "unknown")) return ["这次是否发出去，还不清楚"]
  return ["这次还没发出去"]
}

function needsAttention(group: AccessGroup) {
  return group.events.some(event => event.outcome === "rejected" || event.delivery_state !== "sent")
}

function groupContent(group: AccessGroup) {
  if (group.quiet) return toolLabel(group.tool)
  const lines = headlines(group)
  const primary = lines[0] || toolLabel(group.tool)
  if (group.lines.length > 1) return `${primary} 等 ${group.lines.length} 条`
  return primary
}

function groupResult(group: AccessGroup) {
  if (group.events.some(event => event.outcome === "rejected")) return "已拒绝"
  if (group.events.some(event => event.delivery_state === "failed")) return "发送失败"
  if (group.events.some(event => event.delivery_state === "unknown")) return "交付未知"
  if (group.events.some(event => event.delivery_state !== "sent")) return "未发出"
  if (group.lines.length) {
    return group.events.length > 1 ? `${group.events.length} 次 · ${group.lines.length} 条` : `${group.lines.length} 条记忆`
  }
  if (group.tool === "propose_memory" && group.events.some(event => event.outcome === "success")) return "待确认"
  return outcomeLabel(group.events[0].outcome)
}

function callSummary(group: AccessGroup) {
  const got = group.events.filter(event => event.returned?.length).length
  const empty = group.events.filter(event => event.outcome === "empty").length
  const rejected = group.events.filter(event => event.outcome === "rejected").length
  const unsent = group.events.filter(event => event.delivery_state !== "sent").length
  const parts = [`${group.events.length} 次调用`]
  if (got) parts.push(`${got} 次拿到内容`)
  if (empty) parts.push(`${empty} 次没有内容`)
  if (rejected) parts.push(`${rejected} 次被拒绝`)
  parts.push(unsent ? `${unsent} 次没有按通常方式发出` : "都已交给发送通道")
  return parts.join(" · ")
}

function AccessLog({ name, events }: { name: string; events: { data: { events: Parameters<typeof groupAccess>[0] } | null; loading: boolean; error: string; reload: () => void } }) {
  const [openId, setOpenId] = useState<string | null>(null)
  const list = events.data?.events ?? []
  const groups = groupAccess(list)
  const visible = groups.filter(group => !group.quiet)
  const quiet = groups.filter(group => group.quiet)
  return <div className="access-log">
    <div className="access-toolbar">
      <button className="icon-button" type="button" title="刷新访问记录" aria-label="刷新访问记录" onClick={events.reload}><Icon name="refresh" /></button>
    </div>
    <ResourceNotice resource={events} />
    {events.data && !list.length && !events.error && !events.loading && <p className="inline-empty">还没有访问记录。{name} 调用一次后，会出现在这里。</p>}
    {!!groups.length && <div className="access-list">
      <div className="access-head" aria-hidden="true"><span>内容</span><span>操作</span><span>结果</span><span>时间</span></div>
      {visible.map(group => <AccessGroupRow key={group.id} group={group} open={openId === group.id} onToggle={() => setOpenId(openId === group.id ? null : group.id)} />)}
      {!!quiet.length && <details className="access-quiet">
        <summary>还有 {quiet.reduce((sum, group) => sum + group.events.length, 0)} 次没有返回内容</summary>
        {quiet.map(group => <AccessGroupRow key={group.id} group={group} quiet open={openId === group.id} onToggle={() => setOpenId(openId === group.id ? null : group.id)} />)}
      </details>}
    </div>}
  </div>
}

function AccessGroupRow({ group, open, quiet = false, onToggle }: { group: AccessGroup; open: boolean; quiet?: boolean; onToggle: () => void }) {
  const newest = group.events[0].created_at
  const attention = !quiet && needsAttention(group)
  return <div className={`access-item ${open ? "open" : ""} ${quiet ? "quiet" : ""}`}>
    <button type="button" className="access-row" aria-expanded={open} onClick={onToggle}>
      <span className="access-content">{groupContent(group)}</span>
      <span className="access-tool"><span className={`memo-cat access-tool-chip ${group.tool}`}><ToolMark tool={group.tool} />{toolLabel(group.tool)}</span></span>
      <span className={`access-result${attention ? " alert" : ""}`}>{groupResult(group)}</span>
      <time dateTime={newest} title={dateLabel(newest)}>{listTime(newest)}</time>
    </button>
    {open && <GroupCalls group={group} />}
  </div>
}

function routineCall(event: AccessGroup["events"][number]) {
  return event.outcome === "success" && event.delivery_state === "sent" && !!event.returned?.length
}

function CallButton({ event, selected, onPick }: { event: AccessGroup["events"][number]; selected: boolean; onPick: (id: string) => void }) {
  return <button type="button" className={selected ? "selected" : ""} onClick={() => onPick(event.id)}>
    <span>{dateLabel(event.created_at)}</span>
    <span>{event.outcome === "rejected" ? "已拒绝" : event.returned?.length ? `${event.returned.length} 条记忆` : outcomeLabel(event.outcome)}</span>
    <small>{deliveryLabel(event.delivery_state)}</small>
  </button>
}

function GroupCalls({ group }: { group: AccessGroup }) {
  const routine = group.events.filter(routineCall)
  const notable = group.events.filter(event => !routineCall(event))
  const [eventId, setEventId] = useState(group.events.find(event => event.returned?.length)?.id ?? group.events[0].id)
  const detail = useResource(() => api.accessEvent(eventId), [eventId])
  const calls = (items: AccessGroup["events"]) => items.map(event => <CallButton key={event.id} event={event} selected={event.id === eventId} onPick={setEventId} />)
  return <div className="access-detail">
    {group.events.length > 1 && <p className="helper">{callSummary(group)}</p>}
    {!!notable.length && <div className="access-calls">{calls(notable)}</div>}
    {routine.length > 1 && <details className="access-quiet">
      <summary>其余 {routine.length} 次也拿到了内容</summary>
      <div className="access-calls">{calls(routine)}</div>
    </details>}
    {routine.length === 1 && group.events.length > 1 && <div className="access-calls">{calls(routine)}</div>}
    <ResourceNotice resource={detail} />
    {detail.data?.id === eventId && <AccessPane detail={detail.data} />}
  </div>
}

function AccessPane({ detail }: { detail: AccessDetail }) {
  const items = detail.response.items || [], explained = detail.response.result
  let body: ReactNode = null
  if (!items.length && !explained && !detail.response.error) body = <p className="helper">这次没有返回记忆正文。{detail.response.proposal_id ? "修改建议已进入待确认。" : ""}</p>
  return <div className="access-detail">
    <p className="helper">{dateLabel(detail.created_at)} · {deliveryLabel(detail.delivery_state)}</p>
    {detail.response.error && <Notice tone="warning">{detail.response.error.message}</Notice>}
    {items.map(item => <article key={`${item.id}-${item.revision}`}><p className="prose">{item.content}</p><div className="metadata"><span>{categoryLabel(item.category)}</span><span>版本 {item.revision}</span><span>{item.scope || "未限定场景"}</span></div></article>)}
    {explained && <article><p className="prose">{explained.evidence || "未返回证据片段。"}</p><p className="helper">版本 {explained.revision}</p></article>}
    {body}
    <details className="disclosure"><summary>技术信息</summary><pre className="code-block">{JSON.stringify({ event_id: detail.id, request_id: detail.request_id, policy_version: detail.policy_version, response: detail.response }, null, 2)}</pre></details>
  </div>
}
