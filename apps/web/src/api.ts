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
  readable?: boolean
}

export type Version = Memory & { lifecycle: string }

export type Proposal = {
  id: string
  status: string
  decision: string | null
  change_type: string
  target_id: string | null
  base_revision: number | null
  payload: { content: string; kind: string; category: string; scope: string | null }
  evidence: { text?: string; source_id?: string }
  source: { id: string; kind: string; name: string | null }
  demo: boolean
}

export type Profile = {
  groups: { id: string; title: string; cards: Memory[] }[]
  recent: Memory[]
}

export type Source = {
  id: string
  kind: string
  name: string | null
  content: string
  imported_at: string
}

export type AgentConnection = {
  id: string
  name: string
  enabled: boolean
  policy_version: number
  allowed_tools: string[]
  allowed_categories: string[]
  client_status: string
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

export type ImportJob = {
  job_id: string
  status: string
  error_code: string | null
  proposals: { id: string; status: string; payload: { content: string } }[]
}

let credential = sessionStorage.getItem("zhiwo-owner-credential") || ""

export function setCredential(value: string) {
  credential = value
  if (value) sessionStorage.setItem("zhiwo-owner-credential", value)
  else sessionStorage.removeItem("zhiwo-owner-credential")
}

export function getCredential() {
  return credential
}

export function explain(error: unknown): string {
  if (!(error instanceof ApiError)) return "没有完成，请稍后重试。"
  if (error.status === 0 || error.code === "UNAVAILABLE") return "本地服务未运行。刚才的内容还在，可以重试。"
  if (error.code === "UNAUTHENTICATED") return "本机凭证不正确。"
  if (error.code === "MODEL_UNAVAILABLE") return "模型还没准备好，这次没有保存。"
  if (error.code === "CONFLICT" && error.message.includes("memory changed")) {
    return "当前版本已经变化。请重新查看差异，这次没有覆盖最新内容。"
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
      extractor_configured: boolean
      test_mode: boolean
    }>
  },
  profile() {
    return request("/api/v1/profile") as Promise<Profile>
  },
  memories(params: { query?: string; state?: string; category?: string; limit?: number }) {
    const search = new URLSearchParams()
    if (params.query) search.set("query", params.query)
    if (params.state) search.set("state", params.state)
    if (params.category) search.set("category", params.category)
    search.set("limit", String(params.limit || 50))
    return request(`/api/v1/memories?${search}`) as Promise<{ items: Memory[] }>
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
    }) as Promise<{ status?: string; decision?: string; revision?: number }>
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
