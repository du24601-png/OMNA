import { useEffect, useRef, useState, type ReactNode } from "react"
import { api, explain, type AgentClient, type AgentConnection, type AgentFile, type Memory, type Proposal } from "./api"
import { BATCH_POLL_MS, assignFiles, carryOut, loadLibrary, sortCandidates, type Candidate } from "./batch"
import { BrandMark } from "./Brand"
import { ClientMark } from "./Clients"
import { CATEGORIES, TOOLS, categoryLabel, joinNames } from "./format"
import { CopyButton, Notice } from "./ui"
import type { Service } from "./App"

// Three screens: connect, bring what you already wrote (the connected agent
// organizes its own instruction file, suggestions arrive with a checkbox),
// and what AI now sees. Everything else lives in the main window.
const SLOW_MS = 180000
const ALL_CATEGORIES = CATEGORIES.map(([id]) => id)
const READ_TOOLS = ["get_context", "search_memory"]

type Step = 1 | 2 | 3
type Phase = "idle" | "starting" | "waiting"
type Session = { batch: string; prompt: string; started: number }
type Profile = Record<string, { items: Memory[]; total: number }>
type Organizer = { client: AgentClient; file: AgentFile }
export type OnboardingExit = "tray" | "agents"

export const ONBOARDING_KEY = "omna-onboarding"

