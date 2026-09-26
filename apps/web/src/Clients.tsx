import { useRef, useState } from "react"
import { api, explain, type AgentClient } from "./api"
import { sourceAsset } from "./format"
import { Notice, ResourceNotice, useResource } from "./ui"

const DUAL_MARKS = new Set(["zcode", "opencode", "codex"])

function ClientMark({ id }: { id: string }) {
  const dual = DUAL_MARKS.has(id)
  return <>
    <img className={dual ? "source-mark light dual" : "source-mark"} src={sourceAsset(id)} alt="" />
    {dual && <img className="source-mark dark" src={sourceAsset(id, true)} alt="" />}
  </>
}

export function ClientBoard({ online, onConnected }: { online: boolean; onConnected: (agentId: string) => void }) {
  const resource = useResource(() => api.agentClients(), [])
  const clients = resource.data?.clients ?? []
  const connected = clients.filter(client => client.configured).length
  const [picked, setPicked] = useState<string | null>(null)
  const [preset, setPreset] = useState<"read" | "propose">("read")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const keys = useRef(new Map<string, string>())

  function keyFor(signature: string) {
    const existing = keys.current.get(signature)
    if (existing) return existing
    const created = crypto.randomUUID()
    keys.current.set(signature, created)
    return created
  }

  async function write(client: AgentClient) {
    if (busy || !online || !client.installed) return
    const signature = `connect:${client.id}:${preset}`
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const result = await api.connectClient(client.id, preset, keyFor(signature))
      keys.current.delete(signature)
      setPicked(null)
      setNotice(`已写入 ${result.name} 的配置。打开它，问一次已授权的记忆。`)
      resource.reload()
      onConnected(result.agent_id)
    } catch (err) {
      setError(explain(err))
    } finally {
      setBusy(false)
    }
  }

  return <section className="client-board">
    <header>
      <div>
        <h2>本机客户端</h2>
        <p className="helper">已连接 {connected} / {clients.length || 6}。把知我接到你正在用的客户端：WorkBuddy、ZCode、OpenCode、ChatGPT、Claude、Claude Code。没安装的先装好。点「连接」并确认后，才会写入那个客户端自己的配置。然后打开它，问一次已授权的记忆，才算验证成功。</p>
      </div>
      <button className="button secondary" type="button" onClick={() => resource.reload()}>刷新连接</button>
    </header>
    <ResourceNotice resource={resource} />
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!!clients.length && <div className="client-table">
      {clients.map(client => {
        const open = picked === client.id
        const status = !client.installed ? "未安装" : client.configured ? "配置已写入" : "未连接"
        return <div key={client.id} className="client-row">
          <div className="client-main">
            <ClientMark id={client.id} />
            <strong>{client.name}</strong>
            <span className={`status-pill ${client.configured ? "verified" : ""}`}>{client.configured ? "✓ " : ""}{status}</span>
          </div>
          <button
            className="button secondary"
            type="button"
            disabled={!online || busy || !client.installed}
            onClick={() => { setPicked(open ? null : client.id); setError(""); setPreset("read") }}
          >{client.configured ? "重新写入" : "连接"}</button>
          {open && <form className="client-confirm" onSubmit={event => { event.preventDefault(); void write(client) }}>
            <p className="helper">{client.configured ? "重新写入会让这个客户端里的旧配置失效。" : "确认后才会写入。"}凭证放进它自己的配置文件，页面不会显示。</p>
            <fieldset>
              <label><input type="radio" name={`preset-${client.id}`} checked={preset === "read"} onChange={() => setPreset("read")} />只读：偏好和目标，可以查询</label>
              <label><input type="radio" name={`preset-${client.id}`} checked={preset === "propose"} onChange={() => setPreset("propose")} />可以提议修改</label>
            </fieldset>
            <div className="actions">
              <button className="button primary" type="submit" disabled={busy || !online}>{busy ? "写入中…" : "确认写入"}</button>
              <button className="button secondary" type="button" disabled={busy} onClick={() => setPicked(null)}>取消</button>
            </div>
          </form>}
        </div>
      })}
    </div>}
  </section>
}
