import { useEffect, useMemo, useRef, useState } from "react"
import {
  ApiError,
  api,
  explain,
  getCredential,
  importMessage,
  setCredential,
  type Memory,
  type Profile,
  type Proposal,
} from "./api"
import { Detail } from "./Detail"
import { CATEGORIES, categoryLabel } from "./format"

type Page = "profile" | "memories" | "review" | "agents"
type Service = {
  up: boolean
  extractor: boolean
  testMode: boolean
  embeddings: boolean
}

const emptyProfile: Profile = {
  groups: [
    { id: "identity", title: "身份", cards: [] },
    { id: "goal", title: "目标", cards: [] },
    { id: "preference", title: "偏好", cards: [] },
    { id: "project", title: "项目", cards: [] },
  ],
  recent: [],
}

function pageFromHash(): Page {
  const name = location.hash.replace(/^#\/?/, "")
  if (name === "memories" || name === "review" || name === "agents") return name
  return "profile"
}

export function App() {
  const [authed, setAuthed] = useState(Boolean(getCredential()))
  const [page, setPage] = useState<Page>(pageFromHash)
  const [service, setService] = useState<Service>({ up: false, extractor: false, testMode: false, embeddings: true })
  const [query, setQuery] = useState("")
  const [activeQuery, setActiveQuery] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [composer, setComposer] = useState<"add" | "import" | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  useEffect(() => {
    let alive = true
    const poll = () => {
      api
        .health()
        .then(() => {
          if (!getCredential()) {
            if (alive) setService((prev) => ({ ...prev, up: true }))
            return
          }
          return api.ownerHealth().then((health) => {
            if (!alive) return
            setService({
              up: true,
              extractor: health.extractor_configured,
              testMode: health.test_mode,
              embeddings: health.embeddings_loaded,
            })
          })
        })
        .catch((err: unknown) => {
          if (!alive) return
          if (err instanceof ApiError && err.code === "UNAUTHENTICATED") {
            setService((prev) => ({ ...prev, up: true }))
            return
          }
          setService((prev) => ({ ...prev, up: false }))
        })
    }
    poll()
    const timer = window.setInterval(poll, 4000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [authed, tick])

  function openPage(next: Page) {
    location.hash = next === "profile" ? "#/" : `#/${next}`
    setPage(next)
  }

  if (!authed) {
    return (
      <Gate
        onReady={() => {
          setAuthed(true)
          setTick((value) => value + 1)
        }}
      />
    )
  }

  return (
    <div className="flex h-full min-w-[1024px] flex-col bg-paper text-ink">
      <header className="flex items-center gap-3 border-b border-line bg-white/90 px-4 py-3">
        <form
          className="flex min-w-0 flex-1 gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setActiveQuery(query.trim())
            openPage("memories")
          }}
        >
          <input
            aria-label="搜索记忆"
            className="min-w-0 flex-1 rounded-xl border border-line bg-paper px-3 py-2"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索已确认的记忆"
            value={query}
          />
          <button className="rounded-xl bg-action px-4 py-2 text-white" type="submit">
            搜索
          </button>
        </form>
        <button className="rounded-xl border border-line bg-white px-3 py-2" onClick={() => setComposer("add")} type="button">
          添加
        </button>
        <button className="rounded-xl border border-line bg-white px-3 py-2" onClick={() => setComposer("import")} type="button">
          导入
        </button>
        <p className="text-sm text-muted">{service.up ? "本地服务正在运行" : "本地服务未运行"}</p>
      </header>
      {service.testMode ? (
        <p className="bg-[#fff8e8] px-4 py-2 text-sm">演示数据。当前候选来自测试模式，不是云模型提取结果。</p>
      ) : null}
      {!service.extractor && !service.testMode ? (
        <p className="px-4 py-2 text-sm text-muted">提取模型未配置。手动添加和已有记忆仍可使用。</p>
      ) : null}
      <div className="flex min-h-0 flex-1">
        <nav className="flex w-[208px] shrink-0 flex-col border-r border-line bg-white/80 p-3">
          <p className="px-3 py-2 text-sm font-semibold">知我</p>
          {(
            [
              ["profile", "关于我"],
              ["memories", "记忆"],
              ["review", "待确认"],
              ["agents", "我的 Agent"],
            ] as [Page, string][]
          ).map(([id, label]) => (
            <button
              className={`rounded-xl px-3 py-2 text-left ${page === id ? "bg-[#e8f0ff] text-action" : "hover:bg-paper"}`}
              key={id}
              onClick={() => openPage(id)}
              type="button"
            >
              {label}
            </button>
          ))}
          <p className="mt-auto px-3 py-2 text-xs text-muted">设置、备份和连接管理在后续版本。</p>
        </nav>
        <main className="min-w-0 flex-1 overflow-auto p-6">
          {page === "profile" ? <ProfilePage onOpen={setSelected} tick={tick} /> : null}
          {page === "memories" ? <MemoryPage onOpen={setSelected} query={activeQuery} tick={tick} /> : null}
          {page === "review" ? <ReviewPage demo={service.testMode} tick={tick} /> : null}
          {page === "agents" ? <AgentPage /> : null}
        </main>
        {selected ? (
          <Detail
            memoryId={selected}
            onClose={() => setSelected(null)}
            onSaved={() => setTick((value) => value + 1)}
          />
        ) : null}
      </div>
      {composer ? (
        <Composer
          demo={service.testMode}
          kind={composer}
          onClose={() => setComposer(null)}
          onRefresh={() => setTick((value) => value + 1)}
          onSaved={() => {
            setComposer(null)
            setTick((value) => value + 1)
          }}
        />
      ) : null}
    </div>
  )
}

function Gate({ onReady }: { onReady: () => void }) {
  const [value, setValue] = useState("")
  const [error, setError] = useState("")
  return (
    <main className="grid h-full place-items-center bg-paper px-6">
      <form
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-sm"
        onSubmit={(event) => {
          event.preventDefault()
          setCredential(value.trim())
          api
            .ownerHealth()
            .then(() => onReady())
            .catch((err: unknown) => {
              setCredential("")
              setError(explain(err))
            })
        }}
      >
        <h1 className="mb-2 text-xl font-semibold">进入知我</h1>
        <p className="mb-4 text-sm text-muted">凭证只保存在这次浏览器会话里，用来调用本机服务。</p>
        <label className="mb-3 block text-sm">
          本机凭证
          <input
            className="mt-1 w-full rounded-xl border border-line px-3 py-2"
            onChange={(event) => setValue(event.target.value)}
            type="password"
            value={value}
          />
        </label>
        {error ? <p className="mb-3 text-sm text-[#9b2c2c]">{error}</p> : null}
        <button className="rounded-xl bg-action px-4 py-2 text-white" type="submit">
          进入
        </button>
      </form>
    </main>
  )
}

function ProfilePage({ onOpen, tick }: { onOpen: (id: string) => void; tick: number }) {
  const [profile, setProfile] = useState<Profile>(emptyProfile)
  const [error, setError] = useState("")
  useEffect(() => {
    api
      .profile()
      .then(setProfile)
      .catch((err: unknown) => setError(explain(err)))
  }, [tick])
  const empty = profile.groups.every((group) => group.cards.length === 0) && profile.recent.length === 0
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">关于我</h1>
      {error ? <p className="text-sm text-[#9b2c2c]">{error}</p> : null}
      {empty ? <p className="text-muted">还没有已确认的内容。可以添加一条，或导入后再审核。</p> : null}
      {profile.groups.map((group) => (
        <section key={group.id}>
          <h2 className="mb-3 text-base font-semibold">{group.title}</h2>
          {group.cards.length === 0 ? <p className="text-sm text-muted">还没有已确认的{group.title}。</p> : null}
          <div className="grid gap-3 md:grid-cols-2">
            {group.cards.map((card) => (
              <MemoryCard key={card.id} memory={card} onOpen={onOpen} />
            ))}
          </div>
        </section>
      ))}
      <section>
        <h2 className="mb-3 text-base font-semibold">近期变化</h2>
        {profile.recent.length === 0 ? <p className="text-sm text-muted">还没有已确认的近期变化。</p> : null}
        <div className="grid gap-3 md:grid-cols-2">
          {profile.recent.map((card) => (
            <MemoryCard key={card.id} memory={card} onOpen={onOpen} />
          ))}
        </div>
      </section>
    </div>
  )
}

