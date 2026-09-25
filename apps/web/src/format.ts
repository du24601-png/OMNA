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
  return "来源"
}

export function lifecycleLabel(value: string) {
  if (value === "active") return "当前"
  if (value === "superseded") return "历史"
  return value
}
