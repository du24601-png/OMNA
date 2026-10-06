import { t, formatLocale } from "./i18n"
export const CATEGORIES = (): [string, string][] => ([
  ["identity", t("身份")],
  ["goal", t("目标")],
  ["preference", t("偏好")],
  ["project", t("项目")],
  ["event", t("事件")],
  ["other", t("其他")],
])

export function sourceAsset(id: string, dark = false) {
  if (id === "workbuddy") return "/sources/workbuddy.png"
  if (id === "codex") return dark ? "/sources/codex-dark.png" : "/sources/codex.png"
  return `/sources/${id}${dark ? "-dark" : ""}.svg`
}

export function joinNames(names: string[]) { return new Intl.ListFormat(formatLocale(), { style: "long", type: "conjunction" }).format(names) }

export function categoryLabel(value: string) {
  return CATEGORIES().find(([id]) => id === value)?.[1] || value
}

export function sourceLabel(kind: string) {
  if (kind === "paste") return t("粘贴")
  if (kind === "file") return t("文件")
  if (kind === "manual") return t("手动")
  if (kind === "agent_claim") return t("Agent 提案")
  return t("来源")
}

export function lifecycleLabel(value: string) {
  if (value === "active") return t("当前")
  if (value === "superseded") return t("历史")
  return value
}

export const TOOLS = (): [string, string][] => ([
  ["get_context", t("按任务获取记忆")],
  ["search_memory", t("搜索记忆")],
  ["propose_memory", t("提出修改建议")],
  ["explain_memory", t("解释记忆")],
])

export function toolLabel(value: string) {
  return TOOLS().find(([id]) => id === value)?.[1] || value
}

export function outcomeLabel(value: string) {
  if (value === "success") return t("已返回")
  if (value === "empty") return t("没有返回记忆")
  if (value === "rejected") return t("已拒绝")
  return value
}

export function deliveryLabel(value: string) {
  if (value === "prepared") return t("已准备")
  if (value === "sent") return t("已交给发送通道")
  if (value === "failed") return t("发送失败")
  if (value === "unknown") return t("交付未知")
  return value
}

export function dateLabel(value?: string | null) {
  if (!value) return t("时间未提供")
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return t("时间未提供")
  return new Intl.DateTimeFormat(formatLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date)
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
  if (!value) return t("时间未提供")
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return t("时间未提供")
  const dayStart = (item: Date) => new Date(item.getFullYear(), item.getMonth(), item.getDate()).getTime()
  const day = Math.round((dayStart(now) - dayStart(date)) / 86400000)
  if (day === 0) return t("今天")
  if (day === 1) return t("昨天")
  return new Intl.DateTimeFormat(formatLocale(), { month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" as const } : {}) }).format(date)
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
  return new Intl.DateTimeFormat(formatLocale(), { hour: "2-digit", minute: "2-digit" }).format(date)
}

export function listTime(value?: string | null, now = new Date()) {
  if (!value) return t("时间未提供")
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return t("时间未提供")
  const dayStart = (item: Date) => new Date(item.getFullYear(), item.getMonth(), item.getDate()).getTime()
  const day = Math.round((dayStart(now) - dayStart(date)) / 86400000)
  const clock = clockOf(value)
  if (day === 0) return t("今天 {0}", [clock])
  if (day === 1) return t("昨天 {0}", [clock])
  return new Intl.DateTimeFormat(formatLocale(), { month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" as const } : {}) }).format(date)
}

export const TOOL_DESCRIPTIONS = (): Record<string, string> => ({
  get_context: t("按当前任务获取相关且获准的记忆。"),
  search_memory: t("搜索获准类别内的当前有效记忆。"),
  propose_memory: t("提出新增或修改建议，等待你确认。"),
  explain_memory: t("查看获准记忆的已审核证据片段。"),
})