export function Onboarding({ service, controls, onExit }: { service: Service; controls: ReactNode; onExit: (to: OnboardingExit) => void }) {
  const [step, setStep] = useState<Step>(1)
  const [clients, setClients] = useState<AgentClient[] | null>(null)
  const [agents, setAgents] = useState<AgentConnection[]>([])
  const [files, setFiles] = useState<AgentFile[]>([])
  const [loadError, setLoadError] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  // Clients connected together in one click, in the order of the clicks.
  const [connectGroups, setConnectGroups] = useState<string[][]>([])
  const [phases, setPhases] = useState<Record<string, Phase>>({})
  const [sessions, setSessions] = useState<Record<string, Session>>({})
  const [orgErrors, setOrgErrors] = useState<Record<string, string>>({})
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [library, setLibrary] = useState<Memory[] | null>(null)
  const [off, setOff] = useState<string[]>([])
  const keys = useRef(new Map<string, string>())
  const online = service.status === "online"

  function keyFor(sig: string) {
    let key = keys.current.get(sig)
    if (!key) { key = crypto.randomUUID(); keys.current.set(sig, key) }
    return key
  }
  async function reload() {
    try {
      const [c, a, f] = await Promise.all([api.agentClients(), api.agents(), api.agentFiles()])
      setClients(c.clients); setAgents(a.agents); setFiles(f.files); setLoadError("")
    } catch (err) { setLoadError(explain(err)) }
  }
  useEffect(() => { void reload() }, [])

  // While an agent is organizing, keep its suggestions coming in on their own.
  const polling = step === 2 && Object.keys(sessions).length > 0
  useEffect(() => {
    if (!polling) return
    let alive = true
    const load = () => api.proposals().then(value => { if (alive) setProposals(value.proposals) }).catch(() => undefined)
    void load()
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load() }, BATCH_POLL_MS)
    return () => { alive = false; window.clearInterval(timer) }
  }, [polling])
  useEffect(() => {
    if (!polling || library) return
    loadLibrary().then(setLibrary).catch(err => setError(`还没核对已有记忆：${explain(err)}`))
  }, [polling, library])

  function go(next: Step) { setError(""); setStep(next) }

  // ── step 1 ──
  const linked = (client: AgentClient) => client.configured && !!client.agent_id
  const detected = (clients ?? []).filter(client => client.installed || client.configured)
  const missing = (clients ?? []).filter(client => !client.installed && !client.configured)
  const hasFile = (clientId: string) => files.some(file => !file.empty && file.clients.some(item => item.id === clientId))
  const isOn = (client: AgentClient) => linked(client) || (picked[client.id] ?? true)
  const toConnect = detected.filter(client => !linked(client) && isOn(client))
  async function connect() {
    if (busy) return
    setBusy(true); setError("")
    const failed: string[] = []
    const done: string[] = []
    let first: unknown = null
    for (const client of toConnect) {
      try { await api.connectClient(client.id, "read", keyFor(`connect:${client.id}`)); done.push(client.id) }
      catch (err) { failed.push(client.name); first ??= err }
    }
    if (done.length) setConnectGroups(old => [...old, done])
    await reload()
    setBusy(false)
    if (failed.length) setError(`没连上 ${failed.join("、")}：${explain(first)}`)
    else go(2)
  }

  // ── step 2 ──
  // One card per instruction file, given to the first connected client that
  // reads it: ones connected before onboarding first, then click by click;
  // clients connected in the same click take their own files first.
  const connected = detected.filter(linked)
  const later = new Set(connectGroups.flat())
  const assigned = assignFiles(files, [connected.filter(client => !later.has(client.id)).map(client => client.id), ...connectGroups])
  const organizers: Organizer[] = connected.filter(client => assigned.has(client.id)).map(client => ({ client, file: assigned.get(client.id)! }))
  const agentOf = (client: AgentClient) => agents.find(agent => agent.id === client.agent_id)
  const setPhase = (id: string, phase: Phase) => setPhases(old => ({ ...old, [id]: phase }))
  async function organize(client: AgentClient) {
    const agent = agentOf(client)
    if (!agent || phases[client.id] === "starting") return
    setPhase(client.id, "starting")
    setOrgErrors(old => ({ ...old, [client.id]: "" }))
    try {
      // One click both allows proposing (it was connected read-only) and starts.
      if (!(agent.enabled && agent.allowed_tools.includes("propose_memory") && agent.propose_categories.length)) {
        const wanted = new Set([...agent.allowed_tools, "propose_memory"])
        const updated = await api.updateAgent(agent.id, {
          allowed_tools: TOOLS.map(([id]) => id).filter(id => wanted.has(id)),
          propose_categories: agent.propose_categories.length ? agent.propose_categories : ALL_CATEGORIES,
        }, keyFor(`allow:${agent.id}:${agent.policy_version}`))
        setAgents(old => old.map(item => item.id === updated.id ? { ...item, ...updated } : item))
      }
      const session = await api.organize(agent.id)
      setSessions(old => ({ ...old, [client.id]: { batch: session.batch_id, prompt: session.prompt, started: Date.now() } }))
      setPhase(client.id, "waiting")
    } catch (err) {
      setPhase(client.id, "idle")
      setOrgErrors(old => ({ ...old, [client.id]: explain(err) }))
    }
  }
  const batches = new Set(Object.values(sessions).map(session => session.batch))
  const candidates = library ? sortCandidates(proposals, batches, library) : []
  const shown = candidates.filter(item => item.lane === "plain")
  const keep = shown.filter(item => !off.includes(item.proposal.id))
  const anyWaiting = Object.keys(sessions).length > 0
  async function remember() {
    if (busy) return
    setBusy(true); setError("")
    try {
      // Organize batches can still grow, so only what is on screen is decided:
      // ticked ones are remembered, unticked ones are turned down.
      const outcome = await carryOut({ keep, drop: shown.filter(item => off.includes(item.proposal.id)), categories: {}, choices: [], closed: new Set() }, keyFor)
      if (outcome.errors.length) {
        setProposals((await api.proposals()).proposals)
        setError(`${outcome.errors.length} 条没有完成：${outcome.errors[0]} 再点一次会接着做，不会重复记住。`)
        return
      }
      go(3)
    } catch (err) { setError(explain(err)) }
    finally { setBusy(false) }
  }

  // ── footer ──
  let primaryLabel = "下一步", primaryOk = online, primary = () => go((step + 1) as Step), foot = ""
  if (step === 1) {
    foot = "大约 1 分钟"
    if (toConnect.length) { primaryLabel = busy ? "正在连接…" : `连接 ${toConnect.length} 个`; primary = () => void connect() }
    else if (detected.length && !detected.some(linked)) { primaryLabel = "至少选一个"; primaryOk = false }
  }
  if (step === 2) {
    foot = anyWaiting ? "它还在提交也可以先往下走，后到的会进待确认" : "可以跳过，以后在「Agent」页也能让它整理"
    if (shown.length) {
      primaryLabel = busy ? "正在记住…" : keep.length ? `记住 ${keep.length} 条` : "都不记，下一步"
      primary = () => void remember()
      primaryOk = online && !!library
    }
  }
  if (step === 3) {
    primaryLabel = window.omna?.windowAction ? "完成，收进托盘" : "完成"
    primary = () => onExit("tray")
    foot = "OMNA 会在托盘里安静运行，有建议时再叫你"
  }
  if (busy) primaryOk = false

  return <div className="ob">
    <header className="titlebar ob-bar">
      <span className="titlebar-brand"><BrandMark/><span>OMNA</span></span>
      <span className="titlebar-fill"/>
      <span className="ob-dots" role="img" aria-label={`第 ${step} 步，共 3 步`}>{[1, 2, 3].map(n => <i key={n} className={n === step ? "on" : n < step ? "past" : ""}/>)}</span>
      <span className="titlebar-fill"/>
      {controls}
    </header>
    {service.status === "offline" && <div className="global-notice" role="alert">本地服务未运行。恢复后可以接着往下走，已经选好的都还在。</div>}
    <main className="ob-main">
      <div className="ob-body">
        <p className="ob-kicker">第 {step} 步 · 共 3 步</p>
        {loadError && <Notice tone="error">{loadError} <button type="button" className="text-button" onClick={() => void reload()}>重试</button></Notice>}

        {step === 1 && <>
          <h1 className="ob-title">先连上你常用的 AI 工具</h1>
          <p className="ob-lead">OMNA 把配置写进它们自己的设置里，原有配置都保留。连上之后，它们只能读你的「偏好」和「目标」。</p>
          <div className="ob-list">
            {clients === null && !loadError && <p className="helper">正在检测本机的 AI 工具…</p>}
            {detected.map(client => {
              const agent = agentOf(client)
              const note = !linked(client) ? "已安装" : !agent ? "已连接" : `已连接 · ${!agent.enabled ? "已停用" : agent.client_status === "verified" ? "已验证" : "待验证"}`
              return <label key={client.id} className="ob-card ob-client">
                <input type="checkbox" checked={isOn(client)} disabled={linked(client) || busy} onChange={event => setPicked(old => ({ ...old, [client.id]: event.target.checked }))} aria-label={`连接 ${client.name}`}/>
                <span className="ob-mark"><ClientMark id={client.id}/></span>
                <span className="ob-grow"><strong>{client.name}</strong><small>{note}</small></span>
                {hasFile(client.id) && <span className="ob-pill">有你写过的说明</span>}
              </label>
            })}
            {clients !== null && !detected.length && <p className="ob-lead">这台电脑上没检测到支持的 AI 工具。可以先跳过，装好之后在「Agent」页连接。</p>}
          </div>
          {!!missing.length && <p className="ob-meta">没检测到 {missing.map(client => client.name).join("、")}。装好后可以在「Agent」页连接。</p>}
        </>}

        {step === 2 && <>
          <h1 className="ob-title">带上你写过的说明</h1>
          <p className="ob-lead">让刚连上的 AI 工具读一遍它的说明文件，把关于你的内容交给 OMNA。勾上的才会记住。</p>
          <div className="ob-cards">
            {organizers.map(({ client, file }) => {
              const session = sessions[client.id]
              const mine = session ? candidates.filter(item => item.batch === session.batch) : []
              return <OrganizerCard
                key={client.id}
                client={client}
                file={file}
                phase={phases[client.id] || "idle"}
                session={session}
                online={online}
                busy={busy}
                items={mine.filter(item => item.lane === "plain")}
                held={mine.filter(item => item.lane !== "plain").length}
                arrived={session ? proposals.filter(item => item.batch_id === session.batch).length : 0}
                checking={!!session && !library}
                off={off}
                error={orgErrors[client.id]}
                onStart={() => void organize(client)}
                onToggle={id => setOff(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])}
              />
            })}
            {clients !== null && !organizers.length && <p className="ob-empty">连上的工具里没有找到说明文件。可以直接下一步，以后在「记忆」页右上角的「＋」里导入文字或文件。</p>}
          </div>
        </>}

        {step === 3 && <Portrait/>}

        {error && <p className="ob-error" role="alert">{error}</p>}
      </div>
    </main>
    <footer className="ob-foot">
      <span className="ob-meta">{foot}</span>
      <div className="ob-row-actions">
        {step === 2 && <button type="button" className="button" disabled={busy} onClick={() => go(1)}>上一步</button>}
        {step < 3 && <button type="button" className="button ghost" disabled={busy} onClick={() => go((step + 1) as Step)}>跳过</button>}
        <button type="button" className="button primary" disabled={!primaryOk} onClick={primary}>{primaryLabel}</button>
      </div>
    </footer>
  </div>
}