function MemoryPage({ onOpen, query, tick }: { onOpen: (id: string) => void; query: string; tick: number }) {
  const [state, setState] = useState("current")
  const [category, setCategory] = useState("")
  const [items, setItems] = useState<Memory[]>([])
  const [error, setError] = useState("")
  useEffect(() => {
    api
      .memories({ query, state, category, limit: 50 })
      .then((result) => setItems(result.items))
      .catch((err: unknown) => setError(explain(err)))
  }, [query, state, category, tick])
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">记忆</h1>
        <select aria-label="状态" className="rounded-xl border border-line bg-white px-3 py-2" onChange={(event) => setState(event.target.value)} value={state}>
          <option value="current">当前</option>
          <option value="expired">过期</option>
          <option value="history">历史</option>
        </select>
        <select aria-label="主题" className="rounded-xl border border-line bg-white px-3 py-2" onChange={(event) => setCategory(event.target.value)} value={category}>
          <option value="">全部主题</option>
          {CATEGORIES.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {query ? <p className="mb-3 text-sm text-muted">搜索“{query}”。这里只包含已经确认的记忆。</p> : null}
      {error ? <p className="mb-3 text-sm text-[#9b2c2c]">{error}</p> : null}
      {items.length === 0 ? <p className="text-muted">没有符合条件的记忆。可以清除筛选，或添加一条。</p> : null}
      <div className="grid gap-3">
        {items.map((item) => (
          <MemoryCard key={`${item.id}-${item.revision}`} memory={item} onOpen={onOpen} />
        ))}
      </div>
    </div>
  )
}

