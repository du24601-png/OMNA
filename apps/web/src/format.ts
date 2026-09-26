export const CATEGORIES: [string, string][] = [
  ["identity", "身份"],
  ["goal", "目标"],
  ["preference", "偏好"],
  ["project", "项目"],
  ["event", "事件"],
  ["other", "其他"],
]

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

export function connectionLabel(status: string) {
  if (status === "verified") return "曾验证成功"
  return "待验证"
}

export function sharingLabel(memory: { share_enabled?: boolean }) {
  return memory.share_enabled === false ? "仅自己可见" : "允许已授权 Agent 读取"
}

export function dateLabel(value?: string | null) {
  if (!value) return "时间未提供"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间未提供"
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date)
}

export function relativeTime(value?: string | null) {
  if (!value) return "时间未提供"
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return "时间未提供"
  const minutes = Math.round((Date.now() - date.getTime()) / 60000)
  if (minutes < 1) return "刚刚"
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days} 天前`
  return dateLabel(value)
}

export const TOOL_DESCRIPTIONS: Record<string, string> = {
  get_context: "按当前任务获取相关且获准的记忆。",
  search_memory: "搜索获准类别内的当前有效记忆。",
  propose_memory: "提出新增或修改建议，等待你确认。",
  explain_memory: "查看获准记忆的已审核证据片段。",
}

export function connectionStatus(agent: { enabled: boolean; client_status: string }) {
  if (!agent.enabled) return "已停用"
  return connectionLabel(agent.client_status)
}