function OrganizerCard({ client, file, phase, session, online, busy, items, held, arrived, checking, off, error, onStart, onToggle }: {
  client: AgentClient
  file: AgentFile
  phase: Phase
  session?: Session
  online: boolean
  busy: boolean
  items: Candidate[]
  held: number
  arrived: number
  checking: boolean
  off: string[]
  error?: string
  onStart: () => void
  onToggle: (id: string) => void
}) {
  const slow = !!session && arrived === 0 && Date.now() - session.started >= SLOW_MS
  return <section className="ob-card ob-organizer">
    <div className="ob-organizer-row">
      <span className="ob-mark"><ClientMark id={client.id}/></span>
      <span className="ob-grow"><strong>{client.name}</strong><code>{file.path}</code></span>
      {phase === "idle" && <button type="button" className="button" disabled={!online} onClick={onStart}>让它整理</button>}
      {phase === "starting" && <span className="ob-wait">正在准备…</span>}
      {phase === "waiting" && <span className="ob-wait"><i className="ob-live"/>{arrived ? `已收到 ${arrived} 条…` : "等它提交…"}</span>}
    </div>
    {phase === "idle" && <p className="ob-sub">会允许它「提议修改」，读取范围不变；它提的每一条都要你勾选才记住。</p>}
    {session && phase === "waiting" && <div className="ob-organizer-body">
      <p className="ob-meta">重启 {client.name}，把这句话发给它：</p>
      <div className="ob-prompt"><span>{session.prompt}</span><CopyButton text={session.prompt} className="ob-copy"/></div>
      {slow && <p className="ob-warn">还没收到。确认 {client.name} 已经重启、在它那边发了这句话。也可以先往下走，以后在「Agent」页还能直接读取这个文件。</p>}
      {checking && arrived > 0 && <p className="ob-meta">正在核对已有记忆…</p>}
      {!!items.length && <ul className="ob-items">{items.map(item => {
        const on = !off.includes(item.proposal.id)
        return <li key={item.proposal.id}><label className="ob-row">
          <input type="checkbox" checked={on} disabled={busy} onChange={() => onToggle(item.proposal.id)} aria-label={`记住：${item.proposal.payload.content}`}/>
          <span className={`ob-grow ob-text${on ? "" : " off"}`}>{item.proposal.payload.content}</span>
          <small className="ob-cat">{categoryLabel(item.proposal.payload.category)}</small>
        </label></li>
      })}</ul>}
      {!!held && <p className="ob-meta ob-note">另有 {held} 条和已有的很像或要逐条看，留在「记忆 › 待确认」里，以后再挑。</p>}
    </div>}
    {error && <p className="ob-error" role="alert">{error}</p>}
  </section>
}