function MemoryCard({ memory, onOpen }: { memory: Memory; onOpen: (id: string) => void }) {
  return (
    <button
      className="rounded-xl bg-white p-4 text-left shadow-sm hover:bg-[#fbfcfe]"
      onClick={() => onOpen(memory.id)}
      type="button"
    >
      <p className="whitespace-pre-wrap leading-7">{memory.content || "正文暂时无法读取。"}</p>
      <p className="mt-2 text-sm text-muted">
        {categoryLabel(memory.category)} · 版本 {memory.revision}
        {memory.scope ? ` · ${memory.scope}` : ""}
        {memory.share_enabled === false ? " · 仅自己可见" : ""}
      </p>
    </button>
  )
}

function ReviewPage({ demo, tick }: { demo: boolean; tick: number }) {
  const [items, setItems] = useState<Proposal[]>([])
  const [error, setError] = useState("")
  const [decisionError, setDecisionError] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const load = () => {
    api
      .proposals()
      .then((result) => {
        setItems(result.proposals)
        setSelected((current) => current && result.proposals.some((item) => item.id === current) ? current : result.proposals[0]?.id || null)
      })
      .catch((err: unknown) => setError(explain(err)))
  }
  useEffect(() => {
    load()
  }, [tick])
  const proposal = items.find((item) => item.id === selected) || null
  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
      <div>
        <h1 className="mb-3 text-xl font-semibold">待确认</h1>
        {error ? <p className="mb-3 text-sm text-[#9b2c2c]">{error}</p> : null}
        {decisionError ? (
          <p className="mb-3 rounded-xl bg-[#fff4f2] px-3 py-2 text-sm text-[#9b2c2c]" role="alert">
            {decisionError}
          </p>
        ) : null}
        {items.length === 0 ? <p className="text-muted">当前没有待确认的记忆。</p> : null}
        <div className="space-y-2">
          {items.map((item) => (
            <button
              className={`w-full rounded-xl px-3 py-3 text-left ${item.id === selected ? "bg-white shadow-sm" : "hover:bg-white"}`}
              key={item.id}
              onClick={() => setSelected(item.id)}
              type="button"
            >
              <span className="line-clamp-3 whitespace-pre-wrap">{item.payload.content}</span>
              {item.demo ? <span className="mt-1 block text-xs text-[#8a5a00]">演示数据</span> : null}
            </button>
          ))}
        </div>
      </div>
      {proposal ? (
        <ReviewCard
          demo={demo}
          key={proposal.id}
          onChanged={load}
          onTerminal={(message) => {
            setDecisionError(message)
            load()
          }}
          proposal={proposal}
        />
      ) : null}
    </div>
  )
}

