import { useState } from "react"
import type { AgentClient, AgentConnection } from "./api"
import { sourceAsset } from "./format"

const DUAL_MARKS = new Set(["zcode", "opencode", "codex"])

export type Preset = "read" | "propose"

export const PRESETS: [Preset, string, string][] = [
  ["read", "只读", "查看你允许的记忆"],
  ["propose", "可提议修改", "还能提出新记忆，你确认后才生效"],
]

export function presetOf(tools: string[]): Preset {
  return tools.includes("propose_memory") ? "propose" : "read"
}

export function ClientMark({ id }: { id: string }) {
  const dual = DUAL_MARKS.has(id)
  return <>
    <img className={dual ? "source-mark light dual" : "source-mark"} src={sourceAsset(id)} alt="" />
    {dual && <img className="source-mark dark" src={sourceAsset(id, true)} alt="" />}
  </>
}

export function PresetChoice({ value, disabled, onChange }: { value: Preset | null; disabled?: boolean; onChange: (next: Preset) => void }) {
  return <div className="choice-cards" role="radiogroup" aria-label="它可以做什么">
    {PRESETS.map(([id, title, text]) => <button
      key={id}
      type="button"
      role="radio"
      aria-checked={value === id}
      className={`choice-card ${value === id ? "selected" : ""}`}
      disabled={disabled}
      onClick={() => onChange(id)}
    >
      <span className="choice-radio" aria-hidden="true" />
      <span><strong>{title}</strong><small>{text}</small></span>
    </button>)}
  </div>
}

export function ConnectPanel({ client, agent, online, busy, onConnect }: {
  client: AgentClient
  agent?: AgentConnection
  online: boolean
  busy: boolean
  onConnect: (preset: Preset) => void
}) {
  const [preset, setPreset] = useState<Preset>(agent ? presetOf(agent.allowed_tools) : "read")
  const lost = !!agent && !client.configured
  return <div className="connect-panel">
    <span className="hero-mark"><ClientMark id={client.id} /></span>
    <h2>把知我接到 {client.name}</h2>
    <p className="helper">{lost ? `${client.name} 的配置里已经没有知我了，重新连接即可恢复。` : `${client.name} 只能读取你允许的已确认记忆。`}</p>
    <h3 className="field-title">它可以做什么</h3>
    <PresetChoice value={preset} disabled={busy} onChange={setPreset} />
    <p className="helper">先开放「偏好」和「目标」，连接后随时可以调整。</p>
    <button className="button primary connect-go" type="button" disabled={!online || busy} onClick={() => onConnect(preset)}>
      {busy ? "正在写入…" : `连接 ${client.name}`}
    </button>
    <p className="fine-print">只在 {client.name} 的配置文件里加一条 zhiwo，不动其他设置。凭证只写进那个文件，页面不显示。</p>
  </div>
}
