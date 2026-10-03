// The card on a just-connected client's detail when it has an instruction
// file: one sentence both verifies the connection (the agent reads OMNA
// first) and has the agent organize its own file into suggestions, which
// arrive here with a checkbox. Three states: offer, live, done.
import { Menu } from "@base-ui/react/menu"
import { useEffect, useRef, useState } from "react"
import { api, explain, importMessage, type AgentConnection, type AgentFile, type Memory, type Proposal } from "./api"
import { BATCH_POLL_MS, carryOut, loadLibrary, sortCandidates, undoBatches } from "./batch"
import { CATEGORIES, TOOLS, categoryLabel, joinNames } from "./format"
import { CopyButton, Icon, Notice } from "./ui"
import type { Confirm } from "./Agents"

type Phase = "hidden" | "offer" | "live" | "done" | "queued" | "no"
type Stored = { phase: "live" | "done" | "queued" | "no"; batch?: string; prompt?: string; expires?: string }

const ALL_CATEGORIES = CATEGORIES.map(([id]) => id)
const READ_TOOLS = ["get_context", "search_memory"]
const storeKey = (agentId: string) => `omna-import-card:${agentId}`

function readStored(agentId: string): Stored | null {
  try { return JSON.parse(localStorage.getItem(storeKey(agentId)) || "null") } catch { return null }
}
function writeStored(agentId: string, value: Stored | null) {
  try {
    if (value) localStorage.setItem(storeKey(agentId), JSON.stringify(value))
    else localStorage.removeItem(storeKey(agentId))
  } catch { /* the card just won't come back after a restart */ }
}

