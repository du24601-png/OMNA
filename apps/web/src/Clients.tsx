import { t } from "./i18n"
import type { AgentClient, AgentConnection } from "./api"
import { sourceAsset } from "./format"

const DUAL_MARKS = new Set(["zcode", "opencode", "codex"])

export type Preset = "read" | "propose"

export const PRESETS = (): [Preset, string, string][] => ([
  ["read", t("只读"), t("查看你允许的记忆")],
  ["propose", t("可提议修改"), t("还能提出新记忆，你确认后才生效")],
])

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
  return <div className="choice-cards" role="radiogroup" aria-label={t("它可以做什么")}>
    {PRESETS().map(([id, title, text]) => <button
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
  const preset = agent ? presetOf(agent.allowed_tools) : "read"
  return <div className="connect-panel">
    <span className="hero-mark"><ClientMark id={client.id} /></span>
    <h2>{t("把 OMNA 接到")} {client.name}</h2>
    <button className="button primary connect-go" type="button" disabled={!online || busy} onClick={() => onConnect(preset)}>
      {busy ? t("正在写入…") : t("连接 {0}", [client.name])}
    </button>
  </div>
}
