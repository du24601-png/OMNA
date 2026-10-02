export class ApiError extends Error {
  status: number
  code: string
  retryable: boolean

  constructor(status: number, code: string, message: string, retryable = false) {
    super(message)
    this.status = status
    this.code = code
    this.retryable = retryable
  }
}

export type Memory = {
  id: string
  revision: number
  content: string | null
  kind: string
  category: string
  scope?: string | null
  lifecycle?: string
  share_enabled?: boolean
  valid_until?: string | null
  source_ids?: string[]
  origin?: { client: string; name: string }
  readable?: boolean
  created_at?: string
  evidence?: string | null
  reads_7d?: number
}

export type Version = Memory & { lifecycle: string }

export type Proposal = {
  id: string
  status: string
  decision: string | null
  change_type: string
  target_id: string | null
  base_revision: number | null
  payload: { content: string; kind: string; category: string; scope: string | null; share_enabled?: boolean; valid_until?: string | null }
  evidence: { text?: string; source_id?: string }
  source: { id: string; kind: string; name: string | null }
  requester?: { client: string; name: string }
  demo: boolean
}

export type Source = {
  id: string
  kind: string
  name: string | null
  content: string
  imported_at: string
}

export type AgentClient = {
  id: string
  name: string
  installed: boolean
  configured: boolean
  config_path: string
  agent_id: string | null
}

export type AgentConnection = {
  id: string
  name: string
  enabled: boolean
  policy_version: number
  allowed_tools: string[]
  allowed_categories: string[]
  propose_categories: string[]
  client_status: string
  last_access_at?: string | null
  credential?: string
}

export type AccessVersion = { id: string; revision: number }

export type AccessEvent = {
  id: string
  request_id: string
  agent_id: string | null
  tool: string
  outcome: string
  policy_version: number | null
  created_at: string
  delivery_state: string
  versions: AccessVersion[]
  returned?: string[]
}

export type AccessDetail = AccessEvent & {
  response: {
    items?: Memory[]
    result?: { id?: string; revision?: number; evidence?: string | null; source_kind?: string | null }
    error?: { code?: string; message?: string }
    proposal_id?: string
    status?: string
  }
}

export type TrayStatus = {
  service: { ok: boolean; embeddings_loaded: boolean }
  pending: { count: number; latest_at: string | null }
  sharing: { paused: boolean; paused_until: string | null }
  reads_today: { total: number; agents: { id: string; name: string; count: number }[] }
  recent_reads: { event_id: string; agent_id: string | null; agent_name: string; tool: string; count: number; categories: Record<string, number>; at: string }[]
}

export type ImportJob = {
  source_id?: string
  job_id: string
  status: string
  error_code: string | null
  proposals: { id: string; status: string; payload: { content: string } }[]
}

declare global {
  interface Window {
    omna?: {
      ownerCredential?: string
      windowAction?: (action: "minimize" | "maximize" | "close") => void
      maximized?: () => boolean
      onMaximized?: (listener: (value: boolean) => void) => () => void
    }
  }
}

const desktopCredential = window.omna?.ownerCredential || ""
let credential = desktopCredential || sessionStorage.getItem("zhiwo-owner-credential") || ""

export function setCredential(value: string) {
  if (desktopCredential) return
  credential = value
  if (value) sessionStorage.setItem("zhiwo-owner-credential", value)
  else sessionStorage.removeItem("zhiwo-owner-credential")
}

export function getCredential() {
  return credential
}

export const embeddingHelp = desktopCredential
  ? "请从托盘菜单「打开日志文件夹」查看 service.log，或重新安装 OMNA。"
  : "请查看本地服务日志。"

export function explain(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : "没有完成，请稍后重试。"
  if (error.status === 0 || error.code === "UNAVAILABLE") return "本地服务未运行。刚才的内容还在，可以重试。"
  if (error.code === "UNAUTHENTICATED") return desktopCredential ? "本地服务没有认出这个窗口，请从托盘退出后重新打开 OMNA。" : "本机凭证不正确。"
  if (error.code === "MODEL_UNAVAILABLE" && error.message === "local embedding model is not ready") {
    return `本地向量模型没有加载，这次没有保存。重试不会改变结果，${embeddingHelp}`
  }
  if (error.code === "MODEL_UNAVAILABLE") return "模型还没准备好，这次没有保存。"
  if (error.code === "CONFLICT" && error.message.includes("memory changed")) {
    return "当前记忆已经更新，这条建议不能再改它。"
  }
  if (error.code === "CONFLICT" || error.code === "VALIDATION_ERROR") return error.message
  return error.message || "没有完成，请查看原因后重试。"
}