export function ConnectImport({ name, agent, file, fresh, online, extractor, agents, onChanged, onConfirm, onCovering }: {
  name: string
  agent: AgentConnection
  file: AgentFile
  fresh: boolean
  online: boolean
  extractor: boolean | null
  agents: AgentConnection[]
  onChanged: () => void
  onConfirm: (request: Confirm) => void
  // True while this card stands in for the plain "send this sentence" guide.
  onCovering: (covering: boolean) => void
}) {
  const stored = useRef(readStored(agent.id))
  const [phase, setPhase] = useState<Phase>(() => {
    const value = stored.current
    if (value?.phase === "live" && value.batch) return "live"
    if (value?.phase === "no") return fresh ? "no" : "hidden"
    if (value) return "hidden"
    return fresh && !file.imported_at ? "offer" : "hidden"
  })
  const [batch, setBatch] = useState(stored.current?.batch || "")
  const [prompt, setPrompt] = useState(stored.current?.prompt || "")
  const [proposals, setProposals] = useState<Proposal[] | null>(null)
  const [library, setLibrary] = useState<Memory[] | null>(null)
  const [off, setOff] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [summary, setSummary] = useState({ accepted: 0, categories: [] as string[], queued: "" })
  const keys = useRef(new Map<string, string>())
  const keyFor = (sig: string) => { let key = keys.current.get(sig); if (!key) { key = crypto.randomUUID(); keys.current.set(sig, key) } return key }
  const needConsent = !(agent.enabled && agent.allowed_tools.includes("propose_memory") && agent.propose_categories.length > 0)
  const extractionReady = online && extractor !== null

  useEffect(() => { onCovering(phase === "offer" || phase === "live") }, [phase])
  useEffect(() => {
    if (phase !== "live" || !batch) return
    let alive = true
    const load = () => api.proposals().then(value => { if (alive) setProposals(value.proposals) }).catch(() => undefined)
    void load()
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load() }, BATCH_POLL_MS)
    return () => { alive = false; window.clearInterval(timer) }
  }, [phase, batch])
  useEffect(() => {
    if (phase !== "live" || library) return
    loadLibrary().then(setLibrary).catch(err => setError(`还没核对已有记忆：${explain(err)}`))
  }, [phase, library])
  const members = (proposals ?? []).filter(item => item.batch_id === batch)
  useEffect(() => {
    // A session that ran out with nothing left to look at goes away.
    const expired = !!stored.current?.expires && new Date(stored.current.expires).getTime() < Date.now()
    if (phase === "live" && proposals && !members.length && expired) { remember({ phase: "done", batch }); setPhase("hidden") }
  }, [proposals])

  function remember(next: Stored | null) { stored.current = next; writeStored(agent.id, next) }
  async function start() {
    if (busy) return
    setBusy(true); setError("")
    try {
      if (needConsent) {
        const wanted = new Set([...agent.allowed_tools, "propose_memory"])
        await api.updateAgent(agent.id, {
          allowed_tools: TOOLS.map(([id]) => id).filter(id => wanted.has(id)),
          propose_categories: agent.propose_categories.length ? agent.propose_categories : ALL_CATEGORIES,
        }, keyFor(`allow:${agent.id}:${agent.policy_version}`))
        onChanged()
      }
      const session = await api.organize(agent.id)
      setBatch(session.batch_id); setPrompt(session.prompt); setOff([])
      remember({ phase: "live", batch: session.batch_id, prompt: session.prompt, expires: session.expires_at })
      setPhase("live")
    } catch (err) { setError(explain(err)) }
    finally { setBusy(false) }
  }
  function readDirect() {
    onConfirm({
      title: `直接读取 ${file.path}`,
      body: extractor
        ? "会把这个文件发给你配置的提取模型，提取出的内容放进待确认。只读一次，不改动原文件。"
        : "按标题和列表拆成一条条，放进待确认。没用 AI 判断，类别是按标题猜的，确认时可以改。只读一次，不改动原文件。",
      action: "读取",
      run: async () => {
        try {
          const job = await api.importSource({ kind: "agent_file", file_id: file.id }, keyFor(`file:${file.id}`))
          setSummary(old => ({ ...old, queued: importMessage(job, false) }))
          remember({ phase: "queued" })
          setPhase("queued")
          onChanged()
        } catch (err) { setError(explain(err)) }
      },
    })
  }
  const candidates = library ? sortCandidates(proposals ?? [], new Set([batch]), library) : []
  const plain = candidates.filter(item => item.lane === "plain")
  const held = candidates.filter(item => item.lane !== "plain")
  const kept = plain.filter(item => !off.includes(item.proposal.id))
  async function rememberKept() {
    if (busy || !kept.length) return
    setBusy(true); setError("")
    try {
      const outcome = await carryOut({ keep: kept, drop: plain.filter(item => off.includes(item.proposal.id)), categories: {}, choices: [], closed: new Set() }, keyFor)
      setSummary(old => ({ ...old, accepted: old.accepted + outcome.accepted, categories: [...new Set([...old.categories, ...outcome.categories])] }))
      if (outcome.errors.length) { setError(`${outcome.errors.length} 条没有完成：${outcome.errors[0]} 再点一次会接着做，不会重复记住。`); setProposals((await api.proposals()).proposals); return }
      remember({ phase: "done", batch })
      setPhase("done")
      onChanged()
    } catch (err) { setError(explain(err)) }
    finally { setBusy(false) }
  }
  function undo() {
    const count = summary.accepted
    onConfirm({
      title: "撤销这次整理",
      body: `会永久删除这次整理记住的 ${count} 条记忆，删除后不能恢复。其中有之后又改过的，整批撤销会被拒绝，不会删一半。`,
      action: `删除这 ${count} 条`,
      danger: true,
      run: async () => {
        try {
          const deleted = await undoBatches([batch])
          setSummary({ accepted: 0, categories: [], queued: `已撤销这次整理，删除了 ${deleted} 条。` })
          remember(null); setOff([]); setPhase("offer"); onChanged()
        } catch (err) { setError(explain(err)) }
      },
    })
  }

  if (phase === "hidden") return null
  const readers = agents.filter(item => item.enabled && item.allowed_tools.some(tool => READ_TOOLS.includes(tool)) && item.allowed_categories.some(id => summary.categories.includes(id)))
  return <section className="ci" aria-label="带上说明文件">
    {error && <Notice tone="error">{error}</Notice>}

    {phase === "offer" && <div className="ci-card raised">
      {summary.queued && <p className="ci-meta">{summary.queued}</p>}
      <div className="ci-head">
        <span className="ci-icon"><FileIcon/></span>
        <div className="ci-grow">
          <h3>让 {name} 读一遍你写给它的说明</h3>
          <p className="ci-meta">它挑出关于你的内容交给 OMNA，你勾选后才记住。{needConsent ? "需要允许它「提议修改」，读取范围不变。" : ""}</p>
          <code>{file.path}</code>
        </div>
        <Menu.Root>
          <Menu.Trigger className="icon-button ci-more" aria-label="更多方式" disabled={busy}><Icon name="more"/></Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className="detail-positioner" side="bottom" align="end" sideOffset={6}>
              <Menu.Popup className="select-popup">
                <Menu.Item className="tool-option" disabled={!extractionReady} onClick={readDirect}>{!extractionReady ? "直接读取文件（等本机服务就绪）" : extractor ? "直接读取文件（用你配置的提取模型）" : "直接读取文件（不用 AI）"}</Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <div className="ci-actions">
        <span className="ci-grow"/>
        <button type="button" className="button ghost" disabled={busy} onClick={() => { remember({ phase: "no" }); setPhase("no") }}>不用了</button>
        <button type="button" className="button primary" disabled={!online || busy} onClick={() => void start()}>{busy ? "正在准备…" : needConsent ? "允许并开始" : "开始"}</button>
      </div>
    </div>}

    {phase === "live" && <div className="ci-card raised">
      <div className="ci-head center">
        <i className="ci-live"/>
        <h3 className="ci-grow">{members.length ? `已收到 ${members.length} 条，${name} 还在提交…` : `重启 ${name}，把这句发给它`}</h3>
        <button type="button" className="button ghost" onClick={() => { remember({ phase: "no" }); setPhase("no") }}>停止</button>
      </div>
      <div className="ci-indent">
        <div className="ci-prompt"><span>{prompt}</span><CopyButton text={prompt} className="ob-copy"/></div>
        {!!members.length && !library && <p className="ci-meta">正在核对已有记忆…</p>}
        {!!candidates.length && <ul className="ci-items">
          {plain.map(item => {
            const on = !off.includes(item.proposal.id)
            return <li key={item.proposal.id}><label>
              <input type="checkbox" checked={on} disabled={busy} onChange={() => setOff(old => on ? [...old, item.proposal.id] : old.filter(x => x !== item.proposal.id))} aria-label={`记住：${item.proposal.payload.content}`}/>
              <span className={`ci-grow${on ? "" : " off"}`}>{item.proposal.payload.content}</span>
              <small>{categoryLabel(item.proposal.payload.category)}</small>
            </label></li>
          })}
          {held.map(item => <li key={item.proposal.id}><label>
            <input type="checkbox" checked={false} disabled aria-label={`留在待确认：${item.proposal.payload.content}`}/>
            <span className="ci-grow">{item.proposal.payload.content}</span>
            <span className="ci-tag">{item.lane === "similar" ? "和已有的很像，留在待确认" : "要逐条看，留在待确认"}</span>
            <small>{categoryLabel(item.proposal.payload.category)}</small>
          </label></li>)}
        </ul>}
        <div className="ci-actions">
          <span className="ci-grow ci-meta">关掉这页也会继续收；没勾的不会记住。</span>
          <button type="button" className="button primary" disabled={!online || busy || !kept.length} onClick={() => void rememberKept()}>{busy ? "正在记住…" : kept.length ? `记住 ${kept.length} 条` : "记住"}</button>
        </div>
      </div>
    </div>}

    {phase === "done" && <div className="ci-strip">
      <span className="ci-grow"><b>已记住 {summary.accepted} 条。</b>{readers.length ? `${joinNames(readers.map(item => item.name))} 下次都会用上。` : "现在还没有 Agent 能读到这些类别，可以在「权限」里打开。"}</span>
      {!!summary.accepted && <button type="button" className="ci-strip-button" onClick={undo}>撤销</button>}
    </div>}

    {phase === "queued" && <div className="ci-strip">
      <span className="ci-grow"><b>{summary.queued || "已放进待确认"}</b> 确认前不会生效。</span>
      <button type="button" className="ci-strip-button" onClick={() => { location.hash = "#/review" }}>去确认</button>
    </div>}

    {phase === "no" && <button type="button" className="ci-link" onClick={() => { remember(null); setPhase("offer") }}>还是让它整理说明文件</button>}
  </section>
}

function FileIcon() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>
}
