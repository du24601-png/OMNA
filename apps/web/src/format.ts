export const CATEGORIES: [string, string][] = [
  ["identity", "身份"],
  ["goal", "目标"],
  ["preference", "偏好"],
  ["project", "项目"],
  ["event", "事件"],
  ["other", "其他"],
]

export function sourceAsset(id: string, dark = false) {
  if (id === "workbuddy") return "/sources/workbuddy.png"
  if (id === "codex") return dark ? "/sources/codex-dark.png" : "/sources/codex.png"
  return `/sources/${id}${dark ? "-dark" : ""}.svg`
}

export function joinNames(names: string[]) {
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join("、")} 和 ${names[names.length - 1]}`
}

export function categoryLabel(value: string) {
  return CATEGORIES.find(([id]) => id === value)?.[1] || value
}

export function sourceLabel(kind: string) {
  if (kind === "paste") return "粘贴"
  if (kind === "file") return "文件"
  if (kind === "manual") return "手动"
  if (kind === "agent_claim") return "Agent 提案"
  return "来源"
}

export function lifecycleLabel(value: string) {
  if (value === "active") return "当前"
  if (value === "superseded") return "历史"
  return value
}

export const TOOLS: [string, string][] = [
  ["get_context", "按任务获取记忆"],
  ["search_memory", "搜索记忆"],
  ["propose_memory", "提出修改建议"],
  ["explain_memory", "解释记忆"],
]

export function toolLabel(value: string) {
  return TOOLS.find(([id]) => id === value)?.[1] || value
}

export function outcomeLabel(value: string) {
  if (value === "success") return "已返回"
  if (value === "empty") return "没有返回记忆"
  if (value === "rejected") return "已拒绝"
  return value
}

export function deliveryLabel(value: string) {
  if (value === "prepared") return "已准备"
  if (value === "sent") return "已交给发送通道"
  if (value === "failed") return "发送失败"
  if (value === "unknown") return "交付未知"
  return value
}

export function dateLabel(value?: string | null) {
  if (!value) return "时间未提供"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间未提供"
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date)
}

export type AccessListItem = {
  id: string
  tool: string
  outcome: string
  created_at: string
  delivery_state: string
  returned?: string[]
}

export type AccessGroup = {
  id: string
  tool: string
  events: AccessListItem[]
  lines: string[]
  quiet: boolean
}

const ACCESS_WINDOW_MS = 5 * 60 * 1000

function eventTime(value: string) {
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : NaN
}

function isQuiet(event: AccessListItem) {
  if (event.outcome === "rejected") return false
  if (event.delivery_state !== "sent") return false
  if (event.returned?.length) return false
  if (event.tool === "propose_memory" && event.outcome === "success") return false
  return true
}

/** Same tool within five minutes of the group's newest call becomes one group. */
export function groupAccess(events: AccessListItem[]): AccessGroup[] {
  const sorted = [...events].sort((a, b) => eventTime(b.created_at) - eventTime(a.created_at) || b.id.localeCompare(a.id))
  const groups: AccessGroup[] = []
  const open = new Map<string, AccessGroup>()
  for (const event of sorted) {
    const time = eventTime(event.created_at)
    const current = open.get(event.tool)
    const anchor = current ? eventTime(current.events[0].created_at) : NaN
    if (current && Number.isFinite(time) && Number.isFinite(anchor) && anchor - time <= ACCESS_WINDOW_MS) {
      current.events.push(event)
      continue
    }
    const created: AccessGroup = { id: event.id, tool: event.tool, events: [event], lines: [], quiet: false }
    groups.push(created)
    open.set(event.tool, created)
  }
  for (const group of groups) {
    const seen = new Set<string>()
    for (const event of group.events) {
      for (const line of event.returned ?? []) {
        if (seen.has(line)) continue
        seen.add(line)
        group.lines.push(line)
      }
    }
    group.quiet = group.events.every(isQuiet)
  }
  return groups.sort((a, b) => eventTime(b.events[0].created_at) - eventTime(a.events[0].created_at) || b.id.localeCompare(a.id))
}

export function dayHeading(value?: string | null, now = new Date()) {
  if (!value) return "时间未提供"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间未提供"
  const dayStart = (item: Date) => new Date(item.getFullYear(), item.getMonth(), item.getDate()).getTime()
  const day = Math.round((dayStart(now) - dayStart(date)) / 86400000)
  if (day === 0) return "今天"
  if (day === 1) return "昨天"
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}

export function spanLabel(newest?: string, oldest?: string) {
  const start = listTime(oldest)
  const end = listTime(newest)
  if (start === end) return end
  const startClock = clockOf(oldest)
  const endClock = clockOf(newest)
  if (dayHeading(newest) === dayHeading(oldest) && startClock && endClock) return `${dayHeading(oldest)} ${startClock}–${endClock}`
  return `${start} – ${end}`
}

function clockOf(value?: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ""
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`
}

export function listTime(value?: string | null, now = new Date()) {
  if (!value) return "时间未提供"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间未提供"
  const dayStart = (item: Date) => new Date(item.getFullYear(), item.getMonth(), item.getDate()).getTime()
  const day = Math.round((dayStart(now) - dayStart(date)) / 86400000)
  const clock = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`
  if (day === 0) return `今天 ${clock}`
  if (day === 1) return `昨天 ${clock}`
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}

export const TOOL_DESCRIPTIONS: Record<string, string> = {
  get_context: "按当前任务获取相关且获准的记忆。",
  search_memory: "搜索获准类别内的当前有效记忆。",
  propose_memory: "提出新增或修改建议，等待你确认。",
  explain_memory: "查看获准记忆的已审核证据片段。",
}