async function request(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  if (credential) headers.set("Authorization", `Bearer ${credential}`)
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    window.dispatchEvent(new Event("zhiwo:unavailable"))
    throw new ApiError(0, "UNAVAILABLE", "本地服务未运行", true)
  }
  if (response.status === 204) return null
  const text = await response.text()
  let body: { error?: { code?: string; message?: string; retryable?: boolean } } | null = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      throw new ApiError(0, "UNAVAILABLE", "本地服务未运行", true)
    }
  }
  if (!response.ok) {
    const error = body?.error
    if (!error?.code && response.status >= 500) {
      throw new ApiError(0, "UNAVAILABLE", "本地服务未运行", true)
    }
    throw new ApiError(
      response.status,
      error?.code || "REQUEST_FAILED",
      error?.message || "请求没有完成",
      Boolean(error?.retryable),
    )
  }
  return body
}

export const api = {
  health() {
    return request("/health") as Promise<{ status: string }>
  },
  ownerHealth() {
    return request("/api/v1/health") as Promise<{
      status: string
      embeddings_loaded: boolean
      connect_only: boolean
      extractor_configured: boolean
      test_mode: boolean
      mcp_runtime?: { command: string[]; environment: Record<string, string> }
    }>
  },
  status() {
    return request(`/api/v1/status?utc_offset_minutes=${new Date().getTimezoneOffset()}`) as Promise<TrayStatus>
  },
  pauseSharing() {
    return request("/api/v1/sharing/pause", { method: "POST" }) as Promise<TrayStatus["sharing"]>
  },
  resumeSharing() {
    return request("/api/v1/sharing/pause", { method: "DELETE" }) as Promise<TrayStatus["sharing"]>
  },
  memories(params: { query?: string; state?: string; category?: string; origin?: string; limit?: number; sort?: "newest" | "oldest"; cursor?: string }) {
    const search = new URLSearchParams()
    if (params.query) search.set("query", params.query)
    if (params.state) search.set("state", params.state)
    if (params.category) search.set("category", params.category)
    if (params.origin) search.set("origin", params.origin)
    if (params.sort) search.set("sort", params.sort)
    if (params.cursor) search.set("cursor", params.cursor)
    search.set("limit", String(params.limit || 50))
    return request(`/api/v1/memories?${search}`) as Promise<{ items: Memory[]; origins?: { id: string; name: string }[]; total?: number; next_cursor?: string | null; truncated?: boolean }>
  },
  memory(id: string) {
    return request(`/api/v1/memories/${id}`) as Promise<Memory>
  },
  versions(id: string) {
    return request(`/api/v1/memories/${id}/versions`) as Promise<{ memory_id: string; versions: Version[] }>
  },
  createMemory(body: Record<string, unknown>, key: string) {
    return request("/api/v1/memories", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    })
  },
  updateMemory(id: string, body: Record<string, unknown>, key: string) {
    return request(`/api/v1/memories/${id}`, {
      method: "PATCH",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    })
  },
  source(id: string) {
    return request(`/api/v1/sources/${id}`) as Promise<Source>
  },
  proposals() {
    return request("/api/v1/proposals?status=pending") as Promise<{ proposals: Proposal[] }>
  },
  decide(id: string, body: Record<string, unknown>, key: string) {
    return request(`/api/v1/proposals/${id}/decision`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    }) as Promise<{ status?: string; decision?: string; revision?: number; memory_id?: string }>
  },
  importSource(body: Record<string, unknown>, key: string) {
    return request("/api/v1/imports", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    }) as Promise<ImportJob>
  },
  retryImport(jobId: string) {
    return request(`/api/v1/imports/${jobId}/retry`, { method: "POST", body: "{}" }) as Promise<ImportJob>
  },
  agents() {
    return request("/api/v1/agents") as Promise<{ agents: AgentConnection[] }>
  },
  agentClients() {
    return request("/api/v1/agent-clients") as Promise<{ clients: AgentClient[] }>
  },
  connectClient(id: string, preset: "read" | "propose", key: string, permissions?: { allowed_tools: string[]; allowed_categories: string[]; propose_categories?: string[] }) {
    return request(`/api/v1/agent-clients/${encodeURIComponent(id)}/connect`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ preset, confirm: true, ...(permissions ?? {}) }),
    }) as Promise<{ client_id: string; name: string; agent_id: string; preset: string; config_path: string; configured: boolean }>
  },
  createAgent(name: string, key: string) {
    return request("/api/v1/agents", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ name }),
    }) as Promise<AgentConnection>
  },
  updateAgent(id: string, body: Record<string, unknown>, key: string) {
    return request(`/api/v1/agents/${id}`, {
      method: "PATCH",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    }) as Promise<AgentConnection>
  },
  rotateAgent(id: string, key: string) {
    return request(`/api/v1/agents/${id}/rotate-credential`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }) as Promise<AgentConnection>
  },
  accessEvents(agentId: string) {
    return request(`/api/v1/access-events?agent_id=${encodeURIComponent(agentId)}`) as Promise<{ events: AccessEvent[] }>
  },
  accessEvent(id: string) {
    return request(`/api/v1/access-events/${id}`) as Promise<AccessDetail>
  },
  settings() {
    return request("/api/v1/settings") as Promise<SettingsView>
  },
  saveSettings(body: { extractor_base_url: string; extractor_model: string; extractor_api_key?: string }, key: string) {
    return request("/api/v1/settings", {
      method: "PATCH",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(body),
    }) as Promise<SettingsView>
  },
  testModel(key: string) {
    return request("/api/v1/settings/test-model", {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }) as Promise<{ ok: boolean }>
  },
  pickDataDir() {
    return request("/api/v1/settings/data-dir/pick", { method: "POST" }) as Promise<{ cancelled: boolean; path?: string }>
  },
  openDataDir(path?: string) {
    return request("/api/v1/settings/data-dir/open", {
      method: "POST",
      body: JSON.stringify(path ? { path } : {}),
    }) as Promise<{ status: string; path: string }>
  },
  downloadExport() {
    return download("/api/v1/exports", "zhiwo-export.zip")
  },
  downloadBackup() {
    return download("/api/v1/backups", "zhiwo-backup.zip")
  },
  restoreBackup(file: ArrayBuffer, key: string) {
    return request(`/api/v1/restores?confirm=${encodeURIComponent("恢复备份")}`, {
      method: "POST",
      headers: {
        "Idempotency-Key": key,
        "Content-Type": "application/zip",
      },
      body: file,
    }) as Promise<{ status: string }>
  },
  resetData(key: string) {
    return request("/api/v1/data/reset", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ confirm: "清空数据" }),
    }) as Promise<{ status: string }>
  },
  deletionPreview(id: string) {
    return request(`/api/v1/memories/${id}/deletion-preview`) as Promise<{
      memory_id: string
      version_count: number
      sources: { id: string; kind: string; name: string | null }[]
    }>
  },
  deleteMemory(id: string, key: string) {
    return request(`/api/v1/memories/${id}`, {
      method: "DELETE",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ confirm: true }),
    }) as Promise<{ memory_id: string; status: string }>
  },
}

