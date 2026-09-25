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
  ["get_context", "获取上下文"],
  ["search_memory", "搜索记忆"],
  ["propose_memory", "提出记忆"],
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
  if (status === "verified") return "已连接"
  return "待验证"
}

export function connectionStatus(agent: { enabled: boolean; client_status: string }) {
  if (!agent.enabled) return "已停用"
  return connectionLabel(agent.client_status)
}