function Portrait() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [readers, setReaders] = useState<AgentConnection[]>([])
  const [error, setError] = useState("")
  const [round, setRound] = useState(0)
  useEffect(() => {
    let alive = true
    Promise.all([
      Promise.all(CATEGORIES.map(([id]) => api.memories({ state: "current", category: id, limit: 3 }).then(page => [id, { items: page.items, total: page.total ?? page.items.length }] as const))),
      api.agents(),
    ]).then(([rows, list]) => { if (alive) { setProfile(Object.fromEntries(rows)); setReaders(list.agents); setError("") } })
      .catch(err => { if (alive) setError(explain(err)) })
    return () => { alive = false }
  }, [round])
  const identity = profile?.identity.items ?? []
  const columns = ["preference", "goal", "project", "event", "other"].filter(id => profile?.[id]?.items.length).slice(0, 2)
  const empty = !!profile && !identity.length && !columns.length
  const reading = readers.filter(agent => agent.enabled && agent.allowed_categories.length && agent.allowed_tools.some(tool => READ_TOOLS.includes(tool)))
  const tryWith = reading[0]
  const tryLine = "先看看你在 OMNA 里能读到我的哪些偏好，再按这些习惯回答我"
  return <>
    <h1 className="ob-title">这是 AI 现在眼中的你</h1>
    {error && <Notice tone="error">{error} <button type="button" className="text-button" onClick={() => setRound(n => n + 1)}>重试</button></Notice>}
    <section className="ob-card ob-portrait">
      {!profile && !error && <p className="helper">正在拼出画像…</p>}
      {empty && <p className="ob-lead">还没有确认过的记忆。以后 AI 工具提的建议，你确认之后会出现在这里。</p>}
      {!!identity.length && <div className="ob-portrait-head"><strong>{identity[0].content}</strong>{identity[1] && <span>{identity[1].content}</span>}</div>}
      {!!columns.length && <div className="ob-portrait-grid">{columns.map(id => <div key={id}>
        <h2>{categoryLabel(id)}</h2>
        <ul>{profile![id].items.map(item => <li key={item.id}>{item.content}</li>)}</ul>
        {profile![id].total > 3 && <p className="ob-meta">还有 {profile![id].total - 3} 条</p>}
      </div>)}</div>}
      {!!profile && <p className="ob-portrait-foot">{readersLine(reading)}</p>}
    </section>
    {tryWith && <div className="ob-try">
      <span className="ob-grow">去 {tryWith.name} 里试一句：<b>「{tryLine}」</b>。它会先从 OMNA 读你允许它看的内容。</span>
      <CopyButton text={tryLine} label="复制这句" className="ob-copy"/>
    </div>}
  </>
}

function readersLine(reading: AgentConnection[]) {
  if (!reading.length) return "现在还没有 Agent 能读到这些。连上并授权之后，它们只读你允许的类别。"
  const groups = new Map<string, AgentConnection[]>()
  for (const agent of reading) {
    const key = CATEGORIES.map(([id]) => id).filter(id => agent.allowed_categories.includes(id)).join(",")
    groups.set(key, [...(groups.get(key) || []), agent])
  }
  const parts = [...groups.entries()].map(([key, list]) => `${joinNames(list.map(agent => agent.name))} 现在能读到其中的${key.split(",").map(id => `「${categoryLabel(id)}」`).join("")}`)
  const identity = reading.some(agent => agent.allowed_categories.includes("identity"))
  return `${parts.join("；")}${identity ? "。" : "，身份信息默认不给。"}`
}