export type SettingsView = {
  data_dir: string
  extractor: { base_url: string; model: string; key_saved: boolean; configured: boolean }
}

async function download(path: string, fallback: string) {
  const headers = new Headers()
  if (credential) headers.set("Authorization", `Bearer ${credential}`)
  headers.set("Idempotency-Key", crypto.randomUUID())
  let response: Response
  try {
    response = await fetch(path, { method: "POST", headers })
  } catch {
    window.dispatchEvent(new Event("zhiwo:unavailable"))
    throw new ApiError(0, "UNAVAILABLE", "本地服务未运行", true)
  }
  if (!response.ok) {
    const text = await response.text()
    let body: { error?: { code?: string; message?: string; retryable?: boolean } } | null = null
    try { body = text ? JSON.parse(text) : null } catch { body = null }
    const error = body?.error
    throw new ApiError(response.status, error?.code || "REQUEST_FAILED", error?.message || "下载没有完成", Boolean(error?.retryable))
  }
  const blob = await response.blob()
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")
  const link = document.createElement("a")
  const url = URL.createObjectURL(blob)
  link.href = url
  link.download = match?.[1] || fallback
  link.click()
  URL.revokeObjectURL(url)
}

export function importMessage(job: ImportJob, demo: boolean) {
  const prefix = demo ? "演示数据。" : ""
  if (job.status === "extracted") {
    const count = job.proposals?.length || 0
    return count
      ? `${prefix}已放入待确认 ${count} 条，还没有进入正式记忆。`
      : `${prefix}提取完成，没有可审核的内容。来源已保存。`
  }
  if (job.status === "extractor_unavailable") {
    return "提取模型未配置。来源已保存，没有生成待确认内容。"
  }
  if (job.error_code === "VALIDATION_ERROR") {
    return "提取结果格式无效。来源仍在，可以重试。没有生成待确认内容。"
  }
  if (job.error_code === "TIMEOUT") {
    return "提取超时。来源仍在，可以重试。没有生成待确认内容。"
  }
  return "提取没有完成。来源仍在，可以重试。没有生成待确认内容。"
}