function ReviewCard({
  proposal,
  demo,
  onChanged,
  onTerminal,
}: {
  proposal: Proposal
  demo: boolean
  onChanged: () => void
  onTerminal: (message: string) => void
}) {
  const [memories, setMemories] = useState<Memory[]>([])
  const [targetId, setTargetId] = useState(proposal.target_id || "")
  const [baseRevision, setBaseRevision] = useState<number | null>(proposal.base_revision)
  const [currentText, setCurrentText] = useState("")
  const [scope, setScope] = useState("")
  const [edited, setEdited] = useState(proposal.payload.content)
  const [onlySelf, setOnlySelf] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const attempt = useRef<{ sig: string; key: string } | null>(null)

  useEffect(() => {
    api.memories({ state: "current", limit: 50 }).then((result) => setMemories(result.items)).catch(() => setMemories([]))
  }, [])

  useEffect(() => {
    if (!targetId) return
    api
      .memory(targetId)
      .then((memory) => {
        setBaseRevision(memory.revision)
        setCurrentText(memory.content || "正文暂时无法读取。")
      })
      .catch((err: unknown) => setError(explain(err)))
  }, [targetId])

  const suggestion = proposal.payload.content
  const showDemo = demo || proposal.demo

  async function submit(decision: string, extra: Record<string, unknown>) {
    const body = { decision, ...extra }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const result = await api.decide(proposal.id, body, attempt.current.key)
      if (result.status !== "accepted" && result.status !== "rejected") {
        setError("服务没有确认审核结果，正式记忆不应视为已更新。")
        return
      }
      setNotice(result.status === "rejected" ? "已拒绝。正式记忆没有加入这条内容。" : "已保存。")
      attempt.current = null
      onChanged()
    } catch (err: unknown) {
      const message = explain(err)
      setError(message)
      if (message.includes("已经通过") || message.includes("已经拒绝") || message.includes("正在保存")) {
        onTerminal(message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-xl bg-white p-4">
      {showDemo ? <p className="mb-3 text-sm text-[#8a5a00]">演示数据。这条候选不是云模型提取的结果。</p> : null}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl bg-paper p-3">
          <h2 className="mb-2 text-sm font-semibold">当前理解</h2>
          {targetId ? (
            <p className="whitespace-pre-wrap leading-7">{currentText || "正在读取当前记忆。"}</p>
          ) : (
            <p className="text-muted">这是新增内容，还没有对应的当前记忆。</p>
          )}
        </div>
        <div className="rounded-xl bg-paper p-3">
          <h2 className="mb-2 text-sm font-semibold">建议改为</h2>
          <p className="mb-2 text-sm text-muted">{categoryLabel(proposal.payload.category)}</p>
          <p className="whitespace-pre-wrap leading-7">{suggestion}</p>
        </div>
      </div>
      <div className="mt-4 rounded-xl border border-line p-3">
        <h2 className="mb-2 text-sm font-semibold">证据</h2>
        <p className="whitespace-pre-wrap text-sm leading-6">{proposal.evidence.text || "没有证据片段。"}</p>
        <p className="mt-2 text-sm text-muted">来源类型：{proposal.source.kind === "file" ? "文件" : "粘贴"}</p>
        <SourcePreview sourceId={proposal.source.id} />
      </div>
      <label className="mt-4 block text-sm">
        更新哪一条当前记忆
        <select
          aria-label="更新哪一条当前记忆"
          className="mt-1 w-full rounded-xl border border-line px-3 py-2"
          onChange={(event) => setTargetId(event.target.value)}
          value={targetId}
        >
          <option value="">不关联现有记忆</option>
          {memories.map((memory) => (
            <option key={memory.id} value={memory.id}>
              版本 {memory.revision} · {(memory.content || "").slice(0, 32)}
            </option>
          ))}
        </select>
      </label>
      <label className="mt-3 block text-sm">
        适用场景
        <input
          aria-label="适用场景"
          className="mt-1 w-full rounded-xl border border-line px-3 py-2"
          onChange={(event) => setScope(event.target.value)}
          placeholder="两者保留时需要填写，例如正式报告"
          value={scope}
        />
      </label>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input checked={onlySelf} onChange={(event) => setOnlySelf(event.target.checked)} type="checkbox" />
        仅自己可见
      </label>
      <label className="mt-3 block text-sm">
        修改后的内容
        <textarea
          aria-label="修改后的内容"
          className="mt-1 min-h-24 w-full rounded-xl border border-line px-3 py-2"
          onChange={(event) => setEdited(event.target.value)}
          value={edited}
        />
      </label>
      {error ? (
        <p className="mt-3 rounded-xl bg-[#fff4f2] px-3 py-2 text-sm text-[#9b2c2c]" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="mt-3 text-sm text-[#17663a]">{notice}</p> : null}
      <div className="mt-4 flex flex-wrap gap-2">
        <Action busy={busy} label="通过" onClick={() => submit("accept", { share_enabled: !onlySelf })} />
        <Action
          busy={busy}
          label="更新"
          onClick={() => {
            if (!targetId || baseRevision === null) {
              setError("更新需要选择一条当前记忆。这次没有保存。")
              return
            }
            submit("update", {
              target_id: targetId,
              base_revision: baseRevision,
              content: suggestion,
              share_enabled: !onlySelf,
            })
          }}
        />
        <Action
          busy={busy}
          label="两者保留"
          onClick={() => {
            if (!scope.trim()) {
              setError("两者保留需要填写适用场景。这次没有保存。")
              return
            }
            submit("keep_both", { scope: scope.trim(), content: suggestion, share_enabled: !onlySelf })
          }}
        />
        <Action
          busy={busy}
          label="编辑后保存"
          onClick={() => {
            if (!edited.trim()) {
              setError("编辑后的内容不能为空。这次没有保存。")
              return
            }
            submit("edit", { content: edited, share_enabled: !onlySelf })
          }}
        />
        <Action busy={busy} label="拒绝" onClick={() => submit("reject", {})} />
        {error.includes("版本已经变化") ? (
          <button
            className="rounded-xl border border-line px-3 py-2"
            onClick={() => {
              if (!targetId) return
              api.memory(targetId).then((memory) => {
                setBaseRevision(memory.revision)
                setCurrentText(memory.content || "")
                setError("已重新读取当前版本。请再看一次差异，然后再决定。")
              })
            }}
            type="button"
          >
            重新查看差异
          </button>
        ) : null}
      </div>
    </section>
  )
}

function SourcePreview({ sourceId }: { sourceId: string }) {
  const [text, setText] = useState("")
  const [error, setError] = useState("")
  useEffect(() => {
    api
      .source(sourceId)
      .then((source) => setText(source.content))
      .catch((err: unknown) => setError(explain(err)))
  }, [sourceId])
  if (error) return <p className="mt-2 text-sm text-[#9b2c2c]">{error}</p>
  return <pre className="mt-2 whitespace-pre-wrap font-sans text-sm leading-6 text-muted">{text}</pre>
}

function Action({ label, busy, onClick }: { label: string; busy: boolean; onClick: () => void }) {
  return (
    <button className="rounded-xl bg-action px-3 py-2 text-white disabled:opacity-50" disabled={busy} onClick={onClick} type="button">
      {label}
    </button>
  )
}

function AgentPage() {
  const steps = useMemo(
    () => ["创建连接", "选择权限并复制配置", "在客户端发起验证请求"],
    [],
  )
  return (
    <div className="max-w-xl">
      <h1 className="mb-3 text-xl font-semibold">我的 Agent</h1>
      <p className="mb-4 text-muted">还没有连接。这里不能创建或授权 Agent。</p>
      <ol className="list-decimal space-y-2 pl-5">
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="mt-4 text-sm text-muted">正式的连接管理还没接入。完成上面三步之前，不会有 Agent 读到记忆。</p>
    </div>
  )
}

function Composer({
  kind,
  demo,
  onClose,
  onRefresh,
  onSaved,
}: {
  kind: "add" | "import"
  demo: boolean
  onClose: () => void
  onRefresh: () => void
  onSaved: () => void
}) {
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("preference")
  const [fileName, setFileName] = useState("")
  const [error, setError] = useState("")
  const [result, setResult] = useState("")
  const [busy, setBusy] = useState(false)
  const [jobId, setJobId] = useState("")
  const [onlySelf, setOnlySelf] = useState(false)
  const attempt = useRef<{ sig: string; key: string } | null>(null)

  async function saveMemory() {
    const body = {
      content,
      kind: category === "event" ? "event" : "fact",
      category,
      share_enabled: !onlySelf,
    }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    setBusy(true)
    setError("")
    try {
      const saved = (await api.createMemory(body, attempt.current.key)) as { status?: string; memory_id?: string }
      if (saved?.status !== "accepted" || !saved.memory_id) {
        setError("服务没有确认这条记忆已保存。")
        return
      }
      onSaved()
    } catch (err: unknown) {
      setError(explain(err))
    } finally {
      setBusy(false)
    }
  }

  async function saveImport(retry = false) {
    setBusy(true)
    setError("")
    try {
      const job = retry && jobId
        ? await api.retryImport(jobId)
        : await api.importSource(
            fileName
              ? { kind: "file", name: fileName, text: content }
              : { kind: "paste", text: content },
            crypto.randomUUID(),
          )
      setJobId(job.job_id)
      setResult(importMessage(job, demo))
      onRefresh()
    } catch (err: unknown) {
      setError(explain(err))
      setResult("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-[#17212f]/30 px-4">
      <form
        className="w-full max-w-lg rounded-xl bg-white p-5"
        onSubmit={(event) => {
          event.preventDefault()
          if (kind === "add") saveMemory()
          else saveImport(false)
        }}
      >
        <h2 className="mb-3 text-lg font-semibold">{kind === "add" ? "添加记忆" : "导入来源"}</h2>
        {kind === "add" ? (
          <label className="mb-3 block text-sm">
            主题
            <select
              aria-label="主题"
              className="mt-1 w-full rounded-xl border border-line px-3 py-2"
              onChange={(event) => setCategory(event.target.value)}
              value={category}
            >
              {CATEGORIES.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {kind === "add" ? (
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input checked={onlySelf} onChange={(event) => setOnlySelf(event.target.checked)} type="checkbox" />
            仅自己可见
          </label>
        ) : (
          <label className="mb-3 block text-sm">
            文本或 Markdown 文件
            <input
              accept=".txt,.md,.markdown,text/plain"
              aria-label="文本或 Markdown 文件"
              className="mt-1 block w-full"
              onChange={async (event) => {
                const file = event.target.files?.[0]
                if (!file) return
                setFileName(file.name)
                setContent(await file.text())
              }}
              type="file"
            />
          </label>
        )}
        <label className="mb-3 block text-sm">
          {kind === "add" ? "记忆内容" : "来源文本"}
          <textarea
            aria-label={kind === "add" ? "记忆内容" : "来源文本"}
            className="mt-1 min-h-32 w-full rounded-xl border border-line px-3 py-2"
            onChange={(event) => setContent(event.target.value)}
            value={content}
          />
        </label>
        {error ? (
          <p className="mb-3 text-sm text-[#9b2c2c]" role="alert">
            {error}
          </p>
        ) : null}
        {result ? <p className="mb-3 text-sm">{result}</p> : null}
        <div className="flex gap-2">
          <button className="rounded-xl bg-action px-4 py-2 text-white disabled:opacity-50" disabled={busy} type="submit">
            {kind === "add" ? "保存" : "开始导入"}
          </button>
          {kind === "import" && jobId && result.includes("可以重试") ? (
            <button className="rounded-xl border border-line px-4 py-2" disabled={busy} onClick={() => saveImport(true)} type="button">
              重试
            </button>
          ) : null}
          <button className="rounded-xl border border-line px-4 py-2" onClick={onClose} type="button">
            关闭
          </button>
        </div>
      </form>
    </div>
  )
}
